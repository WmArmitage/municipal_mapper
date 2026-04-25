# Targeted Deep Scrape Report

Date: 2026-04-24
Batch: `batch_non_civicplus_baseline`
Database: `database/non_civicplus_baseline.sqlite`
Targets: `ct_derby`, `ct_east_lyme`, `ct_fairfield`, `ct_stratford`, `ct_vernon`

## Run Notes

- Re-ran only the five target municipalities with the existing crawler.
- Used the same batch database and existing pipeline flow.
- Kept parsing, scoring, ranking, and postprocess logic unchanged.
- Re-ran:
  - `scripts/postprocess_batch.py`
  - `scripts/export_batch_qa.py`

## Before / After

| Municipality | Contacts Before | Contacts After | Winners Before | Winners After | Unresolved Before | Unresolved After | Result |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| ct_derby | 1 | 1 | 0 | 0 | 7 | 7 | No meaningful change |
| ct_east_lyme | 0 | 0 | 0 | 0 | 7 | 7 | No meaningful change |
| ct_fairfield | 50 | 56 | 5 | 6 | 2 | 1 | Improved |
| ct_stratford | 0 | 0 | 0 | 0 | 7 | 7 | No meaningful change |
| ct_vernon | 0 | 0 | 0 | 0 | 7 | 7 | No meaningful change |

`Unresolved` above uses `qa_missing_key_roles.csv` `missing_group_count`.

## Crawl Diagnostics After Rerun

- `ct_derby`: `ok`
- `ct_east_lyme`: `probable_js_shell`
- `ct_fairfield`: `ok`
- `ct_stratford`: `probable_js_shell`
- `ct_vernon`: `probable_js_shell`

## Detail

### ct_derby

- Pages in DB after rerun: `10`
- Contacts after rerun: `1`
- Winners after rerun: `0`
- Missing role groups after rerun: `executive; assessment; tax; clerk; building; planning; finance`

### ct_east_lyme

- Pages in DB after rerun: `12`
- Contacts after rerun: `0`
- Winners after rerun: `0`
- Missing role groups after rerun: `executive; assessment; tax; clerk; building; planning; finance`

### ct_fairfield

- Pages in DB before rerun: `34`
- Pages in DB after rerun: `44`
- Raw contacts after rerun: `169`
- Clean contacts after rerun: `56`
- Winners after rerun: `6`
- Missing role groups after rerun: `finance`
- Newly resolved role-group coverage: planning

### ct_stratford

- Pages in DB after rerun: `4`
- Contacts after rerun: `0`
- Winners after rerun: `0`
- Missing role groups after rerun: `executive; assessment; tax; clerk; building; planning; finance`

### ct_vernon

- Pages in DB after rerun: `4`
- Contacts after rerun: `0`
- Winners after rerun: `0`
- Missing role groups after rerun: `executive; assessment; tax; clerk; building; planning; finance`

## Outcome

- The targeted deep scrape produced a real improvement only for `ct_fairfield`.
- `ct_east_lyme`, `ct_stratford`, and `ct_vernon` remain constrained by JS-shell/challenge behavior.
- `ct_derby` remains crawlable but still has only one clean contact and no winners.
