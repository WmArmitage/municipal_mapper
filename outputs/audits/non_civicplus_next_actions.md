# Non-CivicPlus Crawl Completeness Audit

Date: 2026-04-24
Universe: 83 non-CivicPlus municipalities from `config/municipalities_seed.csv`

## Summary

- `ready_for_extraction_qa`: 30 towns
- `needs_deep_page_scrape`: 5 towns
- `blocked_or_403`: 9 towns
- `extraction_gap`: 5 towns
- `no_action_until_manual_review`: 34 towns

## Method Notes

- Preferred evidence source by town: `batch_revize_trace` for Revize manifest towns, `batch_granicus_vendor_probe` for Granicus manifest towns, otherwise `batch_non_civicplus_baseline`.
- `processed_in_db=yes` only when the town has actual trace rows in `database/revize_trace.sqlite`; this is expected to apply only to Revize towns.
- `pages_count` is populated only where a real page table count exists in `database/revize_trace.sqlite`. Non-Revize towns are left blank rather than inferring pages from homepage diagnostics.
- `contacts_count` and `winners_count` come from the chosen batch `qa_batch_summary.csv` using clean contacts and role winners.
- `blocked_403_count` counts all blocked incidents present in `outputs/batches/*/qa_blocked_towns.csv`, so repeated probe failures can produce counts greater than `1`.
- `needs_deep_page_scrape` means no crawl evidence, zero-contact unblocked crawl, or a superficial crawl (`<=2` contacts and zero winners).

## Coverage Findings

### Not Scraped At All

- None

### Scraped Only Superficially

- ct_derby (Derby)
- ct_east_lyme (East Lyme)
- ct_fairfield (Fairfield)
- ct_stratford (Stratford)
- ct_vernon (Vernon)

### Pages But Zero Contacts

- ct_east_lyme (East Lyme)

### Contacts But Zero Winners

- ct_derby (Derby)
- ct_fairfield (Fairfield)
- ct_groton (Groton)
- ct_naugatuck (Naugatuck)
- ct_new_london (New London)
- ct_stafford (Stafford)
- ct_watertown (Watertown)

### Blocked / 403 Towns

- ct_farmington (Farmington)
- ct_glastonbury (Glastonbury)
- ct_hartford (Hartford)
- ct_manchester (Manchester)
- ct_new_fairfield (New Fairfield)
- ct_new_haven (New Haven)
- ct_stamford (Stamford)
- ct_weston (Weston)
- ct_westport (Westport)

## Next Actions

1. Re-run or recover the `blocked_or_403` towns before any merge-readiness decision. All nine blocked towns remain homepage-blocked, and the Granicus probe shows no recovery progress.
2. Deep-scrape the five `needs_deep_page_scrape` towns first, because they do not yet have enough usable crawl coverage to separate crawl failure from extraction failure.
3. Investigate the five `extraction_gap` towns next; these have contact evidence but still produce zero winners, so crawl coverage exists but downstream extraction is not yielding mergeable outputs.
4. Keep the thirty-four `no_action_until_manual_review` towns out of merge preparation until suspicious winners are reviewed. Their crawl completed, but current outputs still require human validation.
5. The thirty `ready_for_extraction_qa` towns have enough crawl evidence to move into vendor-specific extraction QA without further crawl work.

## Vendor Distribution

- (blank): 9
- AWR Web Design: 2
- Apptegy: 2
- Aptuitiv: 1
- Catalis: 8
- Catalisgov: 5
- CivicLift: 10
- CoreBT: 1
- EvoGov: 3
- Evogov: 1
- Finalsite: 3
- Granicus: 9
- PurpleDog: 1
- Quasar: 1
- Quasar Internet Solutions: 1
- Quasar Internet Solutions, they use news page to post job listings: 1
- Revize: 14
- Town Web: 1
- TownWeb: 1
- Web Solutions: 3
- Wordpress: 2
- egovlink: 1
- finalsite: 2
- ifsight: 1

## Town Buckets

### Ready For Extraction QA

- ct_ansonia (Ansonia)
- ct_avon (Avon)
- ct_barkhamsted (Barkhamsted)
- ct_bethany (Bethany)
- ct_bloomfield (Bloomfield)
- ct_bolton (Bolton)
- ct_canaan (Canaan)
- ct_canton (Canton)
- ct_colebrook (Colebrook)
- ct_cornwall (Cornwall)
- ct_east_granby (East Granby)
- ct_ellington (Ellington)
- ct_killingly (Killingly)
- ct_marlborough (Marlborough)
- ct_meriden (Meriden)
- ct_monroe (Monroe)
- ct_morris (Morris)
- ct_new_britain (New Britain)
- ct_new_canaan (New Canaan)
- ct_new_milford (New Milford)
- ct_north_canaan (North Canaan)
- ct_plainfield (Plainfield)
- ct_ridgefield (Ridgefield)
- ct_shelton (Shelton)
- ct_somers (Somers)
- ct_southbury (Southbury)
- ct_southington (Southington)
- ct_wallingford (Wallingford)
- ct_waterbury (Waterbury)
- ct_woodbury (Woodbury)

### Manual Review Stop

- ct_berlin (Berlin)
- ct_bethel (Bethel)
- ct_bethlehem (Bethlehem)
- ct_bridgeport (Bridgeport)
- ct_chaplin (Chaplin)
- ct_durham (Durham)
- ct_east_haddam (East Haddam)
- ct_griswold (Griswold)
- ct_guilford (Guilford)
- ct_hampton (Hampton)
- ct_hartland (Hartland)
- ct_hebron (Hebron)
- ct_killingworth (Killingworth)
- ct_litchfield (Litchfield)
- ct_lyme (Lyme)
- ct_montville (Montville)
- ct_norfolk (Norfolk)
- ct_north_haven (North Haven)
- ct_plymouth (Plymouth)
- ct_portland (Portland)
- ct_prospect (Prospect)
- ct_putnam (Putnam)
- ct_redding (Redding)
- ct_salisbury (Salisbury)
- ct_scotland (Scotland)
- ct_seymour (Seymour)
- ct_sherman (Sherman)
- ct_sprague (Sprague)
- ct_suffield (Suffield)
- ct_thomaston (Thomaston)
- ct_union (Union)
- ct_west_hartford (West Hartford)
- ct_winchester (Winchester)
- ct_windsor_locks (Windsor Locks)

### Extraction Gap

- ct_groton (Groton)
- ct_naugatuck (Naugatuck)
- ct_new_london (New London)
- ct_stafford (Stafford)
- ct_watertown (Watertown)
