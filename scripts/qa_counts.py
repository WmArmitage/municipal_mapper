from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db import get_connection, get_municipality_table_counts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Print row counts by table for one municipality.")
    parser.add_argument("municipality_id", help="e.g. ct_chester")
    parser.add_argument("--db", default=str(ROOT / "database" / "master.sqlite"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    conn = get_connection(args.db)
    try:
        counts = get_municipality_table_counts(conn, args.municipality_id)
    finally:
        conn.close()
    print(json.dumps({"municipality_id": args.municipality_id, "counts": counts}, indent=2))


if __name__ == "__main__":
    main()

