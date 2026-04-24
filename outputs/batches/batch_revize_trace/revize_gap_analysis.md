# Revize Gap Analysis

Date: 2026-04-24
Database: `database/revize_trace.sqlite`
Scope: missing-winner towns `ct_fairfield`, `ct_groton`, `ct_stafford`, `ct_watertown`

## ct_fairfield

- Contact count in `vw_contacts_clean`: `1`
- Contact profile:
  - `with_name=1`, `with_title=0`, `with_role_normalized=0`, `with_email=0`, `with_phone=0`, `with_department=0`
  - Sample row: `name=Your Link Name`, `page_type=department_page`, `source_url=https://fairfieldct.gov/how_do_i/contact_us/index.php`
- Candidate distribution in `vw_role_candidates_scored`:
  - `high_confidence_winner=0`
  - `candidate_for_review=0`
  - `disqualified=0`
  - No scored candidate rows exist for this town.
- Unresolved roles in `vw_unresolved_roles`:
  - None surfaced.
  - Interpretation: no candidate rows were generated, so unresolved-role diagnostics never had role candidates to analyze.
- Dominant failure reason:
  - Structural extraction failure. The crawl produced a single navigation artifact and no usable contact fields.
- Root cause classification:
  - `no_candidates`
- Recommended fix category:
  - `extraction_gap`

## ct_groton

- Contact count in `vw_contacts_clean`: `32`
- Contact profile:
  - `with_name=25`, `with_title=26`, `with_role_normalized=0`, `with_role_family=2`, `with_email=10`, `with_phone=7`, `with_department=28`
  - All contacts are from `department_page` sources.
  - Representative rows:
    - `name=Delia Morrison`, `department=Director Of Finance`, `email=dmorrison@groton-ct.gov`, `phone=8604416690`
    - `name=Melissa McGuire`, `department=Tax Collection`, `email=taxcollector@groton-ct.gov`, `phone=8604416670`
    - `name=Noah Fellman`, `title=Department Contact`, `department=Gis`, `email=it_helpdesk@groton-ct.gov`, `phone=8604416691`
- Candidate distribution in `vw_role_candidates_scored`:
  - `high_confidence_winner=0`
  - `candidate_for_review=0`
  - `disqualified=0`
  - No scored candidate rows exist for this town.
- Unresolved roles in `vw_unresolved_roles`:
  - None surfaced.
  - Interpretation: there are many extracted contacts, but none were promoted into scored role candidates.
- Dominant failure reason:
  - No candidates. Contact extraction succeeded at a basic level, but role reconstruction/normalization did not convert these department-page contacts into candidate rows.
- Root cause classification:
  - `no_candidates`
- Recommended fix category:
  - `reconstruction_gap`

## ct_stafford

- Contact count in `vw_contacts_clean`: `46`
- Contact profile:
  - `with_name=38`, `with_title=45`, `with_role_normalized=1`, `with_role_family=1`, `with_email=5`, `with_phone=8`, `with_department=39`
  - Most contacts come from `https://www.staffordct.org/departments/first_selectman.php` and are page-content artifacts rather than clean staff records.
  - Representative rows:
    - `name=Filling Vacancies`, `title=Filling Vacancies for Town Clerk and Tax Collector`, `role_normalized=Tax Collector`, `department=Of The First Selectman`
    - `title=Department Contact`, `department=Legal Notices`, `email=clerk@staffordct.org`, `phone=8606841765`
- Candidate distribution in `vw_role_candidates_scored`:
  - `high_confidence_winner=0`
  - `candidate_for_review=0`
  - `disqualified=1`
  - Only scored row:
    - `role_normalized=Tax Collector`
    - `candidate_state=disqualified`
    - `winner_disqualifier_reason=artifact_name`
    - `suspicious_reason=invalid_person_name`
- Unresolved roles in `vw_unresolved_roles`:
  - `Tax Collector`
  - `top_candidate_winner_block_reason=artifact_name`
  - `unresolved_reason=no_person_contact_available`
- Dominant failure reason:
  - All candidates disqualified. The only candidate is a page-heading artifact, and no person-level replacement candidate exists.
- Root cause classification:
  - `all_candidates_disqualified`
- Recommended fix category:
  - `extraction_gap`

## ct_watertown

- Contact count in `vw_contacts_clean`: `17`
- Contact profile:
  - `with_name=15`, `with_title=2`, `with_role_normalized=0`, `with_email=1`, `with_phone=15`, `with_department=2`
  - `15` of `17` contacts come from `https://www.watertownct.org/departments/index.php`.
  - Representative rows:
    - `name=Building`, `phone=8609455264`, `page_type=staff_directory`
    - `name=Land Use`, `phone=8609455266`, `page_type=staff_directory`
    - `title=Department Contact`, `department=Finance`, `phone=8609455259`
    - `title=Test Profession`, `department=Test`, `email=test@test.com`
- Candidate distribution in `vw_role_candidates_scored`:
  - `high_confidence_winner=0`
  - `candidate_for_review=0`
  - `disqualified=0`
  - No scored candidate rows exist for this town.
- Unresolved roles in `vw_unresolved_roles`:
  - None surfaced.
  - Interpretation: extracted records are department/phone directory entries, not role candidates.
- Dominant failure reason:
  - Structural extraction failure. The extracted records are mostly department labels with phone numbers and one obvious test artifact, with no person-role candidates to score.
- Root cause classification:
  - `structural_extraction_failure`
- Recommended fix category:
  - `extraction_gap`

## Summary

- `ct_fairfield`: extraction collapsed to a single navigation artifact; no candidate pipeline activity.
- `ct_groton`: usable contact records exist, but role reconstruction never produced candidate rows.
- `ct_stafford`: one role candidate exists, but it is an artifact and is correctly disqualified.
- `ct_watertown`: extraction is dominated by department directory labels and test data, so no candidate rows are formed.
