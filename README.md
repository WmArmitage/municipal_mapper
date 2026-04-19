# Connecticut Municipal Website Intelligence Crawler (MVP)

Lightweight Python pipeline for crawling Connecticut municipal websites, extracting high-value service links and basic contact/location signals, storing results in SQLite, and exporting clean CSVs.

## Tech Stack
- Python 3.13+
- `requests`
- `beautifulsoup4`
- `sqlite3` (stdlib)

## Repo Layout

```text
config/
  municipalities_seed.csv
  patterns.yaml

data/
  raw/
  processed/
  exports/

database/
  schema.sql
  master.sqlite

scripts/
  init_db.py
  run_town.py
  run_batch.py
  export_csvs.py
  qa_counts.py
  inspect_town.py

src/
  http_client.py
  discover.py
  parsers.py
  vendors.py
  normalize.py
  db.py
```

## Setup

```powershell
python -m pip install requests beautifulsoup4
```

## CLI Workflow (MVP)

1. Initialize DB and load municipalities seed
```powershell
python scripts/init_db.py
```

2. Crawl one municipality
```powershell
python scripts/run_town.py ct_chester
```

3. Export CSVs
```powershell
python scripts/export_csvs.py
```

## QA / Review

Run one town and review quality before batch crawling:

```powershell
python scripts/init_db.py
python scripts/run_town.py ct_chester --qa
python scripts/qa_counts.py ct_chester
python scripts/inspect_town.py ct_chester --limit 20
python scripts/export_csvs.py
```

Quick checks:
- confirm contacts have email and/or phone
- inspect permit links for false positives
- verify locations are not duplicated by repeated footers
- verify vendor signals are not repeated with the same value

`run_town.py` also supports `--qa` to print post-run table counts for that municipality.

## Batch Crawl

```powershell
python scripts/run_batch.py
```

Optional flags:
- `--force` re-runs municipalities even if pages already exist
- `--limit N` process only N municipalities
- `--max-candidate-pages N` cap candidate pages per municipality

## Outputs
- SQLite DB: `database/master.sqlite`
- Raw fetched files: `data/raw/`
- Exported tables:
  - `data/exports/contacts.csv`
  - `data/exports/service_links.csv`
  - `data/exports/locations.csv`
  - `data/exports/signals.csv`

## MVP Notes
- Source URLs are preserved on extracted records.
- Contact phones are normalized to digits in `phone`; detected extensions are stored in `phone_ext`, and original matched text is stored in `source_context`.
- Uncertain location/title fields are stored as raw text.
- Contact extraction intentionally uses simple heuristics in v1 (no advanced person-name resolution).

## Parser Tests

```powershell
python -m unittest tests.test_parsers -v
```
