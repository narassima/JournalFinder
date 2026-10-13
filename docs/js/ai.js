// Optional Gemini integration — runs entirely in the browser with the user's own key.
const LS_KEY = "js.gemini.key";
const LS_MODEL = "js.gemini.model";
export const DEFAULT_MODEL = "gemini-2.5-flash";

export const aiSettings = {
  get key() { return localStorage.getItem(LS_KEY) || ""; },
  set key(v) { v ? localStorage.setItem(LS_KEY, v) : localStorage.removeItem(LS_KEY); },
  get model() { return localStorage.getItem(LS_MODEL) || DEFAULT_MODEL; },
  set model(v) { v ? localStorage.setItem(LS_MODEL, v) : localStorage.removeItem(LS_MODEL); },
  get enabled() { return !!this.key; },
};

function journalBrief(engine, s, idx) {
  const j = s.j;
  const topics = (s.hits || []).slice(0, 4).map((h) => `${engine.T[h[0]][0]} (${Math.round(h[2] * 100)}% of output)`);
  return {
    id: idx + 1,
    title: j.t,
    publisher: j.p,
    scope_topics_matching_abstract: topics,
    scopus_subjects: (j.a || []).slice(0, 3).map((c) => engine.asjc[c] || c),
    abdc_2025_draft: j.ab ? (j.ab[0] === "X" ? `proposed removal (was ${j.ab[1]})` : j.ab[0]) : "not listed",
    abdc_2022: j.ab ? j.ab[1] : "not listed",
    sjr: j.sjr ?? null,
    sjr_quartile: j.q || null,
    h_index: j.h ?? null,
    citescore: j.cs ?? null,
    open_access: !!(j.oa || j.doaj),
    apc_usd: j.apc ?? null,
    scope_fit_0_100: Math.round(s.fitRel * 100),
  };
}

export async function aiReview(engine, input, results, n = 10) {
  const key = aiSettings.key;
  if (!key) throw new Error("No Gemini API key set (⚙ settings).");
  const shortlist = results.slice(0, n).map((s, i) => journalBrief(engine, s, i));
  const prompt = `You are an experienced journal editor and research-publishing advisor.
An author wants to know where to submit the manuscript below. A retrieval system has shortlisted journals using
journal scope profiles and metrics (Scopus/SCImago, ABDC Draft 2025 list). Assess each journal's fit for THIS manuscript.

MANUSCRIPT
Title: ${input.title || "(none)"}
Keywords: ${input.keywords || "(none)"}
Abstract: ${input.abstract}

SHORTLIST (JSON)
${JSON.stringify(shortlist, null, 1)}

Return JSON with this exact shape:
{
 "overall": "2-4 sentences: what the manuscript is about, which journal family suits it, and a recommended submission order (reach → target → safe) referencing journal ids.",
 "journals": [ {"id": 1, "verdict": "Strong fit|Good fit|Possible fit|Weak fit", "justification": "2-3 sentences referencing the journal's scope and the manuscript's contribution, and any positioning advice (e.g. what to emphasise for this journal)."} ]
}
Be specific and candid. Do not invent metrics; use only those provided. Do not claim acceptance likelihood.`;

  const url = `https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(aiSettings.model)}:generateContent?key=${encodeURIComponent(key)}`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      contents: [{ role: "user", parts: [{ text: prompt }] }],
      generationConfig: { temperature: 0.3, responseMimeType: "application/json" },
    }),
  });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try { msg = (await res.json()).error?.message || msg; } catch { /* ignore */ }
    throw new Error(`Gemini API error: ${msg}`);
  }
  const data = await res.json();
  const text = data.candidates?.[0]?.content?.parts?.map((p) => p.text).join("") || "";
  const json = JSON.parse(text.replace(/^```json\s*|\s*```$/g, ""));
  const byId = new Map((json.journals || []).map((x) => [x.id, x]));
  return { overall: json.overall || "", notes: results.slice(0, n).map((s, i) => byId.get(i + 1) || null) };
}
