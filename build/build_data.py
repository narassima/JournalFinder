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

# OpenAlex returns ISO-3166 alpha-2 codes; SCImago spells country names out. Mapped to SCImago's spelling so the
# Country field is uniform. Codes not listed (e.g. OpenAlex's "XR", "BL") are dropped rather than guessed.
ISO2_COUNTRY = {
    "AE": "United Arab Emirates", "AR": "Argentina", "AU": "Australia", "BA": "Bosnia and Herzegovina",
    "BD": "Bangladesh", "BE": "Belgium", "BG": "Bulgaria", "BR": "Brazil", "CA": "Canada", "CH": "Switzerland",
    "CL": "Chile", "CN": "China", "CO": "Colombia", "CU": "Cuba", "CY": "Cyprus", "CZ": "Czech Republic",
    "DE": "Germany", "DZ": "Algeria", "EC": "Ecuador", "EE": "Estonia", "EG": "Egypt", "ES": "Spain",
    "FI": "Finland", "FJ": "Fiji", "FR": "France", "GB": "United Kingdom", "GE": "Georgia", "GH": "Ghana",
    "GR": "Greece", "HK": "Hong Kong", "HR": "Croatia", "HU": "Hungary", "ID": "Indonesia", "IE": "Ireland",
    "IN": "India", "IQ": "Iraq", "IR": "Iran", "IT": "Italy", "JP": "Japan", "KE": "Kenya", "KR": "South Korea",
    "LK": "Sri Lanka", "LT": "Lithuania", "LV": "Latvia", "MA": "Morocco", "ME": "Montenegro", "MK": "Macedonia",
    "MX": "Mexico", "MY": "Malaysia", "NE": "Niger", "NG": "Nigeria", "NL": "Netherlands", "NO": "Norway",
    "NZ": "New Zealand", "PE": "Peru", "PH": "Philippines", "PK": "Pakistan", "PL": "Poland", "PR": "Puerto Rico",
    "PT": "Portugal", "RO": "Romania", "RS": "Serbia", "RU": "Russian Federation", "SA": "Saudi Arabia",
    "SE": "Sweden", "SG": "Singapore", "SI": "Slovenia", "SK": "Slovakia", "TH": "Thailand", "TR": "Turkey",
    "TZ": "Tanzania", "UA": "Ukraine", "US": "United States", "UY": "Uruguay", "VE": "Venezuela",
    "VN": "Viet Nam", "ZA": "South Africa",
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
    main = next((s for s in wb.sheetnames if s.lower().startswith("scopus sources")), wb.sheetnames[0])
    ws = wb[main]
    rows = ws.iter_rows(values_only=True)
    hdr = [clean(h).lower() for h in next(rows)]

    def col(*keys, required=True):
        for i, h in enumerate(hdr):
            if all(k in h for k in keys):
                return i
        if required:
            raise KeyError(keys)
        return None

    def issn_col():  # "ISSN" (2026 list), "Print-ISSN" / "Print ISSN" (older / metrics editions)
        return next((i for i, h in enumerate(hdr) if re.fullmatch(r"(print[- ]?)?issn", h)), None)

    def eissn_col():
        return next((i for i, h in enumerate(hdr) if re.fullmatch(r"(e[- ]?issn|eissn|online[- ]?issn)", h)), None)

    c = dict(
        id=col("sourcerecord id"), title=col("source title"), issn=issn_col(), eissn=eissn_col(),
        active=col("active or inactive", required=False), coverage=col("coverage", required=False),
        disc=col("discontinued", required=False), oa=col("open access", required=False),
        type=col("source type", required=False), pub=col("publisher", required=False),
        asjc=col("classification codes", required=False), lang=col("article language", required=False),
    )
    if c["issn"] is None:
        raise KeyError("no ISSN column in the Scopus list")

    def cell(r, k):
        i = c[k]
        return r[i] if i is not None and i < len(r) else None
    # The title-list edition of the Scopus Source List carries no metrics. The edition downloaded from
    # scopus.com/sources ("Download Scopus Source List") adds CiteScore / SNIP / SJR columns; pick them up
    # when present so the same raw-file slot serves both.
    mc = {}
    for i, h in enumerate(hdr):
        y = re.search(r"(20\d\d)", h)
        yr = y.group(1) if y else ""
        if "citescore" in h and not any(w in h for w in ("percentile", "rank", "tracker", "quartile")):
            mc.setdefault("cs", (i, yr))
        elif "highest percentile" in h or h == "percentile":
            mc.setdefault("pct", (i, yr))
        elif h.startswith("snip"):
            mc.setdefault("snip", (i, yr))
        elif h.startswith("sjr"):
            mc.setdefault("sjr", (i, yr))
        elif ("citation" in h and "count" in h) or h.startswith("citations"):
            mc.setdefault("cit", (i, yr))
        elif ("document" in h and "count" in h) or h.startswith("documents"):
            mc.setdefault("doc", (i, yr))
    journals = {}
    for r in rows:
        if r[c["id"]] is None:
            continue
        # keep book series / other source types too: some ABDC titles are Scopus book series and must not be
        # reported as "not in Scopus". Only journals and trade journals become recommendable records (see build()).
        stype = clean(cell(r, "type")) or "Journal"
        if stype.lower() not in ("journal", "trade journal", "book series"):
            continue
        sid = clean(r[c["id"]])
        asjc = [a.strip() for a in re.split(r"[;,]", clean(cell(r, "asjc"))) if a.strip().isdigit()]
        met = {}
        for k, (i, _y) in mc.items():
            v = to_float(str(r[i]).replace("%", "")) if i < len(r) and r[i] is not None else None
            if v is not None:
                met[k] = v
        journals[sid] = dict(
            metrics=met,
            sid=sid,
            title=clean(r[c["title"]]),
            issn=norm_issn(cell(r, "issn")),
            eissn=norm_issn(cell(r, "eissn")),
            active=(clean(cell(r, "active")).lower() == "active") if c["active"] is not None else True,
            coverage=clean(cell(r, "coverage")),
            discontinued=bool(clean(cell(r, "disc"))),
            oa=bool(clean(cell(r, "oa"))),
            publisher=clean(cell(r, "pub")),
            asjc=asjc,
            lang=clean(cell(r, "lang")),
            stype=stype,
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
    metrics_years = {k: y for k, (_i, y) in mc.items()}
    log(f"  {len(journals):,} journal/book-series records, {len(disc_ids):,} recently discontinued, "
        f"{len(asjc_names)} ASJC codes")
    if metrics_years:
        log(f"  metric columns found: {metrics_years} "
            f"({sum(1 for j in journals.values() if j['metrics']):,} records with values)")
    else:
        log("  no metric columns in this Scopus list (title-list edition) - CiteScore/SNIP come from the "
            "supplementary file or stay blank")
    return journals, asjc_names, version, metrics_years


# ---------------------------------------------------------------- ABDC
_RANK = {"C": 1, "B": 2, "A": 3, "A*": 4}


def _abdc_title_key(t: str) -> str:
    t = re.sub(r"^the\s+", "", t.lower().replace("&", "and"))
    return re.sub(r"[^a-z0-9]", "", t)


def _read_abdc_sheet(ws):
    """Rows of one ABDC edition sheet (title, publisher, issn, eissn, FoR, year, rating)."""
    rows = list(ws.iter_rows(values_only=True))
    hi = next((i for i, r in enumerate(rows[:40]) if "journal title" in [clean(x).lower() for x in r]), None)
    if hi is None:
        return []
    hdr = [clean(x).lower() for x in rows[hi]]

    def ci(*keys):
        return next((i for i, h in enumerate(hdr) if all(k in h for k in keys)), None)

    c_title, c_pub, c_issn = ci("journal title"), ci("publisher"), ci("issn")
    c_eissn = ci("issnonline") if ci("issnonline") is not None else ci("online")
    c_for = ci("for") if ci("for") is not None else ci("field of research")
    c_year, c_rate = ci("inception"), ci("rating")
    out = []
    for r in rows[hi + 1:]:
        if c_title is None or not r[c_title] or c_rate is None:
            continue
        rating = clean(r[c_rate]).upper().replace(" ", "")
        forc = clean(r[c_for]) if c_for is not None else ""
        out.append(dict(
            title=clean(r[c_title]),
            publisher=clean(r[c_pub]) if c_pub is not None else "",
            issn=norm_issn(r[c_issn]) if c_issn is not None else "",
            eissn=norm_issn(r[c_eissn]) if c_eissn is not None else "",
            for_code=forc[:-2] if forc.endswith(".0") else forc,
            year=clean(r[c_year]) if c_year is not None else "",
            rating=rating if rating in _RANK or rating == "A*" else "",
        ))
    return out


def _load_abdc_final(wb, path):
    """Final ABDC list layout: '2025 JQL' (2025 rating) + '2022 JQL' (2022 rating) sheets, no removal sheet.
    The 2022 rating is joined from the 2022 sheet; journals in the 2022 list that have no counterpart in the
    2025 list are reported as removed; the change note is derived from the two ratings."""
    new = _read_abdc_sheet(wb["2025 JQL"])
    old = _read_abdc_sheet(wb["2022 JQL"])
    # 2022 -> 2025 matching: exact normalised title first, then ISSN / eISSN, one-to-one
    by_title = {}
    for o in old:
        by_title.setdefault(_abdc_title_key(o["title"]), []).append(o)
    by_issn = {}
    for o in old:
        for k in (o["issn"], o["eissn"]):
            if k:
                by_issn.setdefault(k, []).append(o)
    used = set()
    pairs = {}
    for pass_no in (1, 2, 3):
        for i, a in enumerate(new):
            if i in pairs:
                continue
            nk = _abdc_title_key(a["title"])
            if pass_no == 1:
                cands = by_title.get(nk, [])
            elif pass_no == 2:
                cands = [o for k in (a["issn"], a["eissn"]) if k for o in by_issn.get(k, [])]
            else:
                # renamed titles that also changed ISSN (e.g. "The Economics of Transition" ->
                # "Economics of Transition and Institutional Change"): one title key extends the other
                cands = [o for o in old if id(o) not in used
                         and min(len(nk), len(_abdc_title_key(o["title"]))) >= 15
                         and (nk.startswith(_abdc_title_key(o["title"])) or _abdc_title_key(o["title"]).startswith(nk))]
            for o in cands:
                if id(o) not in used:
                    used.add(id(o))
                    pairs[i] = o
                    break
    out = []
    n_up = n_down = n_new = 0
    for i, a in enumerate(new):
        o = pairs.get(i)
        r22 = o["rating"] if o else ""
        if not o:
            rec = "New inclusion"
            n_new += 1
        elif _RANK.get(a["rating"], 0) > _RANK.get(r22, 0):
            rec, n_up = "Upgrade", n_up + 1
        elif _RANK.get(a["rating"], 0) < _RANK.get(r22, 0):
            rec, n_down = "Downgrade", n_down + 1
        else:
            rec = "No change"
        out.append(dict(title=a["title"], publisher=a["publisher"], issn=a["issn"], eissn=a["eissn"],
                        for_code=a["for_code"], year=a["year"], r2022=r22, r2025=a["rating"], rec=rec,
                        removed=False))
    removed = [o for o in old if id(o) not in used]
    for o in removed:
        out.append(dict(title=o["title"], publisher=o["publisher"], issn=o["issn"], eissn=o["eissn"],
                        for_code=o["for_code"], year=o["year"], r2022=o["rating"], r2025="",
                        rec="Removed in 2025 review", removed=True))
    m = re.search(r"(\d{4})-v(\d+)-(\d{2})(\d{2})(\d{2})", os.path.basename(path))
    if m:
        mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][int(m.group(4)) - 1]
        version = f"{m.group(1)} ABDC Journal Quality List (final v{m.group(2)}, {int(m.group(3))} {mon} 20{m.group(5)})"
    else:
        version = "2025 ABDC Journal Quality List (final)"
    log(f"  FINAL list: {len(new):,} journals rated; vs 2022: {n_up} upgrades, {n_down} downgrades, "
        f"{n_new} new inclusions, {len(removed)} removed")
    return out, version


def load_abdc(path):
    log("ABDC:", os.path.basename(path))
    wb = openpyxl.load_workbook(path, read_only=True)
    if "2025 JQL" in wb.sheetnames and "2022 JQL" in wb.sheetnames and not any(
            "removed" in s.lower() for s in wb.sheetnames):
        res = _load_abdc_final(wb, path)
        wb.close()
        return res
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
        # edition year = the year in "Total Docs. (2025)"; the file name carries no year
        edition = next((m.group(1) for k in (rd.fieldnames or [])
                        for m in [re.match(r"total docs\. \((20\d\d)\)", k.strip().lower())] if m), None)
        out = {}
        for r in rd:
            r = {(k or "").strip(): (v or "").strip() for k, v in r.items()}
            sid = r.get("Sourceid") or r.get("Source ID") or ""
            # header is "Citations / Doc. (2years)" in the 2025 export, "Cites / Doc. (2years)" in 2023
            cites2 = next((v for k, v in r.items() if re.match(r"(citations|cites)\s*/\s*doc", k.lower())), "")
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
    if not edition:
        m = re.search(r"(20\d\d)", os.path.basename(path))
        edition = m.group(1) if m else None
    log(f"  {len(out):,} SCImago records, edition {edition or 'unknown'}")
    return out, (f"SJR {edition}" if edition else "SJR (edition unknown)")


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
        # The WoS column holds either "WoS" or "Not in Wos"; only the former is an indexing flag.
        # (Storing any non-empty text made every listed journal look WoS-indexed.)
        if c_wos is not None and clean(r[c_wos]).lower() in ("wos", "yes", "y", "indexed"):
            rec["wos"] = "WoS"

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


def legacy_citescore_year(path):
    """CiteScore year of a supplementary list that has no year in it. CiteScore Y is published in June of Y+1,
    so a file compiled before June holds CiteScore (year-2), one compiled from June on holds (year-1).
    Inferred from the file's modification date - reported as such in meta.json."""
    t = dt.datetime.fromtimestamp(os.path.getmtime(path))
    return t.year - 1 if t.month >= 6 else t.year - 2


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

    scopus, asjc_names, scopus_ver, scopus_metric_years = load_scopus(scopus_path)
    abdc, abdc_ver = load_abdc(abdc_path)
    sjr, sjr_ver = load_scimago(sjr_path)
    # The supplementary master list holds an old CiteScore and a WoS flag that cannot be re-validated against a
    # current source, so it is NOT used unless explicitly requested (--use-legacy-list). Only values traceable to
    # the ABDC list, the SCImago export and the Scopus Source List are published.
    if cs_path and not getattr(args, "use_legacy_list", False):
        log(f"Supplementary list {os.path.basename(cs_path)} found but NOT used (unvalidated CiteScore/WoS); "
            "pass --use-legacy-list to include it")
        cs_path = None
    cites = load_citescore(cs_path)
    sjr_year = (re.search(r"(20\d\d)", sjr_ver or "") or [None, None])[1]
    cs_legacy_year = legacy_citescore_year(cs_path) if cs_path else None
    cs_list_date = (dt.datetime.fromtimestamp(os.path.getmtime(cs_path)).strftime("%b %Y") if cs_path else None)

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
    # SCImago record for any ISSN (lets ABDC-only titles that are not in the Scopus list still pick up SJR)
    sjr_by_issn = {}
    for sid, s in sjr.items():
        for i in s["issns"]:
            sjr_by_issn.setdefault(i, sid)

    records = {}
    for sid, j in scopus.items():
        # only journals / trade journals are recommendable; book series enter only through an ABDC listing
        if j["active"] and not j["discontinued"] and j["stype"].lower() in ("journal", "trade journal"):
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
    collisions = 0

    def abdc_only_record(a):
        return records.setdefault("abdc:" + tkey(a["title"]) + a["issn"] + a["eissn"], dict(
            sid="", title=a["title"], issn=a["issn"], eissn=a["eissn"], active=False, coverage="",
            discontinued=False, oa=False, publisher=a["publisher"], asjc=[], lang="", stype="Journal",
            scopus=False, metrics={},
        ))

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
            rec = abdc_only_record(a)
            abdc_only += 1
        prev = rec.get("abdc")
        if prev is not None:
            if not prev["removed"] and a["removed"]:
                continue  # keep the listed rating over a removal note
            if not prev["removed"] and not a["removed"]:
                # two live ABDC rows resolved to one source record (a reused / shared ISSN): the row whose
                # title matches the source record keeps it, the other becomes a record of its own
                collisions += 1
                if tkey(a["title"]) != tkey(rec["title"]):
                    abdc_only_record(a)["abdc"] = a
                    abdc_only += 1
                    continue
                loser = abdc_only_record(prev)
                loser["abdc"] = prev
                abdc_only += 1
        rec["abdc"] = a
    log(f"Merged: {len(records):,} journals ({title_joined} ABDC joined by title; "
        f"{abdc_only:,} ABDC journals not found in Scopus list; {collisions} shared-ISSN collisions split)")

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
    used_scopus_cs = bool(scopus_metric_years.get("cs"))  # Scopus list carries its own CiteScore
    for key, r in records.items():
        s = oa_by_issn.get(r["issn"]) or oa_by_issn.get(r["eissn"])
        rec = {
            "t": r["title"],
            "p": r["publisher"] or "",
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
                rec["ab"][0] = "X"  # in the 2022 list, not in the 2025 list
        sj = sjr.get(r.get("sid") or "")
        if sj is None:  # title without a Scopus id (or id unknown to SCImago): fall back to ISSN
            sj = sjr.get(sjr_by_issn.get(r["issn"]) or sjr_by_issn.get(r["eissn"]) or "")
        if sj:
            rec["sjr"] = round(sj["sjr"], 3) if sj["sjr"] is not None else None
            rec["q"] = sj["q"]
            if sj["h"]:
                rec["h"] = sj["h"]  # SCImago h-index only; never mixed with the OpenAlex one (see "hx")
            if sj["cpd"] is not None:
                rec["cpd"] = round(sj["cpd"], 2)
            if sj["cats"]:
                rec["cat"] = sj["cats"][:6]
            if sj["country"]:
                rec["c"] = sj["country"]
        # CiteScore / percentile / SNIP: the Scopus Source List's own metric columns win when present,
        # otherwise the supplementary master list (older) fills in.
        sm = r.get("metrics") or {}
        cs = (cites.get(r.get("sid") or "") or
              cites.get("issn:" + r.get("issn", "")) or
              cites.get("issn:" + r.get("eissn", "")))
        if cs:
            if not used_scopus_cs:  # never mix the legacy CiteScore into a list that has the current one
                if cs.get("cs") is not None:
                    rec["cs"] = cs["cs"]
                if cs.get("pct") is not None:
                    rec["csp"] = cs["pct"]
                if cs.get("snip") is not None:
                    rec["snip"] = cs["snip"]
            if cs.get("wos"):
                rec["wos"] = cs["wos"]
        if sm:
            if sm.get("cs") is not None:
                rec["cs"] = round(sm["cs"], 1)
            if sm.get("pct") is not None:
                rec["csp"] = round(sm["pct"], 0)
            if sm.get("snip") is not None:
                rec["snip"] = round(sm["snip"], 3)
            if sm.get("sjr") is not None and "sjr" not in rec:
                rec["sjr"] = round(sm["sjr"], 3)
        if s:
            matched_oa += 1
            st = s.get("summary_stats") or {}
            rec["ox"] = short_id(s["id"])
            # OpenAlex feeds the scope profile only (topics + works count for the thin-profile guard) and the
            # journal's homepage link. Its citedness / h-index / APC / DOAJ / country values are NOT published:
            # they cannot be checked against the ABDC, SCImago or Scopus files.
            rec["w"] = s.get("works_count") or 0
            if s.get("homepage_url"):
                rec["url"] = s["homepage_url"]
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

    # Year/edition of every displayed metric, so the app can label values instead of calling them "latest".
    my = scopus_metric_years
    vintage = {
        "sjr": sjr_year, "q": sjr_year, "h": sjr_year, "cpd": sjr_year,
        "cs": (my.get("cs") or None) if used_scopus_cs else (str(cs_legacy_year) if cs_path and cs_legacy_year else None),
        "csp": (my.get("pct") or my.get("cs") or None) if used_scopus_cs else None,
        "snip": my.get("snip") or None,
        "wos": cs_list_date if cs_path else None,
        "abdc": "2025 final" if "final" in (abdc_ver or "").lower() else "2025 draft",
    }
    meta = {
        "built": dt.datetime.now().isoformat(timespec="minutes"),
        "sources": {
            "scopus": f"Scopus Source List ({scopus_ver})",
            "abdc": abdc_ver,
            "sjr": sjr_ver or "not loaded",
            "openalex": f"OpenAlex (fetched {dt.date.today().isoformat()})",
            "citescore": (f"Scopus Source List metrics ({my.get('cs') or 'year n/a'})" if used_scopus_cs else
                          (f"{os.path.basename(cs_path)} - CiteScore {cs_legacy_year} (inferred from file date "
                           f"{cs_list_date}); WoS flag as of that list" if cs_path else "not loaded")),
            "wos": "not loaded",
        },
        "vintage": vintage,
        "abdc_final": "final" in (abdc_ver or "").lower(),
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
    ap.add_argument("--use-legacy-list", action="store_true",
                    help="also use the supplementary *citescore*.xlsx (old CiteScore + WoS flag) - unvalidated, off by default")
    build(ap.parse_args())
