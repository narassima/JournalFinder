// Text processing: tokenisation, light stemming, n-grams.
const STOP = new Set(`a about above across after again against all almost also although always am among an and another any
are around as at be because been before being below between both but by can could did do does doing done down during each
either else enough etc even ever every few for from further had has have having he her here hers herself him himself his how
however i if in into is it its itself just least less like made make many may me might more most mostly much must my myself
neither no nor not now of off often on once one only onto or other others otherwise our ours ourselves out over own per
perhaps quite rather really same several shall she should since so some still such than that the their theirs them themselves
then there therefore these they this those though through throughout thus to too toward towards under until up upon us use used
uses using very via was we well were what whatever when where whereas whether which while who whom whose why will with within
without would yet you your yours yourself yourselves
paper study studies research article result results findings finding show shows shown showed propose proposed proposes approach
approaches based analysis analyse analyze analyzed analysed examine examines examined investigate investigates investigated
aim aims purpose objective objectives method methods methodology design originality value implication implications practical
theoretical contribution contributes contribute literature data sample new provide provides provided significant significantly
effect effects role impact impacts relationship relationships among various different within across key important evidence also
further using use first second third two three including include includes moreover furthermore however finally overall
paper's author authors present presents presented discuss discussed discusses suggest suggests suggested indicate indicates
indicated reveal reveals revealed highlight highlights find finds found conclude concludes concluded limitation limitations
future thereby whilst respectively related particular particularly specific specifically level levels high higher low lower
large larger small smaller number numbers increase increased increasing decrease way ways type types case cases`.split(/\s+/));

// Light English suffix stripper (Porter-inspired, conservative to keep terms readable)
export function stem(w) {
  if (w.length <= 3) return w;
  const rules = [
    [/ization$/, "ize"], [/isation$/, "ize"], [/ational$/, "ate"], [/fulness$/, "ful"], [/iveness$/, "ive"],
    [/ousness$/, "ous"], [/ization$/, "ize"], [/biliti$/, "ble"], [/alities$/, "al"], [/ities$/, "ity"],
    [/ments?$/, ""], [/ings?$/, ""], [/ies$/, "y"], [/ied$/, "y"], [/sses$/, "ss"], [/([^s])s$/, "$1"],
    [/eed$/, "ee"], [/([aeiou].*)ed$/, "$1"], [/ise$/, "ize"], [/ised$/, "ize"], [/izing$/, "ize"],
    [/ational$/, "ate"], [/ally$/, "al"], [/ly$/, ""],
  ];
  let s = w;
  for (const [re, rep] of rules) {
    if (re.test(s)) { const t = s.replace(re, rep); if (t.length >= 3) { s = t; break; } }
  }
  // second pass for common derivational endings
  s = s.replace(/(ation|ition|ions|ion)$/, (m) => (s.length - m.length >= 4 ? "" : m));
  s = s.replace(/(ness|ive|ize|ity|al|er|ic)$/, (m) => (s.length - m.length >= 4 ? "" : m));
  s = s.replace(/e$/, (m) => (s.length > 4 ? "" : m));
  return s;
}

const cache = new Map();
export function words(text) {
  return (text || "")
    .toLowerCase()
    .replace(/[’']/g, "")
    .replace(/[^a-z0-9\-\s]/g, " ")
    .replace(/-/g, " ")
    .split(/\s+/)
    .filter((w) => w.length > 2 && !STOP.has(w) && !/^\d+$/.test(w));
}

/** tokens = stemmed unigrams + stemmed bigrams (prefixed so they don't collide) */
export function tokens(text) {
  const ws = words(text).map((w) => {
    let s = cache.get(w);
    if (s === undefined) { s = stem(w); cache.set(w, s); }
    return s;
  });
  const out = ws.slice();
  for (let i = 0; i + 1 < ws.length; i++) out.push(ws[i] + "_" + ws[i + 1]);
  return out;
}

/** Map stems back to a readable surface form (first seen) */
export function surfaceMap(text) {
  const m = new Map();
  const ws = words(text);
  for (let i = 0; i < ws.length; i++) {
    const s = stem(ws[i]);
    if (!m.has(s)) m.set(s, ws[i]);
    if (i + 1 < ws.length) {
      const b = s + "_" + stem(ws[i + 1]);
      if (!m.has(b)) m.set(b, ws[i] + " " + ws[i + 1]);
    }
  }
  return m;
}

export function termFreq(toks, weight = 1, into = new Map()) {
  for (const t of toks) into.set(t, (into.get(t) || 0) + weight);
  return into;
}
