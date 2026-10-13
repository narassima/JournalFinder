# JournalScope — Manuscript Journal Finder

An intelligent academic journal recommendation web application built for researchers. Paste your title and abstract to receive ranked, justified journal recommendations based on:
- **Journal Scope & Aims**: Mapped via OpenAlex research topic taxonomy (~4,500 topics and journal publication shares).
- **ABDC Journal Quality List**: 2025 final-list ratings (`A*`, `A`, `B`, `C`), 2022 ratings, ANZSRC Field-of-Research codes, and derived change notes (upgrade / downgrade / new / removed).
- **Scopus Source List**: Active/inactive/discontinued status, ASJC subject classifications, open-access status. CiteScore / SNIP appear only when the Scopus *Source List with metrics* is supplied.
- **SCImago Journal Rank (SJR)**: SJR score, Best Quartile (`Q1`–`Q4`), H-index, citations per document and subject categories — edition year shown on every card.
- **Validated-only policy**: the app shows a value only if it traces to the ABDC list, the SCImago export or the Scopus Source List. Anything else (old CiteScore, WoS flag, OpenAlex citedness / h-index / APC) is not published. Each card links to the Scopus, SCImago and ABDC pages so users can check every number.

---

## Live Demo & Architecture

The web application is **100% static client-side HTML/CSS/JavaScript** located in [`docs/`](file:///C:/Users/narassima.s/.gemini/antigravity-ide/scratch/journal-finder/docs). It runs instantly in any browser without needing a backend server, making it directly hostable on **GitHub Pages**.

### Key Features
1. **Scope-based Matching**: Compares abstract TF-IDF vectors against 4,500 research topic profiles, matching against the percentage of works published by each journal in those topics.
2. **Prestige vs. Scope Balance**: Interactive slider lets you choose whether you want pure scope fit or higher emphasis on journal prestige (ABDC rank, SJR quartile, CiteScore).
3. **Structured Justifications**:
   - Scope alignment (% of journal publications matching your abstract topics).
   - Shared themes/keywords highlighted.
   - Quality metrics (SJR, Quartile, CiteScore, H-index, APC, Open Access).
   - ABDC ranking breakdown (2025 draft vs. 2022, FoR code, upgrade/downgrade notes).
   - Submission ladder: **Reach** (top-tier), **Target** (solid standing), and **Safe** options.
   - Cautions (e.g., Scopus discontinued titles, ABDC proposed removals, high APCs).
4. **Journal Lookup**: Instant search by journal name or ISSN with full metric cards.
5. **Shortlist & Export**: Star journals, export to CSV, or print a formatted justification report for co-authors.
6. **Optional AI Narrative**: Add your Google Gemini API key in Settings (stored only in browser `localStorage`) for an editorial review of your top matches.

---

## Quick Start (Local)

To run the application locally:

```bash
cd docs
python -m http.server 8080
```
Open **[http://localhost:8080](http://localhost:8080)** in your browser.

---

## Deploying to GitHub Pages (Free Hosting)

1. Create a new repository on GitHub (e.g. `journal-scope`).
2. Push the files in this directory to your repository:
   ```bash
   git init
   git add docs build data
   git commit -m "Initial JournalScope release"
   git remote add origin https://github.com/<your-username>/journal-scope.git
   git push -u origin main
   ```
3. In your GitHub repository:
   - Go to **Settings** → **Pages**.
   - Under **Build and deployment**, set:
     - **Source**: `Deploy from a branch`
     - **Branch**: `main`
     - **Folder**: `/docs`
   - Click **Save**.
4. Your site will be live at `https://<your-username>.github.io/journal-scope/` in ~1 minute!

---

## Updating or Rebuilding the Dataset

The pipeline script is in [`build/build_data.py`](file:///C:/Users/narassima.s/.gemini/antigravity-ide/scratch/journal-finder/build/build_data.py).

Raw files are placed in [`data/raw/`](file:///C:/Users/narassima.s/.gemini/antigravity-ide/scratch/journal-finder/data/raw/). The build finds each by file-name pattern and uses the newest match; older editions go in `data/raw/superseded/`.

| Slot | File-name pattern | What it must be | Source |
|---|---|---|---|
| ABDC | `*abdc*.xlsx` | The **final** list workbook (sheets `2025 JQL` + `2022 JQL`), e.g. `ABDC-JQL-2025-v3-210926.xlsx`. The draft layout (`Removed journals` sheet) is still read if supplied. | abdc.edu.au → ABDC Journal Quality List → Downloads |
| SCImago | `*scimago*.csv` | The `journalrank` export for the latest year (SJR, quartile, H-index, categories, citations/doc). The edition year is read from the `Total Docs. (YYYY)` column. | scimagojr.com → Journal Rankings → Download data |
| Scopus | `*scopus*source*.xlsx` | The **Source List with metrics** (CiteScore, highest percentile, SNIP, SJR) from `scopus.com/sources` → *Download Scopus Source List* (needs a Scopus sign-in). The title-list edition from elsevier.com is accepted too but has **no** CiteScore/SNIP. | scopus.com/sources |
| Legacy list (off) | `*citescore*.xlsx` | Old CiteScore + WoS flag. **Ignored by default** because it cannot be validated; enable with `--use-legacy-list` only if you accept an old edition (it is then labelled with its inferred year). | — |

Every metric shown in the app carries its edition year (taken from `meta.json` → `vintage`), so a stale file shows up as, e.g., "CiteScore 2021" rather than passing for current.

To rebuild the dataset:
```bash
# Rebuild using local files and cached OpenAlex data (fast, ~15 seconds):
python build/build_data.py --offline

# To fetch additional OpenAlex journal profiles online in polite mode:
python build/build_data.py
```
Outputs are written directly to `docs/data/`:
- `journals.json`: Compact JSON records for 31,000+ journals (`h` = SCImago h-index, `hx` = OpenAlex h-index, never mixed).
- `topics.json`: Topic taxonomy and keyword vocabulary.
- `asjc.json`: ASJC classification codes and subject areas.
- `meta.json`: Build metadata, timestamp, source counts, and the per-metric `vintage` (edition year) shown in the UI.
