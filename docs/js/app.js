import { Engine } from "./engine.js";
import { explain, esc, fitLabel, cautions, plainSummary, verifyUrls } from "./explain.js";
import { aiSettings, aiReview, DEFAULT_MODEL } from "./ai.js";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const PAGE = 20;

const AREAS = {
  10: "Multidisciplinary", 11: "Agricultural & Biological Sciences", 12: "Arts & Humanities",
  13: "Biochemistry, Genetics & Molecular Biology", 14: "Business, Management & Accounting", 15: "Chemical Engineering",
  16: "Chemistry", 17: "Computer Science", 18: "Decision Sciences", 19: "Earth & Planetary Sciences",
  20: "Economics, Econometrics & Finance", 21: "Energy", 22: "Engineering", 23: "Environmental Science",
  24: "Immunology & Microbiology", 25: "Materials Science", 26: "Mathematics", 27: "Medicine", 28: "Neuroscience",
  29: "Nursing", 30: "Pharmacology, Toxicology & Pharmaceutics", 31: "Physics & Astronomy", 32: "Psychology",
  33: "Social Sciences", 34: "Veterinary", 35: "Dentistry", 36: "Health Professions",
};

const SAMPLE = {
  title: "Digital capabilities and supply chain resilience: Evidence from manufacturing SMEs in an emerging economy",
  keywords: "supply chain resilience, digital transformation, dynamic capabilities, SMEs, structural equation modelling",
  abstract: `Disruptions such as pandemics, geopolitical conflicts and climate events have exposed the fragility of supply chains, particularly for small and medium-sized enterprises (SMEs) with limited slack resources. Drawing on the dynamic capabilities view, this study examines how digital capabilities — data analytics, supply chain visibility technologies and digital platforms — enhance supply chain resilience and, in turn, operational and financial performance. We further test whether supplier collaboration and environmental dynamism moderate these relationships. Survey data from 412 manufacturing SMEs in India were analysed using partial least squares structural equation modelling (PLS-SEM). The results show that digital capabilities significantly strengthen the readiness, response and recovery dimensions of resilience, and that resilience fully mediates the effect of digital capabilities on firm performance. Supplier collaboration amplifies the effect of visibility technologies, while the effect of analytics is stronger in highly dynamic environments. The study extends the dynamic capabilities literature to the resilience context of resource-constrained firms and offers managers and policymakers guidance on prioritising digital investments to build resilient supply chains.`,
};

const state = {
  engine: null,
  last: null, // { input, out, shown, ai }
  shortlist: new Map(JSON.parse(localStorage.getItem("js.shortlist") || "[]")),
};

// ------------------------------------------------------------ boot
init().catch((e) => {
  console.error(e);
  const st = $("#data-status");
  st.className = "data-status error";
  st.textContent = `Could not load journal data (${e.message}). If you opened index.html directly, serve the folder over HTTP (see README).`;
});

async function init() {
  wireUI();
  // no-cache = always revalidate with the server, so a stale browser copy of the data can never be shown
  const get = (f) => fetch(`data/${f}`, { cache: "no-cache" }).then((r) => { if (!r.ok) throw new Error(`${f}: ${r.status}`); return r.json(); });
  const [journals, topics, asjc, meta] = await Promise.all([get("journals.json"), get("topics.json"), get("asjc.json"), get("meta.json")]);
  const t0 = performance.now();
  state.engine = new Engine({ journals, topics, asjc, meta });
  console.info(`Engine ready in ${Math.round(performance.now() - t0)} ms`);

  const st = $("#data-status");
  st.className = "data-status ready";
  st.innerHTML = `● ${meta.counts.journals.toLocaleString()} journals · ${meta.counts.abdc.toLocaleString()} ABDC-rated · ${meta.counts.sjr ? meta.counts.sjr.toLocaleString() + " with SJR" : "SJR not loaded"} · data built ${meta.built.replace("T", " ")}${meta.vintage?.sjr ? ` · SJR ${meta.vintage.sjr}` : ""}`;
  $("#stat-journals").textContent = meta.counts.scopus.toLocaleString();
  $("#footer-sources").innerHTML = "Data: " + Object.values(meta.sources).filter((s) => !/not loaded/.test(s)).map(esc).join(" · ");
  $("#btn-match").disabled = false;

  const used = new Set(journals.flatMap((j) => (j.a || []).map((c) => Math.floor(c / 100))));
  $("#f-area").insertAdjacentHTML("beforeend", Object.entries(AREAS).filter(([k]) => used.has(+k)).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join(""));
  updateShortlistCount();
}

// ------------------------------------------------------------ UI wiring
function wireUI() {
  window.addEventListener("resize", moveTabIndicator);
  requestAnimationFrame(moveTabIndicator);
  document.fonts?.ready.then(moveTabIndicator);
  $$(".tab").forEach((t) => t.addEventListener("click", () => showView(t.dataset.view)));
  $("#brand-home").addEventListener("click", (e) => { e.preventDefault(); showView("match"); });
  const abs = $("#in-abstract");
  abs.addEventListener("input", () => { $("#word-count").textContent = `${(abs.value.match(/\S+/g) || []).length} words`; });
  $("#match-form").addEventListener("submit", (e) => { e.preventDefault(); runMatch(); });
  $("#btn-sample").addEventListener("click", () => {
    $("#in-title").value = SAMPLE.title; $("#in-keywords").value = SAMPLE.keywords; abs.value = SAMPLE.abstract;
    abs.dispatchEvent(new Event("input"));
    if (state.engine) runMatch();
  });
  $("#btn-clear").addEventListener("click", () => {
    ["#in-title", "#in-keywords", "#in-abstract"].forEach((s) => ($(s).value = ""));
    abs.dispatchEvent(new Event("input"));
    state.last = null;
    $("#results").innerHTML = "";
    $("#results").append(emptyStateNode);
  });
  let deb;
  $("#filters").addEventListener("input", () => { clearTimeout(deb); deb = setTimeout(() => state.last && runMatch(true), 250); });

  let ldeb;
  $("#lookup-q").addEventListener("input", (e) => { clearTimeout(ldeb); ldeb = setTimeout(() => runLookup(e.target.value), 200); });

  $("#btn-export").addEventListener("click", exportCSV);
  $("#btn-print").addEventListener("click", () => {
    const v = $("#view-shortlist"); v.classList.add("printing");
    $$("#shortlist-results details").forEach((d) => (d.open = true));
    window.print(); v.classList.remove("printing");
  });
  $("#btn-clear-shortlist").addEventListener("click", () => { if (confirm("Clear the shortlist?")) { state.shortlist.clear(); saveShortlist(); renderShortlist(); } });

  // settings
  const dlg = $("#settings-modal");
  $("#btn-settings").addEventListener("click", () => { $("#set-key").value = aiSettings.key; $("#set-model").value = aiSettings.model; dlg.showModal(); });
  $("#settings-form").addEventListener("submit", (e) => {
    if (e.submitter?.value === "save") {
      aiSettings.key = $("#set-key").value.trim();
      aiSettings.model = $("#set-model").value.trim() || DEFAULT_MODEL;
      toast(aiSettings.enabled ? "AI justifications enabled" : "AI key removed");
      if (state.last) renderResults();
    }
  });
  $("#btn-forget-key").addEventListener("click", () => { aiSettings.key = ""; $("#set-key").value = ""; toast("Key forgotten"); });

  // delegated card actions
  document.addEventListener("click", (e) => {
    const star = e.target.closest(".star");
    if (star) toggleStar(star);
    const more = e.target.closest("#btn-more");
    if (more) { state.last.shown += PAGE; renderResults(); }
    const ai = e.target.closest("#btn-ai");
    if (ai) runAI(ai);
  });
}
const emptyStateNode = document.getElementById("empty-state");

// Slides the underline under the active tab (CSS transitions the two custom properties).
function moveTabIndicator() {
  const tabs = $(".tabs"), a = $(".tab.active", tabs);
  if (!tabs || !a) return;
  tabs.style.setProperty("--ind-x", `${a.offsetLeft}px`);
  tabs.style.setProperty("--ind-w", `${a.offsetWidth}px`);
}

function showView(v) {
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === v));
  moveTabIndicator();
  $$(".view").forEach((s) => s.classList.toggle("active", s.id === `view-${v}`));
  if (v === "shortlist") renderShortlist();
  if (v === "lookup") setTimeout(() => $("#lookup-q").focus(), 50);
}

function readFilters() {
  const set = (id) => new Set($$(`#${id} input:checked`).map((i) => i.value));
  return {
    abdc: set("f-abdc"),
    quartile: set("f-quartile"),
    area: +$("#f-area").value || 0,
    scopusOnly: $("#f-scopus").checked,
    oa: $("#f-oa").checked,
  };
}

// ------------------------------------------------------------ matching
function runMatch(keepAI = false) {
  const input = { title: $("#in-title").value.trim(), abstract: $("#in-abstract").value.trim(), keywords: $("#in-keywords").value.trim() };
  if ((input.abstract.match(/\S+/g) || []).length < 25) { toast("Please paste an abstract of at least ~25 words."); return; }
  const weight = +$("#in-weight").value / 100;
  const t0 = performance.now();
  const out = state.engine.match(input, readFilters(), weight);
  const ms = Math.round(performance.now() - t0);
  const sameInput = state.last && JSON.stringify(state.last.input) === JSON.stringify(input);
  state.last = { input, out, shown: PAGE, ms, ai: keepAI && sameInput ? state.last.ai : null };
  renderResults();
  if (!keepAI) $("#results").scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderResults() {
  const { out, shown, ms, ai } = state.last;
  const E = state.engine;
  const box = $("#results");
  if (!out.results.length) {
    box.innerHTML = `<div class="empty-state"><h2>No matching journals</h2><p class="muted">Try relaxing the filters or adding more detail to the abstract.</p></div>`;
    return;
  }
  const maxR = out.topics[0]?.r || 1;
  const topicChips = out.topics.slice(0, 8).map((t) => {
    const T = E.T[t.i];
    return `<span class="topic-chip" title="${esc(T[2])}">${esc(T[0])} <i>${esc(T[3])}</i><span class="bar"><span style="width:${Math.round((t.r / maxR) * 100)}%"></span></span></span>`;
  }).join("");
  const lad = E.ladder(out.results);
  const ladCol = (k, title, hint) => `<div class="ladder-col ${k}"><h4>${title}</h4><ul>${lad[k].length ? lad[k].map((s) => `<li><a href="#card-${idOf(s.j)}">${esc(s.j.t)}</a> <span class="muted small">${s.j.ab && s.j.ab[0] !== "X" ? "ABDC " + s.j.ab[0] : ""}${s.j.q ? " · " + s.j.q : ""}</span></li>`).join("") : `<li class="muted">${hint}</li>`}</ul></div>`;

  const head = `
    <div class="results-head glass">
      <div class="row">
        <div><h2>${out.results.length} suitable journal${out.results.length > 1 ? "s" : ""}</h2>
          <span class="muted small">Ranked by scope fit${+$("#in-weight").value ? " and prestige" : ""} · computed in ${ms} ms</span></div>
        ${aiSettings.enabled ? `<button class="btn small ai" id="btn-ai">✦ AI review of top 10</button>` : `<button class="btn small ghost" onclick="document.getElementById('btn-settings').click()">✦ Enable AI justifications</button>`}
      </div>
      <div class="muted small" style="margin-top:12px">Your abstract maps most strongly to these research topics:</div>
      <div class="topic-chips">${topicChips}</div>
      <div class="ladder">
        ${ladCol("reach", "Reach — top-tier", "No top-tier journal in the best matches")}
        ${ladCol("target", "Target — strong fit, solid standing", "—")}
        ${ladCol("safe", "Safe — good fit, more accessible", "—")}
      </div>
      ${ai?.overall ? `<div class="ai-summary"><h4>✦ AI assessment (${esc(aiSettings.model)})</h4>${esc(ai.overall)}</div>` : ""}
    </div>`;
  const ctx = { qv: out.qv, surface: out.surface };
  const cards = out.results.slice(0, shown).map((s, i) => cardHTML(s, i + 1, ctx, ai?.notes?.[i])).join("");
  const more = shown < out.results.length ? `<div class="more"><button class="btn ghost" id="btn-more">Show more (${out.results.length - shown} remaining)</button></div>` : "";
  box.innerHTML = head + `<div class="cards">${cards}</div>` + more;
}

// ------------------------------------------------------------ card rendering
const idOf = (j) => (j.s || j.i || j.e || j.t).replace(/[^A-Za-z0-9]/g, "");

function badgesHTML(j, tier) {
  const b = [];
  const M = state.engine.meta;
  const fin = !!M.abdc_final;
  const vy = (k) => (M.vintage?.[k] ? ` ${M.vintage[k]}` : ""); // edition year of a metric, e.g. " 2025"
  if (tier) b.push(`<span class="badge tier tier-${tier}">${tier}</span>`);
  if (j.ab) {
    const r = j.ab[0];
    if (r === "X") b.push(`<span class="badge abdc-X" title="${fin ? "Rated " + esc(j.ab[1]) + " in 2022; not on the 2025 ABDC list" : "Proposed for removal in Draft 2025"}">ABDC ${esc(j.ab[1])} → ${fin ? "removed" : "removal"}</span>`);
    else if (r !== "-") b.push(`<span class="badge abdc-${r === "A*" ? "As" : r}" title="${fin ? "ABDC 2025 rating (final list)" : "ABDC Draft 2025 rating"}">ABDC <b>${r}</b>${j.ab[1] && j.ab[1] !== r && j.ab[1] !== "-" ? ` <span class="muted">(was ${j.ab[1]})</span>` : ""}</span>`);
  }
  if (j.q) b.push(`<span class="badge ${j.q.toLowerCase()}" title="SCImago Journal Rank${vy("sjr")}: best quartile and SJR value">SJR${vy("sjr")} <b>${j.q}</b>${j.sjr != null ? ` · ${j.sjr.toFixed(2)}` : ""}</span>`);
  if (j.cs != null) b.push(`<span class="badge" title="Scopus CiteScore${vy("cs")}">CiteScore${vy("cs")} <b>${j.cs}</b></span>`);
  if (j.h) b.push(`<span class="badge" title="h-index (SCImago${vy("h")})">h <b>${j.h}</b></span>`);
  if (j.s) b.push(`<span class="badge scopus" title="Listed in the Scopus Source List">Scopus${j.inact ? " (inactive)" : ""}</span>`);
  else b.push(`<span class="badge warn">Not in Scopus</span>`);
  if (j.oa) b.push(`<span class="badge oa" title="Open access status in the Scopus Source List">Open access</span>`);
  return b.join("");
}

function linksHTML(j) {
  const l = [];
  if (j.url) l.push(`<a href="${esc(j.url)}" target="_blank" rel="noopener">Journal site ↗</a>`);
  const u = verifyUrls(j);
  if (u.scopus) l.push(`<a href="${u.scopus}" target="_blank" rel="noopener">Scopus ↗</a>`);
  if (u.scimago) l.push(`<a href="${u.scimago}" target="_blank" rel="noopener">SCImago ↗</a>`);
  if (j.ox) l.push(`<a href="https://openalex.org/S${j.ox}" target="_blank" rel="noopener">OpenAlex ↗</a>`);
  return l.join("");
}

function starHTML(j) {
  const on = state.shortlist.has(idOf(j));
  return `<button class="star ${on ? "on" : ""}" data-id="${idOf(j)}"><svg viewBox="0 0 24 24"><path d="m12 2 3.1 6.3 6.9 1-5 4.9 1.2 6.8L12 17.8 5.8 21l1.2-6.8-5-4.9 6.9-1Z"/></svg>${on ? "Shortlisted" : "Shortlist"}</button>`;
}

function topicBars(rows) {
  if (!rows.length) return '<p class="muted">No topic profile available for this journal.</p>';
  const E = state.engine;
  const max = Math.max(...rows.map((r) => r[2]));
  return `<div class="topic-bars">${rows.map(([ti, r, share]) => `
    <div class="tb" title="${esc(E.T[ti][2])}"><span class="name ${r >= 0.3 ? "hit" : ""}">${esc(E.T[ti][0])}</span>
    <span class="track"><span style="width:${Math.round((share / max) * 100)}%"></span></span><span class="pct">${Math.round(share * 100)}%</span></div>`).join("")}</div>`;
}

function cardHTML(s, rank, ctx, aiNote) {
  const E = state.engine;
  const j = s.j;
  const x = explain(E, s, ctx);
  const fit = Math.round(s.fitRel * 100);
  return `
  <article class="card glass" id="card-${idOf(j)}" style="animation-delay:${Math.min(rank, 10) * 40}ms">
    <div class="card-top">
      <div class="rank">${rank}</div>
      <div>
        <h3>${j.url ? `<a href="${esc(j.url)}" target="_blank" rel="noopener">${esc(j.t)}</a>` : esc(j.t)}</h3>
        <div class="pub">${esc(j.p)}${j.i ? ` · ISSN ${j.i}` : ""}${j.e ? ` · eISSN ${j.e}` : ""}</div>
        <div class="badges">${badgesHTML(j, x.tier)}</div>
      </div>
      <div class="score" title="Scope fit relative to the best-matching journal for your abstract">
        <div class="ring-score" style="--p:${fit}"><span>${fit}</span></div>
        <small>${fitLabel(s.fitRel)} fit</small>
      </div>
    </div>
    <div class="why">
      <div class="why-summary">${x.summary}</div>
      ${aiNote ? `<div class="ai-note"><b>✦ ${esc(aiNote.verdict || "AI view")}:</b> ${esc(aiNote.justification || "")}</div>` : ""}
      <details>
        <summary>Full justification</summary>
        <div class="why-grid">
          <div class="why-block">
            <h5>Scope alignment</h5>
            ${x.scopeTxt.map((t) => `<p>${t}</p>`).join("")}
            ${topicBars(x.rows)}
            ${x.terms.length ? `<p style="margin-top:10px">Shared themes with your abstract:</p><div class="terms">${x.terms.map((t) => `<span class="term">${esc(t)}</span>`).join("")}</div>` : ""}
          </div>
          <div class="why-block">
            <h5>Journal metrics</h5>
            <dl class="kv">${x.kv.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>
            <p class="muted small verify">${x.sourceNote}<br>${x.verify}</p>
          </div>
          <div class="why-block ${x.cautions.length ? "" : "full"}">
            <h5>Ranking status</h5>
            ${x.rankingHtml}
          </div>
          ${x.cautions.length ? `<div class="why-block"><h5>Cautions</h5><ul class="cautions">${x.cautions.map((c) => `<li>${esc(c)}</li>`).join("")}</ul></div>` : ""}
        </div>
      </details>
    </div>
    <div class="card-actions">${starHTML(j)}<div class="link-row">${linksHTML(j)}</div></div>
  </article>`;
}

/** Profile card (lookup / shortlist) — no abstract context */
function profileCardHTML(j, rank, saved) {
  const E = state.engine;
  const rows = (j.tp || []).slice(0, 8).map(([ti, c]) => [ti, 0, c / j._tot]);
  const pseudo = { j, fitRel: 1, prestige: j._prestige, hits: null, ts: 0, matchedShare: 0 };
  const x = explain(E, pseudo, { qv: new Map(), surface: new Map() });
  const subjects = (j.a || []).slice(0, 4).map((c) => E.asjc[c] || c);
  return `
  <article class="card glass" id="card-${idOf(j)}">
    <div class="card-top">
      <div class="rank">${rank}</div>
      <div>
        <h3>${j.url ? `<a href="${esc(j.url)}" target="_blank" rel="noopener">${esc(j.t)}</a>` : esc(j.t)}</h3>
        <div class="pub">${esc(j.p)}${j.i ? ` · ISSN ${j.i}` : ""}${j.e ? ` · eISSN ${j.e}` : ""}</div>
        <div class="badges">${badgesHTML(j, x.tier)}</div>
      </div>
      <div class="score" title="Prestige index"><div class="ring-score" style="--p:${Math.round(j._prestige * 100)}"><span>${Math.round(j._prestige * 100)}</span></div><small>Prestige</small></div>
    </div>
    <div class="why">
      ${saved?.summary ? `<div class="why-summary"><span class="muted small">Saved justification${saved.title ? ` for “${esc(saved.title)}”` : ""}:</span><br>${saved.summary}</div>` : `<div class="why-summary">${subjects.length ? `Scopus subject areas: <strong>${subjects.map(esc).join("; ")}</strong>.` : ""}</div>`}
      <details ${saved ? "" : "open"}>
        <summary>Scope profile, metrics &amp; ranking</summary>
        <div class="why-grid">
          <div class="why-block"><h5>What it publishes (OpenAlex topics)</h5>${topicBars(rows)}</div>
          <div class="why-block"><h5>Journal metrics</h5><dl class="kv">${x.kv.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl><p class="muted small verify">${x.sourceNote}<br>${x.verify}</p></div>
          <div class="why-block ${x.cautions.length ? "" : "full"}"><h5>Ranking status</h5>${x.rankingHtml}</div>
          ${x.cautions.length ? `<div class="why-block"><h5>Cautions</h5><ul class="cautions">${x.cautions.map((c) => `<li>${esc(c)}</li>`).join("")}</ul></div>` : ""}
        </div>
      </details>
    </div>
    <div class="card-actions">${starHTML(j)}<div class="link-row">${linksHTML(j)}</div></div>
  </article>`;
}

// ------------------------------------------------------------ lookup
function runLookup(q) {
  const box = $("#lookup-results");
  if (!state.engine) return;
  if (q.trim().length < 2) { box.innerHTML = ""; return; }
  const hits = state.engine.search(q);
  box.innerHTML = hits.length ? hits.map((j, i) => profileCardHTML(j, i + 1)).join("") : `<div class="no-results">No journal found for “${esc(q)}”.</div>`;
}

// ------------------------------------------------------------ shortlist
function findById(id) { return state.engine.J.find((j) => idOf(j) === id); }

function toggleStar(btn) {
  const id = btn.dataset.id;
  if (state.shortlist.has(id)) state.shortlist.delete(id);
  else {
    const card = btn.closest(".card");
    const summary = card.querySelector(".why-summary")?.innerHTML || "";
    const aiNote = card.querySelector(".ai-note")?.innerHTML || "";
    state.shortlist.set(id, { summary: summary + (aiNote ? `<div class="ai-note">${aiNote}</div>` : ""), title: state.last?.input.title || "", at: Date.now() });
  }
  saveShortlist();
  $$(`.star[data-id="${id}"]`).forEach((b) => {
    const on = state.shortlist.has(id);
    b.classList.toggle("on", on);
    b.lastChild.textContent = on ? "Shortlisted" : "Shortlist";
  });
  toast(state.shortlist.has(id) ? "Added to shortlist" : "Removed from shortlist");
  if ($("#view-shortlist").classList.contains("active")) renderShortlist();
}
function saveShortlist() { localStorage.setItem("js.shortlist", JSON.stringify([...state.shortlist])); updateShortlistCount(); }
function updateShortlistCount() { $("#shortlist-count").textContent = state.shortlist.size; moveTabIndicator(); }

function renderShortlist() {
  const box = $("#shortlist-results");
  if (!state.engine) return;
  const items = [...state.shortlist].map(([id, saved]) => [findById(id), saved]).filter(([j]) => j);
  box.innerHTML = items.length ? items.map(([j, saved], i) => profileCardHTML(j, i + 1, saved)).join("") : `<div class="no-results">Your shortlist is empty. Star journals from your results to compare them here.</div>`;
}

function exportCSV() {
  const v = (k) => (state.engine.meta.vintage?.[k] ? ` ${state.engine.meta.vintage[k]}` : "");
  const rows = [["Journal", "Publisher", "ISSN", "eISSN", `ABDC 2025${state.engine.meta.abdc_final ? "" : " (draft)"}`, "ABDC 2022", "FoR", `SJR${v("sjr")}`, `SJR quartile${v("q")}`, `H-index (SCImago${v("h")})`, "Scopus", "Open access (Scopus list)", "Scopus page", "SCImago page", "Justification", "Journal URL"]];
  for (const [id, saved] of state.shortlist) {
    const j = findById(id);
    if (!j) continue;
    rows.push([j.t, j.p, j.i, j.e, j.ab ? (j.ab[0] === "X" ? (state.engine.meta.abdc_final ? "Not on 2025 list" : "Proposed removal") : j.ab[0]) : "", j.ab?.[1] || "", j.ab?.[2] || "", j.sjr ?? "", j.q || "", j.h ?? "", j.s ? (j.inact ? "Inactive" : "Active") : "No", j.oa ? "Yes" : "No", verifyUrls(j).scopus, verifyUrls(j).scimago, plainSummary(saved.summary || ""), j.url || ""]);
  }
  const csv = rows.map((r) => r.map((c) => `"${String(c ?? "").replace(/"/g, '""')}"`).join(",")).join("\r\n");
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(new Blob(["\ufeff" + csv], { type: "text/csv" })), download: "journal-shortlist.csv" });
  a.click();
  URL.revokeObjectURL(a.href);
}

// ------------------------------------------------------------ AI
async function runAI(btn) {
  btn.disabled = true;
  const orig = btn.textContent;
  btn.innerHTML = `<span class="spinner"></span> Asking ${esc(aiSettings.model)}…`;
  try {
    state.last.ai = await aiReview(state.engine, state.last.input, state.last.out.results, 10);
    renderResults();
    toast("AI review added to the top 10");
  } catch (e) {
    console.error(e);
    toast(e.message);
    btn.disabled = false;
    btn.textContent = orig;
  }
}

// ------------------------------------------------------------ toast
let tt;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(tt);
  tt = setTimeout(() => t.classList.remove("show"), 2800);
}
