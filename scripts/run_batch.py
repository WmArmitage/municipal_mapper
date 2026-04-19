from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import db
from scripts.run_town import crawl_single_municipality


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run crawler pipeline for all municipalities.")
    parser.add_argument("--force", action="store_true", help="Re-run municipalities that already have pages.")
    parser.add_argument("--limit", type=int, default=None, help="Optional max municipalities to process.")
    parser.add_argument("--max-candidate-pages", type=int, default=25)
    parser.add_argument("--db", default=str(ROOT / "database" / "master.sqlite"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    conn = db.get_connection(args.db)
    try:
        municipalities = db.list_municipalities(conn)
        processed = 0
        skipped = 0
        summaries: list[dict] = []

        for municipality in municipalities:
            municipality_id = municipality["municipality_id"]
            if not args.force and db.municipality_has_pages(conn, municipality_id):
                skipped += 1
                continue

            summary = crawl_single_municipality(
                conn=conn,
                municipality=municipality,
                raw_dir=ROOT / "data" / "raw",
                max_candidate_pages=args.max_candidate_pages,
            )
            summaries.append(summary)
            processed += 1
            print(f"Processed: {municipality_id}")

            if args.limit is not None and processed >= args.limit:
                break
    finally:
        conn.close()

    print("Batch crawl complete")
    print(json.dumps({"processed": processed, "skipped": skipped}, indent=2))
    if summaries:
        print("Sample summary:")
        print(json.dumps(summaries[0], indent=2))


if __name__ == "__main__":
    main()

