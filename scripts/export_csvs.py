from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db import get_connection

PUBLIC_SIGNAL_EXCLUDE_TYPES = {
    "crawl_error",
    "crawl_status",
    "fetched_pages_count",
    "high_value_links_count",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export SQLite tables to CSVs.")
    parser.add_argument("--db", default=str(ROOT / "database" / "master.sqlite"))
    parser.add_argument(
        "--signals-view",
        choices=("full", "public", "both"),
        default="both",
        help="Export full debug signals, public-facing filtered signals, or both.",
    )
    parser.add_argument(
        "--manual-review",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Export manual-review municipality summary CSV.",
    )
    parser.add_argument(
        "--manual-review-path",
        default=str(ROOT / "data" / "exports" / "manual_review_towns.csv"),
        help="Output path for manual review municipality summary CSV.",
    )
    return parser.parse_args()


def export_query(conn, query: str, params: tuple, out_path: Path) -> int:
    rows = conn.execute(query, params).fetchall()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        if not rows:
            handle.write("")
            return 0
        fieldnames = rows[0].keys()
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row))
    return len(rows)


def export_table(conn, table_name: str, out_path: Path) -> int:
    return export_query(conn, f"SELECT * FROM {table_name}", tuple(), out_path)


def export_signals_full(conn, out_path: Path) -> int:
    return export_table(conn, "signals", out_path)


def export_signals_public(conn, out_path: Path) -> int:
    placeholders = ",".join("?" for _ in PUBLIC_SIGNAL_EXCLUDE_TYPES)
    query = f"""
        SELECT *
        FROM signals
        WHERE signal_type NOT IN ({placeholders})
    """
    params = tuple(sorted(PUBLIC_SIGNAL_EXCLUDE_TYPES))
    return export_query(conn, query, params, out_path)


def export_manual_review_towns(conn, out_path: Path) -> int:
    query = """
        WITH signal_rollup AS (
            SELECT
                municipality_id,
                MAX(CASE WHEN signal_type = 'blocked_homepage' THEN 1 ELSE 0 END) AS blocked_homepage,
                MAX(CASE WHEN signal_type = 'blocked_homepage' THEN value END) AS blocked_homepage_value,
                MAX(
                    CASE
                        WHEN signal_type = 'crawl_status' AND lower(trim(value)) = 'homepage_fetch_failed' THEN 1
                        ELSE 0
                    END
                ) AS homepage_fetch_failed,
                MAX(
                    CASE
                        WHEN signal_type = 'alternate_seed_attempted'
                            AND lower(trim(value)) IN ('true', '1', 'yes', 'attempted')
                        THEN 1
                        ELSE 0
                    END
                ) AS alternate_seed_attempted,
                MAX(
                    CASE
                        WHEN signal_type = 'alternate_seed_recovered'
                            AND lower(trim(value)) IN ('true', '1', 'yes')
                        THEN 1
                        ELSE 0
                    END
                ) AS alternate_seed_recovered
            FROM signals
            GROUP BY municipality_id
        )
        SELECT
            m.municipality_id,
            m.name,
            m.website_url,
            CASE WHEN COALESCE(sr.blocked_homepage, 0) = 1 THEN 'true' ELSE 'false' END AS blocked_homepage,
            COALESCE(sr.blocked_homepage_value, '') AS blocked_homepage_value,
            CASE WHEN COALESCE(sr.alternate_seed_attempted, 0) = 1 THEN 'true' ELSE 'false' END AS alternate_seed_attempted,
            CASE WHEN COALESCE(sr.alternate_seed_recovered, 0) = 1 THEN 'true' ELSE 'false' END AS alternate_seed_recovered,
            m.jobs_url,
            m.directory_url,
            m.assessor_url,
            m.tax_url
        FROM municipalities AS m
        LEFT JOIN signal_rollup AS sr
            ON sr.municipality_id = m.municipality_id
        WHERE
            COALESCE(sr.blocked_homepage, 0) = 1
            OR COALESCE(sr.homepage_fetch_failed, 0) = 1
            OR (
                COALESCE(sr.alternate_seed_attempted, 0) = 1
                AND COALESCE(sr.alternate_seed_recovered, 0) = 0
            )
        ORDER BY m.municipality_id
    """
    return export_query(conn, query, tuple(), out_path)


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    exports = ROOT / "data" / "exports"
    conn = get_connection(db_path)
    try:
        counts = {
            "contacts": export_table(conn, "contacts", exports / "contacts.csv"),
            "service_links": export_table(conn, "service_links", exports / "service_links.csv"),
            "locations": export_table(conn, "locations", exports / "locations.csv"),
        }
        if args.signals_view in {"full", "both"}:
            counts["signals_full"] = export_signals_full(conn, exports / "signals.csv")
        if args.signals_view in {"public", "both"}:
            counts["signals_public"] = export_signals_public(conn, exports / "signals_public.csv")
        if args.manual_review:
            counts["manual_review_towns"] = export_manual_review_towns(conn, Path(args.manual_review_path))
    finally:
        conn.close()

    for name, count in counts.items():
        print(f"Exported {name}: {count} rows")


if __name__ == "__main__":
    main()
