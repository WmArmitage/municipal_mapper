from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db import fetch_municipality_rows, get_connection

TABLES = ("contacts", "service_links", "locations", "signals")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect extracted rows for one municipality.")
    parser.add_argument("municipality_id", help="e.g. ct_chester")
    parser.add_argument("--limit", type=int, default=None, help="Optional per-table row limit.")
    parser.add_argument("--db", default=str(ROOT / "database" / "master.sqlite"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    conn = get_connection(args.db)
    try:
        payload = {"municipality_id": args.municipality_id, "tables": {}}
        for table in TABLES:
            payload["tables"][table] = fetch_municipality_rows(
                conn=conn,
                municipality_id=args.municipality_id,
                table_name=table,
                limit=args.limit,
            )
    finally:
        conn.close()

    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()

