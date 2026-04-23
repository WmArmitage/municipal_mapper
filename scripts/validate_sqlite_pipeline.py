from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.batch_manifest import load_manifest_rows, load_seed_platform_map

REQUIRED_BASE_TABLES = (
    "contacts",
    "locations",
    "municipalities",
    "pages",
    "service_links",
    "signals",
)
REQUIRED_POSTPROCESS_VIEWS = ("vw_contacts_clean", "vw_best_role_per_town")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate SQLite pipeline stage objects and counts.")
    parser.add_argument("--db", default=str(ROOT / "database" / "master.sqlite"), help="SQLite DB path.")
    parser.add_argument("--batch-id", default=None, help="Optional manifest batch ID scope filter.")
    parser.add_argument(
        "--manifest",
        default=str(ROOT / "data" / "manifests" / "civicplus_manifest.csv"),
        help="Manifest CSV path used when --batch-id is provided.",
    )
    parser.add_argument(
        "--platform",
        default=None,
        help="Optional platform filter when --batch-id is provided.",
    )
    parser.add_argument(
        "--seed-csv",
        default=str(ROOT / "config" / "municipalities_seed.csv"),
        help="Seed CSV path containing municipality_id + platform for platform filter.",
    )
    parser.add_argument(
        "--allow-missing-postprocess",
        action="store_true",
        help="Do not fail when postprocess views are missing.",
    )
    return parser.parse_args()


def object_exists(conn: sqlite3.Connection, name: str, object_type: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = ? AND name = ? LIMIT 1",
        (object_type, name),
    ).fetchone()
    return row is not None


def placeholders(size: int) -> str:
    return ",".join("?" for _ in range(size))


def scoped_ids(
    manifest_path: str | Path,
    batch_id: str | None,
    platform: str | None,
    seed_csv_path: str | Path,
) -> list[str]:
    if not batch_id:
        return []
    rows = load_manifest_rows(manifest_path)
    selected = [row for row in rows if row["batch_id"].strip().lower() == batch_id.strip().lower()]
    if platform:
        platform_map = load_seed_platform_map(seed_csv_path)
        wanted = platform.strip().lower()
        selected = [
            row
            for row in selected
            if (platform_map.get(row["municipality_id"]) or "").strip().lower() == wanted
        ]
    return [row["municipality_id"] for row in selected]


def count_rows(conn: sqlite3.Connection, object_name: str, municipality_ids: list[str]) -> int:
    if municipality_ids:
        where_in = placeholders(len(municipality_ids))
        row = conn.execute(
            f"SELECT COUNT(*) FROM {object_name} WHERE municipality_id IN ({where_in})",
            tuple(municipality_ids),
        ).fetchone()
        return int(row[0] if row else 0)
    row = conn.execute(f"SELECT COUNT(*) FROM {object_name}").fetchone()
    return int(row[0] if row else 0)


def main() -> None:
    args = parse_args()
    municipality_ids = scoped_ids(
        manifest_path=args.manifest,
        batch_id=args.batch_id,
        platform=args.platform,
        seed_csv_path=args.seed_csv,
    )

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        missing_base = [
            table_name
            for table_name in REQUIRED_BASE_TABLES
            if not object_exists(conn, table_name, "table")
        ]
        if missing_base:
            raise SystemExit("Missing required base tables: " + ", ".join(missing_base))

        missing_postprocess = [
            view_name
            for view_name in REQUIRED_POSTPROCESS_VIEWS
            if not object_exists(conn, view_name, "view")
        ]
        if missing_postprocess and not args.allow_missing_postprocess:
            raise SystemExit(
                "Missing required postprocess views: "
                + ", ".join(missing_postprocess)
                + ". Run scripts/postprocess_batch.py."
            )

        contact_candidates_exists = object_exists(conn, "contact_candidates", "table")
        raw_contacts = count_rows(conn, "contacts", municipality_ids)
        clean_contacts = (
            count_rows(conn, "vw_contacts_clean", municipality_ids)
            if object_exists(conn, "vw_contacts_clean", "view")
            else 0
        )
        role_winners = (
            count_rows(conn, "vw_best_role_per_town", municipality_ids)
            if object_exists(conn, "vw_best_role_per_town", "view")
            else 0
        )
    finally:
        conn.close()

    scope_label = "scoped" if municipality_ids else "global"
    print("SQLite pipeline validation")
    print(f"DB: {args.db}")
    print(f"Scope: {scope_label}")
    if municipality_ids:
        print(f"Municipalities in scope: {len(municipality_ids)}")
    print("Required base tables present: yes")
    print(
        "Required postprocess views present: "
        + ("yes" if not missing_postprocess else "no (" + ", ".join(missing_postprocess) + ")")
    )
    print(f"contact_candidates table present (optional): {'yes' if contact_candidates_exists else 'no'}")
    print("Note: SQLite pipeline writes staging directly to contacts; contact_candidates is optional/legacy.")
    print(f"raw_contacts: {raw_contacts}")
    print(f"clean_contacts: {clean_contacts}")
    print(f"role_winners: {role_winners}")


if __name__ == "__main__":
    main()
