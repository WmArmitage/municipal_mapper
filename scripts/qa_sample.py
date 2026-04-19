from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db import get_connection


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="QA summary across a sample of municipalities.")
    parser.add_argument(
        "municipality_ids",
        nargs="+",
        help="One or more municipality IDs, e.g. ct_chester ct_haddam ct_essex",
    )
    parser.add_argument("--db", default=str(ROOT / "database" / "master.sqlite"))
    parser.add_argument(
        "--export-csv",
        action="store_true",
        help="Write one-row-per-town summary CSV to data/exports/qa_summary.csv",
    )
    parser.add_argument(
        "--csv-path",
        default=str(ROOT / "data" / "exports" / "qa_summary.csv"),
        help="Output CSV path (used with --export-csv).",
    )
    return parser.parse_args()


def get_base_counts(conn, municipality_id: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for table in ("pages", "contacts", "service_links", "locations", "signals"):
        count = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE municipality_id = ?",
            (municipality_id,),
        ).fetchone()[0]
        out[table] = int(count)
    return out


def get_contact_presence_counts(conn, municipality_id: str) -> dict[str, int]:
    row = conn.execute(
        """
        SELECT
            SUM(CASE WHEN email IS NOT NULL AND TRIM(email) <> '' THEN 1 ELSE 0 END) AS with_email,
            SUM(CASE WHEN phone IS NOT NULL AND TRIM(phone) <> '' THEN 1 ELSE 0 END) AS with_phone,
            SUM(CASE WHEN phone_ext IS NOT NULL AND TRIM(phone_ext) <> '' THEN 1 ELSE 0 END) AS with_phone_ext,
            SUM(CASE WHEN department IS NOT NULL AND TRIM(department) <> '' THEN 1 ELSE 0 END) AS with_department,
            SUM(CASE WHEN name IS NOT NULL AND TRIM(name) <> '' THEN 1 ELSE 0 END) AS with_name
        FROM contacts
        WHERE municipality_id = ?
        """,
        (municipality_id,),
    ).fetchone()
    return {
        "contact_with_email": int(row[0] or 0),
        "contact_with_phone": int(row[1] or 0),
        "contact_with_phone_ext": int(row[2] or 0),
        "contact_with_department": int(row[3] or 0),
        "contact_with_name": int(row[4] or 0),
    }


def get_service_category_counts(conn, municipality_id: str) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT category, COUNT(*) AS c
        FROM service_links
        WHERE municipality_id = ?
        GROUP BY category
        ORDER BY c DESC, category
        """,
        (municipality_id,),
    ).fetchall()
    return {str(row[0] or "unknown"): int(row[1]) for row in rows}


def get_signal_type_counts(conn, municipality_id: str) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT signal_type, COUNT(*) AS c
        FROM signals
        WHERE municipality_id = ?
        GROUP BY signal_type
        ORDER BY c DESC, signal_type
        """,
        (municipality_id,),
    ).fetchall()
    return {str(row[0] or "unknown"): int(row[1]) for row in rows}


def build_summary_row(conn, municipality_id: str) -> tuple[dict[str, int | str], dict[str, int], dict[str, int]]:
    base = get_base_counts(conn, municipality_id)
    contact_presence = get_contact_presence_counts(conn, municipality_id)
    service_categories = get_service_category_counts(conn, municipality_id)
    signal_types = get_signal_type_counts(conn, municipality_id)
    fallback = get_fallback_status(conn, municipality_id, base)

    row: dict[str, int | str] = {
        "municipality_id": municipality_id,
        "pages": base["pages"],
        "contacts": base["contacts"],
        "service_links": base["service_links"],
        "locations": base["locations"],
        "signals": base["signals"],
        "blocked_homepage": str(bool(fallback["blocked_homepage"])).lower(),
        "blocked_homepage_value": str(fallback["blocked_homepage_value"] or ""),
        "homepage_fetch_failed": str(bool(fallback["homepage_fetch_failed"])).lower(),
        "alternate_seed_attempted": str(bool(fallback["alternate_seed_attempted"])).lower(),
        "recovered_after_homepage_failure": str(bool(fallback["recovered_after_homepage_failure"])).lower(),
        **contact_presence,
    }
    return row, service_categories, signal_types


def print_summary(
    municipality_id: str,
    row: dict[str, int | str],
    service_categories: dict[str, int],
    signal_types: dict[str, int],
) -> None:
    print(f"\n=== {municipality_id} ===")
    print(
        json.dumps(
            {
                "counts": {
                    "pages": row["pages"],
                    "contacts": row["contacts"],
                    "service_links": row["service_links"],
                    "locations": row["locations"],
                    "signals": row["signals"],
                },
                "contact_presence": {
                    "email_present": row["contact_with_email"],
                    "phone_present": row["contact_with_phone"],
                    "phone_ext_present": row["contact_with_phone_ext"],
                    "department_present": row["contact_with_department"],
                    "name_present": row["contact_with_name"],
                },
                "service_links_by_category": service_categories,
                "signals_by_type": signal_types,
                "fallback_status": {
                    "blocked_homepage": row["blocked_homepage"],
                    "blocked_homepage_value": row["blocked_homepage_value"],
                    "homepage_fetch_failed": row["homepage_fetch_failed"],
                    "alternate_seed_attempted": row["alternate_seed_attempted"],
                    "recovered_after_homepage_failure": row["recovered_after_homepage_failure"],
                },
            },
            indent=2,
        )
    )


def write_csv(rows: list[dict[str, int | str]], csv_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "municipality_id",
        "pages",
        "contacts",
        "service_links",
        "locations",
        "signals",
        "blocked_homepage",
        "blocked_homepage_value",
        "homepage_fetch_failed",
        "alternate_seed_attempted",
        "recovered_after_homepage_failure",
        "contact_with_email",
        "contact_with_phone",
        "contact_with_phone_ext",
        "contact_with_department",
        "contact_with_name",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        print(f"\nWrote QA CSV: {csv_path}")


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


def main() -> None:
    args = parse_args()
    conn = get_connection(args.db)
    rows: list[dict[str, int | str]] = []
    try:
        for municipality_id in args.municipality_ids:
            row, service_categories, signal_types = build_summary_row(conn, municipality_id)
            rows.append(row)
            print_summary(municipality_id, row, service_categories, signal_types)
    finally:
        conn.close()

    if args.export_csv:
        write_csv(rows, Path(args.csv_path))


if __name__ == "__main__":
    main()
