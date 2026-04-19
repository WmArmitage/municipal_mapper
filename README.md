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
- Uncertain location/title fields are stored as raw text.
- Contact extraction intentionally uses simple heuristics in v1 (no advanced person-name resolution).
