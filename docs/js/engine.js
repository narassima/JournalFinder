// Matching engine: abstract -> research topics -> journals, plus prestige scoring.
import { tokens, termFreq, surfaceMap } from "./textproc.js";

const ABDC_SCORE = { "A*": 1, A: 0.8, B: 0.55, C: 0.3 };
const Q_SCORE = { Q1: 1, Q2: 0.7, Q3: 0.45, Q4: 0.2 };

export class Engine {
  constructor({ journals, topics, asjc, meta }) {
    this.J = journals;
    this.T = topics; // [name, keywords[], description, subfield, field, subfieldId]
    this.asjc = asjc;
    this.meta = meta;
    this.#indexTopics();
    this.#indexJournals();
  }

  // ---------------------------------------------------------- indexing
  #indexTopics() {
    const N = this.T.length;
    const docs = this.T.map((t) => {
      const tf = new Map();
      termFreq(tokens(t[0]), 3, tf);
      termFreq(tokens(t[1].join(" ; ")), 2, tf);
      termFreq(tokens(t[3]), 1, tf);
      termFreq(tokens(t[2]), 1, tf);
      return tf;
    });
    // "label" tokens (name + keywords) for explaining shared terms
    this.topicLabelTokens = this.T.map((t) => new Set(tokens(t[0] + " ; " + t[1].join(" ; "))));
    const df = new Map();
    for (const d of docs) for (const k of d.keys()) df.set(k, (df.get(k) || 0) + 1);
    this.idf = new Map();
    for (const [k, v] of df) this.idf.set(k, Math.log(1 + N / v));
    this.inv = new Map(); // token -> [[topicIdx, weight], ...]
    docs.forEach((tf, ti) => {
      let norm = 0;
      const w = [];
      for (const [k, f] of tf) {
        const x = (1 + Math.log(f)) * this.idf.get(k);
        w.push([k, x]);
        norm += x * x;
      }
      norm = Math.sqrt(norm) || 1;
      for (const [k, x] of w) {
        let l = this.inv.get(k);
        if (!l) this.inv.set(k, (l = []));
        l.push([ti, x / norm]);
      }
    });
    this.topicsBySubfield = new Map();
    this.T.forEach((t, i) => {
      const sf = t[5];
      if (!sf) return;
      if (!this.topicsBySubfield.has(sf)) this.topicsBySubfield.set(sf, []);
      this.topicsBySubfield.get(sf).push(i);
    });
  }

  #indexJournals() {
    const sjrs = [];
    for (const j of this.J) {
      if (j.tp) {
        j._tot = j.tp.reduce((s, x) => s + x[1], 0);
      }
      j._tt = tokens(j.t);
      if (j.sjr != null) sjrs.push(j.sjr);
      j._key = (j.t + " " + (j.i || "") + " " + (j.e || "")).toLowerCase();
    }
    sjrs.sort((a, b) => a - b);
    const pct = (arr, v) => {
      let lo = 0, hi = arr.length;
      while (lo < hi) { const mid = (lo + hi) >> 1; if (arr[mid] < v) lo = mid + 1; else hi = mid; }
      return arr.length ? lo / arr.length : 0;
    };
    for (const j of this.J) {
      if (j.sjr != null) j._sjrPct = pct(sjrs, j.sjr);
      j._prestige = this.prestige(j);
    }
  }

  // ---------------------------------------------------------- scoring pieces
  prestige(j) {
    let s = 0, w = 0;
    const add = (v, wt) => { if (v != null && !Number.isNaN(v)) { s += v * wt; w += wt; } };
    const ab = j.ab && ABDC_SCORE[j.ab[0]];
    add(ab, 0.35);
    add(Q_SCORE[j.q], 0.25);
    add(j._sjrPct, 0.2);
    add(j.csp != null ? j.csp / 100 : null, 0.2); // only present when the Scopus list supplied CiteScore
    return w ? s / w : 0.2;
  }

  queryVector(title, abstract, keywords) {
    const tf = new Map();
    termFreq(tokens(title), 2, tf);
    termFreq(tokens(keywords), 2, tf);
    termFreq(tokens(abstract), 1, tf);
    const v = new Map();
    let norm = 0;
    for (const [k, f] of tf) {
      const idf = this.idf.get(k);
      if (!idf) continue;
      const x = (1 + Math.log(f)) * idf;
      v.set(k, x);
      norm += x * x;
    }
    norm = Math.sqrt(norm) || 1;
    for (const [k, x] of v) v.set(k, x / norm);
    return v;
  }

  topicScores(qv) {
    const acc = new Float32Array(this.T.length);
    for (const [k, qx] of qv) {
      const l = this.inv.get(k);
      if (l) for (const [ti, w] of l) acc[ti] += qx * w;
    }
    const ranked = [];
    for (let i = 0; i < acc.length; i++) if (acc[i] > 0) ranked.push([i, acc[i]]);
    ranked.sort((a, b) => b[1] - a[1]);
    const top = ranked.slice(0, 60);
    const max = top.length ? top[0][1] : 1;
    const rel = new Map();
    for (const [i, s] of top) {
      const r = Math.pow(s / max, 1.3);
      if (r >= 0.12) rel.set(i, r);
    }
    return { rel, top: top.slice(0, 12).map(([i, s]) => ({ i, s, r: rel.get(i) || 0 })) };
  }

  titleSim(qv, j) {
    if (!j._tt.length) return 0;
    const tf = termFreq(j._tt, 1);
    let dot = 0, norm = 0;
    for (const [k, f] of tf) {
      const idf = this.idf.get(k) || 0;
      const x = (1 + Math.log(f)) * idf;
      norm += x * x;
      const q = qv.get(k);
      if (q) dot += q * x;
    }
    return norm ? dot / Math.sqrt(norm) : 0;
  }

  // ---------------------------------------------------------- main match
  match({ title = "", abstract = "", keywords = "" }, filters = {}, weight = 0.2) {
    const qv = this.queryVector(title, abstract, keywords);
    if (!qv.size) return { results: [], topics: [], qv };
    const { rel, top } = this.topicScores(qv);

    // subfield relevance (for journals without a topic profile)
    const sfRel = new Map();
    for (const [ti, r] of rel) {
      const sf = this.T[ti][5];
      if (sf) sfRel.set(sf, Math.max(sfRel.get(sf) || 0, r));
    }

    const scored = [];
    for (const j of this.J) {
      if (!this.passes(j, filters)) continue;
      let fit = 0, coverage = 0, matchedShare = 0, hits = null;
      if (j.tp && j._tot) {
        let peak = 0;
        for (const [ti, c] of j.tp) {
          const r = rel.get(ti);
          if (!r) continue;
          const share = c / j._tot;
          coverage += r * share;
          peak = Math.max(peak, r * Math.min(1, share / 0.08));
          if (r >= 0.3) matchedShare += share;
          (hits ||= []).push([ti, r, share]);
        }
        fit = 0.65 * coverage + 0.35 * peak;
        if (j._tot < 30) fit *= 0.85; // thin publication record -> less reliable profile
      } else if (j.a && j.a.length) {
        const vals = j.a.map((c) => sfRel.get(c) || 0).sort((a, b) => b - a);
        fit = 0.35 * (vals[0] || 0) * (vals.length > 1 ? 0.7 + 0.3 * vals[1] : 1);
      }
      const ts = this.titleSim(qv, j);
      fit += 0.12 * ts;
      if (fit <= 0.01) continue;
      scored.push({ j, fit, coverage, matchedShare, hits, ts });
    }
    if (!scored.length) return { results: [], topics: top, qv };
    const maxFit = Math.max(...scored.map((s) => s.fit));
    for (const s of scored) {
      s.fitRel = s.fit / maxFit;
      s.prestige = s.j._prestige;
      s.final = (1 - weight) * s.fitRel + weight * s.prestige;
    }
    const results = scored
      .filter((s) => s.fitRel >= 0.22)
      .sort((a, b) => b.final - a.final)
      .slice(0, 200);
    results.forEach((s) => { if (s.hits) s.hits.sort((a, b) => b[1] * b[2] - a[1] * a[2]); });
    return { results, topics: top, qv, surface: surfaceMap([title, keywords, abstract].join(" . ")) };
  }

  passes(j, f) {
    if (f.scopusOnly && !j.s) return false;
    if (f.oa && !j.oa) return false;
    if (f.abdc && f.abdc.size) {
      const r = j.ab && j.ab[0] !== "X" && j.ab[0] !== "-" ? j.ab[0] : "none";
      if (!f.abdc.has(r)) return false;
    }
    if (f.quartile && f.quartile.size && !f.quartile.has(j.q)) return false;
    if (f.area) {
      if (!j.a || !j.a.some((c) => Math.floor(c / 100) === f.area)) return false;
    }
    return true;
  }

  tier(prestige) {
    if (prestige >= 0.74) return "reach";
    if (prestige >= 0.45) return "target";
    return "safe";
  }

  /** Submission ladder from the top results */
  ladder(results) {
    const pool = results.slice(0, 40);
    const pick = (t, n) => pool.filter((s) => this.tier(s.prestige) === t).slice(0, n);
    return { reach: pick("reach", 3), target: pick("target", 3), safe: pick("safe", 3) };
  }

  /** Shared terms between the query and the journal's matched topics */
  sharedTerms(qv, hits, surface, max = 8) {
    if (!hits) return [];
    const labelToks = new Set();
    for (const [ti, r] of hits.slice(0, 6)) if (r >= 0.2) for (const t of this.topicLabelTokens[ti]) labelToks.add(t);
    const out = [];
    const seen = new Set();
    const qs = [...qv.entries()].sort((a, b) => b[1] - a[1]);
    for (const [k] of qs) {
      if (!labelToks.has(k)) continue;
      const word = surface?.get(k);
      if (!word || seen.has(word)) continue;
      // skip unigrams already covered by a chosen bigram
      if (!k.includes("_") && out.some((w) => w.split(" ").includes(word))) continue;
      seen.add(word);
      out.push(word);
      if (out.length >= max) break;
    }
    return out;
  }

  search(q, limit = 30) {
    q = q.trim().toLowerCase();
    if (q.length < 2) return [];
    const issn = q.replace(/[^0-9x]/g, "");
    const parts = q.split(/\s+/).filter(Boolean);
    const res = [];
    for (const j of this.J) {
      let score = 0;
      if (issn.length >= 7 && ((j.i || "").replace("-", "").toLowerCase().startsWith(issn) || (j.e || "").replace("-", "").toLowerCase().startsWith(issn))) score = 100;
      else if (parts.every((p) => j._key.includes(p))) {
        const t = j.t.toLowerCase();
        score = t === q ? 90 : t.startsWith(q) ? 70 : 40;
        score += j._prestige * 10;
      }
      if (score) res.push([score, j]);
    }
    return res.sort((a, b) => b[0] - a[0]).slice(0, limit).map((x) => x[1]);
  }
}
