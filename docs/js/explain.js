// Builds human-readable justifications for each recommended journal.
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (x) => `${Math.round(x * 100)}%`;
const fmt = (x, d = 2) => (x == null ? "—" : Number(x).toLocaleString(undefined, { maximumFractionDigits: d }));

export function fitLabel(fitRel) {
  if (fitRel >= 0.85) return "Excellent";
  if (fitRel >= 0.7) return "Strong";
  if (fitRel >= 0.5) return "Good";
  return "Partial";
}

export function abdcInfo(j, meta) {
  if (!j.ab) return null;
  const [r25, r22, forCode, rec] = j.ab;
  const forName = meta.for_names?.[forCode] || "";
  return { r25, r22, forCode, forName, rec, removed: r25 === "X" };
}

function topSubjects(j, asjc, n = 3) {
  return (j.a || []).slice(0, n).map((c) => asjc[c] || c);
}

export function cautions(j) {
  const c = [];
  if (!j.s) c.push("Not found in the current Scopus Source List — check indexing status before submitting.");
  if (j.inact) c.push("Marked as inactive in the Scopus Source List (coverage may have stopped).");
  if (j.disc) c.push("Discontinued by Scopus — new articles will not be indexed.");
  if (j.ab && j.ab[0] === "X") c.push(`Proposed for removal from the ABDC list in the 2025 draft (was ${j.ab[1]} in 2022).`);
  if (j.ab && /downgrade/i.test(j.ab[3] || "")) c.push(`ABDC rating proposed to be downgraded (${j.ab[1]} → ${j.ab[0]}) in the 2025 draft.`);
  if (j.apc && j.apc >= 2500) c.push(`High article processing charge (≈ US$${fmt(j.apc, 0)}).`);
  if (j.w && j.w < 60) c.push("Small publication record — topic profile is based on few articles.");
  return c;
}

/**
 * Produces {summary, scopeHtml, metricsHtml, rankingHtml, cautions[], terms[], topicRows[]}
 */
export function explain(engine, s, ctx) {
  const { j } = s;
  const { asjc, meta } = engine;
  const T = engine.T;
  const terms = engine.sharedTerms(ctx.qv, s.hits, ctx.surface);
  const hitTopics = (s.hits || []).filter((h) => h[1] >= 0.3).slice(0, 3).map((h) => T[h[0]][0]);
  const subjects = topSubjects(j, asjc);
  const ab = abdcInfo(j, meta);

  // ---- summary sentence
  const parts = [];
  const label = fitLabel(s.fitRel);
  if (s.hits && s.matchedShare > 0) {
    parts.push(`<strong>${label} scope fit.</strong> About <strong>${pct(s.matchedShare)}</strong> of this journal's recent publications fall in research topics that match your abstract${hitTopics.length ? ` — notably <em>${hitTopics.map(esc).join("</em>, <em>")}</em>` : ""}.`);
  } else if (s.hits) {
    parts.push(`<strong>${label} scope fit.</strong> The journal publishes on topics adjacent to your abstract${hitTopics.length ? ` (e.g. <em>${esc(hitTopics[0])}</em>)` : ""}.`);
  } else {
    parts.push(`<strong>${label} scope fit</strong> inferred from its Scopus subject classification${subjects.length ? ` (${subjects.map(esc).join(", ")})` : ""}; no detailed topic profile is available.`);
  }
  const q = [];
  if (j.q) q.push(`SJR ${j.q}${j.sjr != null ? ` (SJR ${fmt(j.sjr, 2)})` : ""}`);
  if (j.cs != null) q.push(`CiteScore ${fmt(j.cs, 1)}`);
  if (j.h) q.push(`H-index ${fmt(j.h, 0)}`);
  if (!j.q && j.m2 != null) q.push(`2-yr mean citedness ${fmt(j.m2, 2)}`);
  if (ab && !ab.removed && ab.r25 !== "-") q.push(`ABDC <strong>${esc(ab.r25)}</strong>`);
  else if (!ab) q.push("not ABDC-listed");
  if (q.length) parts.push(`Quality: ${q.join(" · ")}.`);
  const summary = parts.join(" ");

  // ---- scope block
  const rows = (s.hits || j.tp?.map(([ti, c]) => [ti, 0, c / j._tot]) || []).slice(0, 6);
  const scopeTxt = [];
  if (s.hits) {
    scopeTxt.push(`Your abstract was mapped to research topics, then compared with the topics this journal actually publishes (OpenAlex profile of ${fmt(j.w, 0)} works).`);
  }
  if (subjects.length) scopeTxt.push(`Scopus subject areas: ${subjects.map(esc).join("; ")}.`);
  if (s.ts > 0.25) scopeTxt.push("The journal title itself also reflects key terms from your manuscript.");

  // ---- metrics block
  const kv = [];
  if (j.sjr != null) kv.push(["SJR", `${fmt(j.sjr, 3)}${j._sjrPct != null ? ` <span class="muted">(top ${Math.max(1, Math.round((1 - j._sjrPct) * 100))}%)</span>` : ""}`]);
  if (j.q) kv.push(["Best quartile", j.q]);
  if (j.cat?.length) kv.push(["SJR categories", j.cat.slice(0, 3).map((c) => `${esc(c[0])}${c[1] ? ` (${c[1]})` : ""}`).join("<br>")]);
  if (j.cs != null) kv.push(["CiteScore", `${fmt(j.cs, 1)}${j.csp != null ? ` <span class="muted">(${fmt(j.csp, 0)}th pct)</span>` : ""}`]);
  if (j.snip != null) kv.push(["SNIP", fmt(j.snip, 3)]);
  if (j.h) kv.push(["H-index", fmt(j.h, 0)]);
  if (j.cpd != null) kv.push(["Cites/doc (2y)", fmt(j.cpd, 2)]);
  if (j.m2 != null) kv.push(["2-yr mean citedness", `${fmt(j.m2, 2)} <span class="muted">(OpenAlex)</span>`]);
  if (j.w) kv.push(["Works indexed", fmt(j.w, 0)]);
  kv.push(["Scopus", j.s ? (j.inact ? "Indexed (inactive)" : "Indexed, active") : "Not in source list"]);
  kv.push(["Access", j.doaj ? "Open access (DOAJ)" : j.oa ? "Open access" : "Subscription / hybrid"]);
  if (j.apc) kv.push(["APC", `≈ US$${fmt(j.apc, 0)}`]);
  if (j.c) kv.push(["Country", esc(j.c)]);

  // ---- ranking block
  const rk = [];
  if (ab) {
    if (ab.removed) rk.push(`<p>Rated <b>${esc(ab.r22)}</b> in ABDC 2022 but <b>proposed for removal</b> in the 2025 draft list.</p>`);
    else {
      const change = ab.r22 && ab.r22 !== "-" && ab.r22 !== ab.r25 ? ` (${ab.r22} in 2022 → ${ab.r25} proposed)` : ab.r22 === "-" ? " (new inclusion)" : " (unchanged from 2022)";
      rk.push(`<p>ABDC rating <b>${esc(ab.r25)}</b> in the Draft 2025 list${change}.</p>`);
    }
    if (ab.forName || ab.forCode) rk.push(`<p>Field of Research: ${esc(ab.forCode)} ${esc(ab.forName)}</p>`);
    if (ab.rec && !/^none$/i.test(ab.rec)) rk.push(`<p class="muted small">ABDC note: ${esc(ab.rec)}</p>`);
  } else {
    rk.push("<p>Not on the ABDC Journal Quality List. If your institution's research incentives are ABDC-based, weigh this accordingly.</p>");
  }
  const tier = engine.tier(s.prestige ?? j._prestige);
  rk.push(`<p>Prestige index: <b>${Math.round((s.prestige ?? j._prestige) * 100)}/100</b> — classed as a <b>${tier}</b> option (combines ABDC, quartile, SJR and citedness).</p>`);

  return {
    summary,
    scopeTxt,
    rows,
    terms,
    kv,
    rankingHtml: rk.join(""),
    cautions: cautions(j),
    tier,
    ab,
  };
}

/** Plain-text version used for CSV export / AI prompt */
export function plainSummary(html) {
  const d = document.createElement("div");
  d.innerHTML = html;
  return d.textContent.replace(/\s+/g, " ").trim();
}
