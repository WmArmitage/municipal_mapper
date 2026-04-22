from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import db
from src.batch_manifest import (
    ensure_batch_qa_scaffold,
    filter_rows_by_platform,
    load_seed_platform_map,
    load_selected_manifest_rows,
)
from scripts.blocked_recovery import run_blocked_recovery_pass
from scripts.run_town import crawl_single_municipality


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run crawler pipeline for all municipalities.")
    parser.add_argument("--force", action="store_true", help="Re-run municipalities that already have pages.")
    parser.add_argument("--limit", type=int, default=None, help="Optional max municipalities to process.")
    parser.add_argument("--max-candidate-pages", type=int, default=25)
    parser.add_argument(
        "--manifest",
        default=str(ROOT / "data" / "manifests" / "civicplus_manifest.csv"),
        help="Batch manifest CSV path.",
    )
    parser.add_argument("--batch-id", default=None, help="Optional manifest batch ID filter (e.g. batch_1).")
    parser.add_argument("--manifest-status", default="pending", help="Manifest status filter (default: pending).")
    parser.add_argument(
        "--platform",
        default=None,
        help="Optional platform filter from seed CSV, e.g. CivicPlus.",
    )
    parser.add_argument(
        "--seed-csv",
        default=str(ROOT / "config" / "municipalities_seed.csv"),
        help="Seed CSV containing municipality_id + platform columns.",
    )
    parser.add_argument(
        "--outputs-root",
        default=str(ROOT / "outputs" / "batches"),
        help="Base folder for batch QA outputs.",
    )
    parser.add_argument("--db", default=str(ROOT / "database" / "master.sqlite"))
    parser.add_argument(
        "--blocked-recovery",
        action="store_true",
        help="Run a conservative blocked-town recovery pass after normal crawling.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    conn = db.get_connection(args.db)
    try:
        platform_map = load_seed_platform_map(args.seed_csv)
        municipalities = db.list_municipalities(conn)
        if args.batch_id:
            selected_rows = load_selected_manifest_rows(
                manifest_path=args.manifest,
                batch_id=args.batch_id,
                status=args.manifest_status,
            )
            if args.platform:
                selected_rows, excluded_by_platform = filter_rows_by_platform(
                    selected_rows,
                    platform_map,
                    args.platform,
                )
                if excluded_by_platform:
                    print(
                        "Manifest municipalities excluded by platform filter; skipped: "
                        + ", ".join(excluded_by_platform)
                    )
            selected_ids = [row["municipality_id"] for row in selected_rows]
            if not selected_ids:
                raise SystemExit(
                    f"No municipalities selected from manifest for batch_id={args.batch_id}, status={args.manifest_status}"
                )

            municipality_lookup = {row["municipality_id"]: row for row in municipalities}
            missing_ids: list[str] = []
            filtered_municipalities: list[dict] = []
            for municipality_id in selected_ids:
                municipality = municipality_lookup.get(municipality_id)
                if municipality is None:
                    missing_ids.append(municipality_id)
                    continue
                filtered_municipalities.append(municipality)

            if missing_ids:
                print(
                    "Manifest municipalities missing from DB seed; skipped: "
                    + ", ".join(missing_ids)
                )
            municipalities = filtered_municipalities
            if not municipalities:
                raise SystemExit("Manifest selection left zero crawlable municipalities.")

            ensure_batch_qa_scaffold(args.outputs_root, args.batch_id)
            mode_label = (
                f"Manifest mode enabled: batch_id={args.batch_id}, status={args.manifest_status}, selected={len(municipalities)}"
            )
            if args.platform:
                mode_label += f", platform={args.platform}"
            print(mode_label)

        processed = 0
        skipped = 0
        summaries: list[dict] = []
        in_scope_municipalities: list[dict] = []
        blocked_recovery_rows: list[dict[str, object]] = []

        for municipality in municipalities:
            municipality_id = municipality["municipality_id"]
            crawl_municipality = dict(municipality)
            crawl_municipality["platform"] = platform_map.get(municipality_id, "")
            in_scope_municipalities.append(crawl_municipality)
            if not args.force and db.municipality_has_pages(conn, municipality_id):
                skipped += 1
                continue

            summary = crawl_single_municipality(
                conn=conn,
                municipality=crawl_municipality,
                raw_dir=ROOT / "data" / "raw",
                max_candidate_pages=args.max_candidate_pages,
            )
            summaries.append(summary)
            processed += 1
            print(f"Processed: {municipality_id}")

            if args.limit is not None and processed >= args.limit:
                break

        if args.blocked_recovery and in_scope_municipalities:
            blocked_recovery_rows = run_blocked_recovery_pass(
                conn=conn,
                municipalities=in_scope_municipalities,
                batch_id=args.batch_id or "",
            )
            print(
                "Blocked recovery attempted for "
                f"{len(blocked_recovery_rows)} blocked municipalities"
            )
    finally:
        conn.close()

    print("Batch crawl complete")
    print(
        json.dumps(
            {
                "processed": processed,
                "skipped": skipped,
                "blocked_recovery_attempted": len(blocked_recovery_rows),
            },
            indent=2,
        )
    )
    if summaries:
        print("Sample summary:")
        print(json.dumps(summaries[0], indent=2))


if __name__ == "__main__":
    main()
