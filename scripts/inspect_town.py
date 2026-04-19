from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db import fetch_municipality_rows, get_connection, get_municipality, get_municipality_table_counts

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
        counts = get_municipality_table_counts(conn, args.municipality_id)
        payload = {
            "municipality_id": args.municipality_id,
            "municipality": get_municipality(conn, args.municipality_id),
            "counts": counts,
            "fallback_status": get_fallback_status(conn, args.municipality_id, counts),
            "tables": {},
        }
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


def get_signal_values(conn, municipality_id: str, signal_type: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT value
        FROM signals
        WHERE municipality_id = ? AND signal_type = ?
        """,
        (municipality_id, signal_type),
    ).fetchall()
    return [str(row[0] or "").strip().lower() for row in rows if row and row[0] is not None]


def get_fallback_status(conn, municipality_id: str, counts: dict[str, int]) -> dict[str, bool | str | None]:
    blocked_values = get_signal_values(conn, municipality_id, "blocked_homepage")
    homepage_statuses = get_signal_values(conn, municipality_id, "crawl_status")
    alt_attempt_values = get_signal_values(conn, municipality_id, "alternate_seed_attempted")
    has_data = any(counts.get(key, 0) > 0 for key in ("pages", "contacts", "service_links", "locations"))
    return {
        "blocked_homepage": bool(blocked_values),
        "blocked_homepage_value": blocked_values[0] if blocked_values else None,
        "homepage_fetch_failed": "homepage_fetch_failed" in homepage_statuses,
        "alternate_seed_attempted": any(value in {"true", "1", "yes", "attempted"} for value in alt_attempt_values),
        "recovered_after_homepage_failure": ("homepage_fetch_failed" in homepage_statuses) and has_data,
    }


if __name__ == "__main__":
    main()
