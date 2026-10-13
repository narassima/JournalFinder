"""
Journal Finder - data build pipeline
====================================

Merges the following sources into compact JSON files consumed by the static web app (docs/):

  1. Scopus Source List (Excel)         -> indexing status, ASJC subject codes, publisher, OA flag
  2. ABDC Journal Quality List (Excel)  -> ABDC rating (2025 draft + 2022), FoR code, change recommendation
  3. SCImago Journal Rank CSV (optional)-> SJR, best quartile, H-index, cites/doc, per-category quartiles
  4. OpenAlex API (fetched, CC0)        -> journal scope profile (research topics + their share),
                                           2-yr mean citedness, h-index, APC, DOAJ, homepage
  5. CiteScore export (optional Excel)  -> CiteScore / percentile / SNIP if you download it from Scopus

Outputs (docs/data/):
  journals.json  - one compact record per journal
  topics.json    - OpenAlex topic vocabulary (name, keywords, description, subfield) used as scope text
  asjc.json      - ASJC code -> subject name
  meta.json      - build date, source versions, counts

Usage:
  python build/build_data.py                    # uses files in data/raw, fetches OpenAlex (cached)
  python build/build_data.py --offline          # do not call OpenAlex, only use cache
  python build/build_data.py --refresh-openalex # ignore cache and refetch

Raw-file discovery (data/raw):
  Scopus  : *scopus*source*.xlsx
  ABDC    : *abdc*.xlsx
  SCImago : *scimago*.csv
  CiteScore (optional): *citescore*.xlsx|csv
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import glob
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict

import openpyxl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
CACHE = os.path.join(ROOT, "data", "cache")
OUT = os.path.join(ROOT, "docs", "data")

OPENALEX = "https://api.openalex.org"
MAILTO = os.environ.get("OPENALEX_MAILTO", "")  # polite pool: set OPENALEX_MAILTO=you@uni.edu
OPENALEX_KEY = os.environ.get("OPENALEX_API_KEY", "")

# ANZSRC 2020 Field-of-Research codes used in the ABDC list
FOR_NAMES = {
    "3501": "Accounting, auditing & accountability",
    "3502": "Banking, finance & investment",
    "3503": "Business systems in context",
    "3504": "Commercial services",
    "3505": "Human resources & industrial relations",
    "3506": "Marketing",
    "3507": "Strategy, management & organisational behaviour",
    "3508": "Tourism",
    "3509": "Transportation, logistics & supply chains",
    "3599": "Other commerce, management, tourism & services",
    "3801": "Applied economics",
    "3802": "Econometrics",
    "3803": "Economic theory",
    "3899": "Other economics",
    "4609": "Information systems",
    "4801": "Commercial law",
    "4905": "Statistics",
    "35": "Commerce, management, tourism & services",
    "38": "Economics",
}


# ---------------------------------------------------------------- helpers
def log(*a):
    print("[build]", *a, flush=True)


def find_one(patterns, required=True):
    for pat in patterns:
        hits = sorted(glob.glob(os.path.join(RAW, pat)), key=os.path.getmtime, reverse=True)
        if hits:
            return hits[0]
    if required:
        sys.exit(f"Missing input in {RAW}: expected one of {patterns}")
    return None


def norm_issn(v) -> str:
    """Return ISSN as 8 chars without hyphen (e.g. '00014273'), or ''."""
    if v is None:
        return ""
    s = str(v).strip().upper().replace("-", "").replace("\t", "").replace(" ", "")
    if s.endswith(".0"):
        s = s[:-2]
    s = re.sub(r"[^0-9X]", "", s)
    if not s:
        return ""
    if len(s) < 8 and s.isdigit():
        s = s.zfill(8)
    return s if len(s) == 8 else ""


def hyphen(issn8: str) -> str:
    return f"{issn8[:4]}-{issn8[4:]}"


def to_float(v):
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        return None


def clean(v) -> str:
    return re.sub(r"\s+", " ", str(v)).strip() if v is not None else ""


# ---------------------------------------------------------------- Scopus
def load_scopus(path):
    log("Scopus:", os.path.basename(path))
    wb = openpyxl.load_workbook(path, read_only=True)
    main = next(s for s in wb.sheetnames if s.lower().startswith("scopus sources"))
    ws = wb[main]
    rows = ws.iter_rows(values_only=True)
    hdr = [clean(h).lower() for h in next(rows)]

    def col(*keys):
        for i, h in enumerate(hdr):
            if all(k in h for k in keys):
                return i
        raise KeyError(keys)

    c = dict(
        id=col("sourcerecord id"), title=col("source title"), issn=hdr.index("issn"), eissn=col("eissn"),
        active=col("active or inactive"), coverage=col("coverage"), disc=col("discontinued"),
        oa=col("open access"), type=col("source type"), pub=col("publisher"), asjc=col("classification codes"),
        lang=col("article language"),
    )
    journals = {}
    for r in rows:
        if r[c["id"]] is None:
            continue
        if clean(r[c["type"]]).lower() not in ("journal", "trade journal"):
            continue
        sid = clean(r[c["id"]])
        asjc = [a.strip() for a in re.split(r"[;,]", clean(r[c["asjc"]])) if a.strip().isdigit()]
        journals[sid] = dict(
            sid=sid,
            title=clean(r[c["title"]]),
            issn=norm_issn(r[c["issn"]]),
            eissn=norm_issn(r[c["eissn"]]),
            active=clean(r[c["active"]]).lower() == "active",
            coverage=clean(r[c["coverage"]]),
            discontinued=bool(clean(r[c["disc"]])),
            oa=bool(clean(r[c["oa"]])),
            publisher=clean(r[c["pub"]]),
            asjc=asjc,
            lang=clean(r[c["lang"]]),
            stype=clean(r[c["type"]]),
        )
    # Discontinued sheet (recent discontinuations)
    disc_sheet = next((s for s in wb.sheetnames if s.lower().startswith("discontinued")), None)
    disc_ids = set()
    if disc_sheet:
        for r in wb[disc_sheet].iter_rows(min_row=3, values_only=True):
            if r and r[0] is not None:
                disc_ids.add(clean(r[0]))
    for sid in disc_ids:
        if sid in journals:
            journals[sid]["discontinued"] = True

    # ASJC code names
    asjc_names = {}
    asjc_sheet = next((s for s in wb.sheetnames if "asjc" in s.lower()), None)
    if asjc_sheet:
        for r in wb[asjc_sheet].iter_rows(values_only=True):
            if r and isinstance(r[0], (int, float)) and r[1]:
                asjc_names[str(int(r[0]))] = clean(r[1])
    wb.close()
    m = re.search(r"([A-Za-z]{3,9})[ ._-]*(20\d\d)", os.path.basename(path))
    version = f"{m.group(1).title()} {m.group(2)}" if m else os.path.basename(path)
    log(f"  {len(journals):,} journal records, {len(disc_ids):,} recently discontinued, {len(asjc_names)} ASJC codes")
    return journals, asjc_names, version


# ---------------------------------------------------------------- ABDC
def load_abdc(path):
    log("ABDC:", os.path.basename(path))
    wb = openpyxl.load_workbook(path, read_only=True)
    out = []
    version = "ABDC JQL"
    for sname in wb.sheetnames:
        ws = wb[sname]
        hdr_idx, hdr = None, None
        rows = list(ws.iter_rows(values_only=True))
        for i, r in enumerate(rows[:40]):
            cells = [clean(x).lower() for x in r]
            if "journal title" in cells:
                hdr_idx, hdr = i, cells
                break
            for x in cells:
                if "abdc journal quality list" in x:
                    version = clean(next(v for v in r if v and "ABDC" in str(v)))
        if hdr is None:
            continue
        removed_sheet = "removed" in sname.lower()

        def ci(*keys):
            for i, h in enumerate(hdr):
                if all(k in h for k in keys):
                    return i
            return None

        c_title, c_pub, c_issn = ci("journal title"), ci("publisher"), ci("issn")
        c_eissn = ci("issnonline") if ci("issnonline") is not None else ci("online")
        c_for, c_year = ci("for"), ci("inception")
        c_r22 = ci("2022") if ci("2022") is not None else ci("rating")
        c_r25 = ci("2025")
        c_rec = ci("recommendation")
        for r in rows[hdr_idx + 1:]:
            if c_title is None or not r[c_title]:
                continue
            r22 = clean(r[c_r22]).upper().replace(" ", "") if c_r22 is not None else ""
            r25 = clean(r[c_r25]).upper().replace(" ", "") if c_r25 is not None else ""
            r22 = r22 if r22 in ("A*", "A", "B", "C") else ""
            r25 = r25 if r25 in ("A*", "A", "B", "C") else ""
            forc = clean(r[c_for]) if c_for is not None else ""
            forc = forc[:-2] if forc.endswith(".0") else forc
            out.append(dict(
                title=clean(r[c_title]),
                publisher=clean(r[c_pub]) if c_pub is not None else "",
                issn=norm_issn(r[c_issn]) if c_issn is not None else "",
                eissn=norm_issn(r[c_eissn]) if c_eissn is not None else "",
                for_code=forc,
                year=clean(r[c_year]) if c_year is not None else "",
                r2022=r22,
                r2025=r25,
                rec=clean(r[c_rec]) if c_rec is not None else "",
                removed=removed_sheet,
            ))
    wb.close()
    if "draft" in os.path.basename(path).lower() and "draft" not in version.lower():
        version = "Draft " + version
    log(f"  {sum(not x['removed'] for x in out):,} listed, {sum(x['removed'] for x in out):,} proposed for removal")
    return out, version


# ---------------------------------------------------------------- SCImago
def load_scimago(path):
    if not path:
        log("SCImago: not found (optional) - SJR/quartile metrics will be blank. "
            "Download from https://www.scimagojr.com/journalrank.php -> 'Download data' into data/raw/")
        return {}, None
    log("SCImago:", os.path.basename(path))
    with open(path, encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        delim = ";" if sample.count(";") > sample.count(",") else ","
        rd = csv.DictReader(f, delimiter=delim)
        out = {}
        for r in rd:
            r = {(k or "").strip(): (v or "").strip() for k, v in r.items()}
            sid = r.get("Sourceid") or r.get("Source ID") or ""
            cites2 = next((v for k, v in r.items() if k.lower().startswith("citations / doc")), "")
            docs = next((v for k, v in r.items() if k.lower().startswith("total docs. (20")), "")
            cats = []
            for part in (r.get("Categories") or "").split(";"):
                m = re.match(r"\s*(.+?)\s*\((Q[1-4])\)\s*$", part)
                if m:
                    cats.append([m.group(1), m.group(2)])
                elif part.strip():
                    cats.append([part.strip(), ""])
            out[sid] = dict(
                sjr=to_float(r.get("SJR")),
                q=(r.get("SJR Best Quartile") or "").replace("-", ""),
                h=int(to_float(r.get("H index")) or 0) or None,
                cpd=to_float(cites2),
                docs=int(to_float(docs) or 0) or None,
                rank=int(to_float(r.get("Rank")) or 0) or None,
                country=r.get("Country", ""),
                cats=cats,
                areas=r.get("Areas", ""),
                issns=[norm_issn(x) for x in (r.get("Issn") or "").split(",") if norm_issn(x)],
            )
    m = re.search(r"(20\d\d)", os.path.basename(path))
    log(f"  {len(out):,} SCImago records")
    return out, (f"SJR {m.group(1)}" if m else "SJR (latest)")


# ---------------------------------------------------------------- CiteScore (optional)
def load_citescore(path):
    if not path:
        return {}
    log("CiteScore:", os.path.basename(path))
    out = {}
    if path.lower().endswith(".csv"):
        with open(path, encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
    else:
        wb = openpyxl.load_workbook(path, read_only=True)
        ws = wb[wb.sheetnames[0]]
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
    hi = next(i for i, r in enumerate(rows[:30]) if any("citescore" in clean(x).lower() for x in r))
    hdr = [clean(x).lower() for x in rows[hi]]

    def ci(*keys):
        return next((i for i, h in enumerate(hdr) if all(k in h for k in keys)), None)

    c_id = ci("source", "id") if ci("source", "id") is not None else ci("scopus", "id")
    c_issn = ci("print", "issn") if ci("print", "issn") is not None else ci("issn")
    c_eissn = ci("e", "issn") if ci("e", "issn") is not None else ci("online", "issn")
    c_cs, c_pct, c_snip = ci("citescore"), ci("percentile"), ci("snip")
    c_wos = ci("wos")

    for r in rows[hi + 1:]:
        sid = clean(r[c_id]).replace(".0", "") if c_id is not None and r[c_id] else None
        issn1 = norm_issn(r[c_issn]) if c_issn is not None and r[c_issn] else None
        issn2 = norm_issn(r[c_eissn]) if c_eissn is not None and r[c_eissn] else None
        if not sid and not issn1 and not issn2:
            continue

        rec = {}
        cs = to_float(r[c_cs]) if c_cs is not None else None
        if cs is not None:
            rec["cs"] = cs
        if c_pct is not None and to_float(r[c_pct]) is not None:
            rec["pct"] = to_float(r[c_pct])
        if c_snip is not None and to_float(r[c_snip]) is not None:
            rec["snip"] = to_float(r[c_snip])
        if c_wos is not None and clean(r[c_wos]):
            rec["wos"] = clean(r[c_wos])

        if not rec:
            continue

        if sid:
            out[sid] = rec
        if issn1:
            out["issn:" + issn1] = rec
        if issn2:
            out["issn:" + issn2] = rec

    log(f"  {len(out):,} CiteScore / metrics records")
    return out


# ---------------------------------------------------------------- OpenAlex
def http_json(url, tries=8):
    mailto = MAILTO or "narassima.s@greatlakes.edu.in"
    if mailto and "mailto=" not in url:
        url += ("&" if "?" in url else "?") + "mailto=" + urllib.parse.quote(mailto)
    if OPENALEX_KEY and "api_key=" not in url:
        url += ("&" if "?" in url else "?") + "api_key=" + urllib.parse.quote(OPENALEX_KEY)
    for k in range(tries):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "JournalScope/1.0 (academic research; mailto:" + mailto + ")",
                    "Accept": "application/json"
                }
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = max(5, 3 ** min(k + 1, 4))
                log(f"  OpenAlex rate limit (429), pausing for {wait}s (attempt {k+1}/{tries})...")
                time.sleep(wait)
            else:
                wait = 2 ** k
                log(f"  HTTP {e.code} error, retry in {wait}s...")
                time.sleep(wait)
        except Exception as e:  # noqa: BLE001
            wait = 2 ** k
            log(f"  network retry {k + 1}/{tries} in {wait}s: {e}")
            time.sleep(wait)
    raise RuntimeError(f"OpenAlex request failed: {url}")


def fetch_topics(refresh=False, offline=False):
    cache = os.path.join(CACHE, "openalex_topics.json")
    if os.path.exists(cache) and not refresh:
        return json.load(open(cache, encoding="utf-8"))
    if offline:
        sys.exit("No cached OpenAlex topics and --offline given.")
    log("OpenAlex: fetching topic vocabulary ...")
    out, cursor = [], "*"
    sel = "id,display_name,description,keywords,subfield,field,domain,works_count"
    while cursor:
        d = http_json(f"{OPENALEX}/topics?per_page=200&select={sel}&cursor={urllib.parse.quote(cursor)}")
        out.extend(d["results"])
        cursor = d["meta"].get("next_cursor")
    os.makedirs(CACHE, exist_ok=True)
    json.dump(out, open(cache, "w", encoding="utf-8"))
    log(f"  {len(out):,} topics")
    return out


def fetch_sources(issns, priority_issns=None, refresh=False, offline=False, max_new=None):
    """Fetch OpenAlex sources for a set of hyphen-less ISSNs. Returns issn8 -> source dict."""
    cache = os.path.join(CACHE, "openalex_sources.json")
    data = {}
    if os.path.exists(cache) and not refresh:
        data = json.load(open(cache, encoding="utf-8"))
    by_issn = {}
    for s in data.values():
        for i in s.get("issn") or []:
            by_issn[norm_issn(i)] = s
    tried_path = os.path.join(CACHE, "openalex_tried_issns.json")
    tried = set(json.load(open(tried_path))) if os.path.exists(tried_path) and not refresh else set()

    pri = set(priority_issns or [])
    todo_pri = sorted(i for i in issns if i and i in pri and i not in by_issn and i not in tried)
    todo_rest = sorted(i for i in issns if i and i not in pri and i not in by_issn and i not in tried)
    todo = todo_pri + todo_rest

    if max_new is not None:
        todo = todo[:max_new]

    if todo and offline:
        log(f"OpenAlex: offline, {len(todo):,} ISSNs not in cache - skipped")
        todo = []
    if todo:
        log(f"OpenAlex: fetching {len(todo):,} ISSNs in batches of 50 ({len(todo_pri)} priority ABDC) ...")

    sel = ("id,display_name,issn_l,issn,host_organization_name,works_count,cited_by_count,summary_stats,"
           "topics,is_oa,is_in_doaj,apc_usd,homepage_url,country_code,type")
    B = 50
    for bi in range(0, len(todo), B):
        batch = todo[bi:bi + B]
        flt = "issn:" + "|".join(hyphen(i) for i in batch)
        d = http_json(f"{OPENALEX}/sources?filter={flt}&per_page=200&select={sel}")
        for s in d["results"]:
            data[s["id"]] = s
            for i in s.get("issn") or []:
                by_issn[norm_issn(i)] = s
        tried.update(batch)
        if (bi // B) % 10 == 0 or (bi + B >= len(todo)):
            log(f"  {min(bi + B, len(todo)):,}/{len(todo):,}  ({len(data):,} sources cached)")
            os.makedirs(CACHE, exist_ok=True)
            json.dump(data, open(cache, "w", encoding="utf-8"))
            json.dump(sorted(tried), open(tried_path, "w"))
        time.sleep(0.2)
    os.makedirs(CACHE, exist_ok=True)
    json.dump(data, open(cache, "w", encoding="utf-8"))
    json.dump(sorted(tried), open(tried_path, "w"))
    return by_issn


# ---------------------------------------------------------------- merge
def short_id(oa_id: str) -> int:
    return int(re.sub(r"\D", "", oa_id.rsplit("/", 1)[-1]))


def build(args):
    scopus_path = find_one(["*scopus*source*.xlsx", "*scopus*.xlsx"])
    abdc_path = find_one(["*abdc*.xlsx"])
    sjr_path = find_one(["*scimago*.csv", "scimagojr*.csv", "*sjr*.csv"], required=False)
    cs_path = find_one(["*citescore*.xlsx", "*citescore*.csv"], required=False)

    scopus, asjc_names, scopus_ver = load_scopus(scopus_path)
    abdc, abdc_ver = load_abdc(abdc_path)
    sjr, sjr_ver = load_scimago(sjr_path)
    cites = load_citescore(cs_path)

    # Keep active Scopus journals (+ inactive ones that are ABDC listed, flagged)
    issn_to_sid = {}
    for sid, j in scopus.items():
        for i in (j["issn"], j["eissn"]):
            if i:
                issn_to_sid.setdefault(i, sid)
    for sid, s in sjr.items():
        if sid in scopus:
            for i in s["issns"]:
                issn_to_sid.setdefault(i, sid)

    records = {}
    for sid, j in scopus.items():
        if j["active"] and not j["discontinued"]:
            records[sid] = dict(j, scopus=True)

    # attach ABDC
    def tkey(t):
        t = re.sub(r"^the\s+", "", t.lower().replace("&", "and"))
        return re.sub(r"[^a-z0-9]", "", t)

    title_to_sid = {}
    for sid, j in scopus.items():
        k = tkey(j["title"])
        if k and (k not in title_to_sid or (j["active"] and not scopus[title_to_sid[k]]["active"])):
            title_to_sid[k] = sid

    abdc_only = 0
    title_joined = 0
    for a in abdc:
        sid = issn_to_sid.get(a["issn"]) or issn_to_sid.get(a["eissn"])
        if not sid:
            sid = title_to_sid.get(tkey(a["title"]))
            if sid:
                title_joined += 1
        if sid and sid in scopus:
            rec = records.get(sid)
            if rec is None:  # inactive / discontinued in Scopus but ABDC-listed -> keep, flagged
                rec = records[sid] = dict(scopus[sid], scopus=True)
        else:
            key = "abdc:" + (a["issn"] or a["eissn"] or a["title"])
            rec = records.setdefault(key, dict(
                sid="", title=a["title"], issn=a["issn"], eissn=a["eissn"], active=False, coverage="",
                discontinued=False, oa=False, publisher=a["publisher"], asjc=[], lang="", stype="Journal",
                scopus=False,
            ))
            abdc_only += 1
        # prefer listed over removed record
        if "abdc" in rec and not rec["abdc"]["removed"] and a["removed"]:
            continue
        rec["abdc"] = a
    log(f"Merged: {len(records):,} journals ({title_joined} ABDC joined by title; "
        f"{abdc_only:,} ABDC journals not found in Scopus list)")

    # OpenAlex
    want = set()
    priority_issns = set()
    for r in records.values():
        want.update(x for x in (r["issn"], r["eissn"]) if x)
        if "abdc" in r:
            priority_issns.update(x for x in (r["issn"], r["eissn"]) if x)

    topics = fetch_topics(args.refresh_openalex, args.offline)
    oa_by_issn = fetch_sources(
        want,
        priority_issns=priority_issns,
        refresh=args.refresh_openalex,
        offline=args.offline,
        max_new=getattr(args, "max_new", None)
    )

    # topic vocabulary -> compact index
    topic_index = {t["id"]: i for i, t in enumerate(topics)}
    topics_out = []
    for t in topics:
        topics_out.append([
            t["display_name"],
            [k["display_name"] for k in (t.get("keywords") or [])],
            t.get("description") or "",
            (t.get("subfield") or {}).get("display_name", ""),
            (t.get("field") or {}).get("display_name", ""),
            # OpenAlex subfield ids are ASJC codes (e.g. .../subfields/1407)
            int(re.sub(r"\D", "", (t.get("subfield") or {}).get("id", "0")) or 0),
        ])

    # final compact records
    out = []
    matched_oa = 0
    for key, r in records.items():
        s = oa_by_issn.get(r["issn"]) or oa_by_issn.get(r["eissn"])
        rec = {
            "t": r["title"],
            "p": r["publisher"] or (s or {}).get("host_organization_name") or "",
            "i": hyphen(r["issn"]) if r["issn"] else "",
            "e": hyphen(r["eissn"]) if r["eissn"] else "",
        }
        if r["scopus"]:
            rec["s"] = r["sid"]
            rec["a"] = [int(x) for x in r["asjc"]]
            if not r["active"]:
                rec["inact"] = 1
            if r["discontinued"]:
                rec["disc"] = 1
            if r["oa"]:
                rec["oa"] = 1
        if "abdc" in r:
            a = r["abdc"]
            rec["ab"] = [a["r2025"] or "-", a["r2022"] or "-", a["for_code"], a["rec"] if a["rec"] else ""]
            if a["removed"]:
                rec["ab"][0] = "X"  # proposed for removal
        sj = sjr.get(r.get("sid") or "")
        if sj:
            rec["sjr"] = round(sj["sjr"], 3) if sj["sjr"] is not None else None
            rec["q"] = sj["q"]
            if sj["h"]:
                rec["h"] = sj["h"]
            if sj["cpd"] is not None:
                rec["cpd"] = round(sj["cpd"], 2)
            if sj["cats"]:
                rec["cat"] = sj["cats"][:6]
            if sj["country"]:
                rec["c"] = sj["country"]
        cs = (cites.get(r.get("sid") or "") or
              cites.get("issn:" + r.get("issn", "")) or
              cites.get("issn:" + r.get("eissn", "")))
        if cs:
            if cs.get("cs") is not None:
                rec["cs"] = cs["cs"]
            if cs.get("pct") is not None:
                rec["csp"] = cs["pct"]
            if cs.get("snip") is not None:
                rec["snip"] = cs["snip"]
            if cs.get("wos"):
                rec["wos"] = cs["wos"]
        if s:
            matched_oa += 1
            st = s.get("summary_stats") or {}
            rec["ox"] = short_id(s["id"])
            if st.get("2yr_mean_citedness") is not None:
                rec["m2"] = round(st["2yr_mean_citedness"], 2)
            if st.get("h_index") and "h" not in rec:
                rec["h"] = st["h_index"]
            rec["w"] = s.get("works_count") or 0
            if s.get("apc_usd"):
                rec["apc"] = s["apc_usd"]
            if s.get("is_in_doaj"):
                rec["doaj"] = 1
            if s.get("homepage_url"):
                rec["url"] = s["homepage_url"]
            if s.get("country_code") and "c" not in rec:
                rec["c"] = s["country_code"]
            tl = []
            for t in (s.get("topics") or []):
                ti = topic_index.get(t["id"])
                if ti is not None and t.get("count"):
                    tl.append([ti, t["count"]])
            if tl:
                rec["tp"] = tl
        out.append(rec)

    out.sort(key=lambda x: x["t"].lower())
    os.makedirs(OUT, exist_ok=True)

    def dump(name, obj):
        p = os.path.join(OUT, name)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        log(f"  wrote {name}: {os.path.getsize(p) / 1e6:.2f} MB")

    meta = {
        "built": dt.datetime.now().isoformat(timespec="minutes"),
        "sources": {
            "scopus": f"Scopus Source List ({scopus_ver})",
            "abdc": abdc_ver,
            "sjr": sjr_ver or "not loaded",
            "openalex": f"OpenAlex (fetched {dt.date.today().isoformat()})",
            "citescore": os.path.basename(cs_path) if cs_path else "not loaded",
        },
        "counts": {
            "journals": len(out),
            "scopus": sum(1 for x in out if "s" in x),
            "abdc": sum(1 for x in out if "ab" in x and x["ab"][0] != "X"),
            "sjr": sum(1 for x in out if "sjr" in x),
            "openalex": matched_oa,
            "with_topics": sum(1 for x in out if "tp" in x),
        },
        "for_names": FOR_NAMES,
    }
    dump("journals.json", out)
    dump("topics.json", topics_out)
    dump("asjc.json", asjc_names)
    dump("meta.json", meta)
    log("Counts:", json.dumps(meta["counts"]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true", help="never call OpenAlex; use cache only")
    ap.add_argument("--refresh-openalex", action="store_true", help="ignore OpenAlex cache")
    ap.add_argument("--max-new", type=int, default=None, help="limit number of new ISSNs to query from OpenAlex in this run")
    build(ap.parse_args())
