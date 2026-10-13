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

export function cautions(j, meta = {}) {
  const c = [];
  const fin = !!meta.abdc_final;
  if (!j.s) c.push("Not found in the current Scopus Source List — check indexing status before submitting.");
  if (j.inact) c.push("Marked as inactive in the Scopus Source List (coverage may have stopped).");
  if (j.disc) c.push("Discontinued by Scopus — new articles will not be indexed.");
  if (j.ab && j.ab[0] === "X") c.push(fin
    ? `Not on the 2025 ABDC list — it was rated ${j.ab[1]} in 2022 and is no longer listed.`
    : `Proposed for removal from the ABDC list in the 2025 draft (was ${j.ab[1]} in 2022).`);
  if (j.ab && /downgrade/i.test(j.ab[3] || "")) c.push(fin
    ? `ABDC rating was downgraded from ${j.ab[1]} (2022) to ${j.ab[0]} (2025).`
    : `ABDC rating proposed to be downgraded (${j.ab[1]} → ${j.ab[0]}) in the 2025 draft.`);
  if (j.w && j.w < 60) c.push("Small publication record — topic profile is based on few articles.");
  return c;
}

/** Source pages where every shown value can be checked. Scopus and SCImago are keyed by the Scopus source id. */
export function verifyUrls(j) {
  const issn = (j.i || j.e || "").replace("-", "");
  return {
    scopus: j.s ? `https://www.scopus.com/sourceid/${j.s}` : "",
    scimago: j.s ? `https://www.scimagojr.com/journalsearch.php?q=${j.s}&tip=sid&clean=0`
      : issn ? `https://www.scimagojr.com/journalsearch.php?q=${issn}&tip=iss&clean=0` : "",
    abdc: j.ab ? "https://abdc.edu.au/abdc-journal-quality-list/" : "",
  };
}

function verifyHtml(j) {
  const u = verifyUrls(j);
  const a = (href, t) => (href ? `<a href="${esc(href)}" target="_blank" rel="noopener">${t} ↗</a>` : "");
  const parts = [a(u.scopus, "Scopus"), a(u.scimago, "SCImago"), a(u.abdc, "ABDC list")].filter(Boolean);
  return parts.length ? `Verify at source: ${parts.join(" · ")}` : "";
}

/** Where each shown metric comes from, with its edition. */
function sourceNote(meta, j) {
  const v = meta.vintage || {};
  const n = [];
  if (j.sjr != null || j.q || j.h || j.cpd != null) n.push(`SCImago SJR ${v.sjr || ""}`.trim());
  if (j.cs != null || j.snip != null) n.push(`Scopus Source List metrics ${v.cs || ""}`.trim());
  if (j.ab) n.push(`ABDC ${meta.abdc_final ? "2025 final list" : "2025 draft list"}`);
  n.push(`Scopus Source List ${(meta.sources?.scopus || "").replace(/^Scopus Source List \(|\)$/g, "")}`.trim());
  return `Sources: ${n.join(" · ")}.`;
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
  const vy = (k) => (meta.vintage?.[k] ? ` ${meta.vintage[k]}` : ""); // " 2025" - edition year of a metric
  const fin = !!meta.abdc_final;

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
  if (j.q) q.push(`SJR${vy("sjr")} ${j.q}${j.sjr != null ? ` (${fmt(j.sjr, 2)})` : ""}`);
  if (j.cs != null) q.push(`CiteScore${vy("cs")} ${fmt(j.cs, 1)}`);
  if (j.h) q.push(`H-index ${fmt(j.h, 0)}`);
  if (ab && !ab.removed && ab.r25 !== "-") q.push(`ABDC <strong>${esc(ab.r25)}</strong>`);
  else if (!ab) q.push("not ABDC-listed");
  if (q.length) parts.push(`Quality: ${q.join(" · ")}.`);
  const summary = parts.join(" ");

  // ---- scope block
  const rows = (s.hits || j.tp?.map(([ti, c]) => [ti, 0, c / j._tot]) || []).slice(0, 6);
  const scopeTxt = [];
  if (s.hits) {
    scopeTxt.push(`Your abstract was mapped to research topics, then compared with the topics this journal actually publishes (OpenAlex publication profile).`);
  }
  if (subjects.length) scopeTxt.push(`Scopus subject areas: ${subjects.map(esc).join("; ")}.`);
  if (s.ts > 0.25) scopeTxt.push("The journal title itself also reflects key terms from your manuscript.");

  // ---- metrics block
  const kv = [];
  if (j.sjr != null) kv.push([`SJR${vy("sjr")}`, fmt(j.sjr, 3)]);
  if (j.q) kv.push([`Best SJR quartile${vy("q")}`, j.q]);
  if (j.cat?.length) kv.push([`SJR categories${vy("q")}`, j.cat.slice(0, 3).map((c) => `${esc(c[0])}${c[1] ? ` (${c[1]})` : ""}`).join("<br>")]);
  if (j.cs != null) kv.push([`CiteScore${vy("cs")}`, `${fmt(j.cs, 1)}${j.csp != null ? ` <span class="muted">(${fmt(j.csp, 0)}th pct)</span>` : ""}`]);
  if (j.snip != null) kv.push([`SNIP${vy("snip")}`, fmt(j.snip, 3)]);
  if (j.h) kv.push([`H-index (SCImago${vy("h")})`, fmt(j.h, 0)]);
  if (j.cpd != null) kv.push([`Cites/doc (2y)${vy("cpd")}`, fmt(j.cpd, 2)]);
  kv.push(["Scopus", j.s ? (j.inact ? "Indexed (inactive)" : "Indexed, active") : "Not in source list"]);
  kv.push(["Open access", j.oa ? "Yes <span class=\"muted\">(Scopus list, Unpaywall status)</span>" : "Not flagged in Scopus list"]);
  if (j.c) kv.push(["Country", esc(j.c)]);

  // ---- ranking block
  const rk = [];
  if (ab) {
    if (ab.removed) rk.push(fin
      ? `<p>Rated <b>${esc(ab.r22)}</b> in ABDC 2022 but <b>not on the 2025 list</b> (removed in the 2025 review).</p>`
      : `<p>Rated <b>${esc(ab.r22)}</b> in ABDC 2022 but <b>proposed for removal</b> in the 2025 draft list.</p>`);
    else {
      const change = ab.r22 && ab.r22 !== "-" && ab.r22 !== ab.r25 ? ` (${ab.r22} in 2022 → ${ab.r25}${fin ? "" : " proposed"})` : ab.r22 === "-" ? " (new inclusion)" : " (unchanged from 2022)";
      rk.push(`<p>ABDC rating <b>${esc(ab.r25)}</b> in the ${fin ? "2025 ABDC list" : "Draft 2025 list"}${change}.</p>`);
    }
    if (ab.forName || ab.forCode) rk.push(`<p>Field of Research: ${esc(ab.forCode)} ${esc(ab.forName)}</p>`);
    if (!fin && ab.rec && !/^none$/i.test(ab.rec)) rk.push(`<p class="muted small">ABDC note: ${esc(ab.rec)}</p>`);
  } else {
    rk.push("<p>Not on the ABDC Journal Quality List. If your institution's research incentives are ABDC-based, weigh this accordingly.</p>");
  }
  const tier = engine.tier(s.prestige ?? j._prestige);
  rk.push(`<p>Prestige index: <b>${Math.round((s.prestige ?? j._prestige) * 100)}/100</b> — classed as a <b>${tier}</b> option (app-computed from the ABDC rating, SJR quartile and SJR rank — not a published metric).</p>`);

  return {
    summary,
    scopeTxt,
    rows,
    terms,
    kv,
    rankingHtml: rk.join(""),
    verify: verifyHtml(j),
    sourceNote: sourceNote(meta, j),
    cautions: cautions(j, meta),
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
