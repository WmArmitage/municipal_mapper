from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db import get_connection
from src.batch_manifest import (
    ensure_batch_qa_scaffold,
    load_manifest_rows,
    load_seed_platform_map,
)

KEY_ROLES = (
    "First Selectman",
    "Mayor",
    "Town Manager",
    "Assessor",
    "Tax Collector",
    "Town Clerk",
)

ROLE_WINNER_FIELDS = (
    "contact_id",
    "municipality_id",
    "entity_type",
    "name",
    "title",
    "role_normalized",
    "role_family",
    "department",
    "department_normalized",
    "email",
    "email_type",
    "phone",
    "phone_ext",
    "address",
    "hours",
    "page_type",
    "source_url",
    "display_confidence",
    "is_likely_noise",
    "rn",
)

SUSPICIOUS_FIELDS = (
    "municipality_id",
    "role_normalized",
    "name",
    "email",
    "phone",
    "department",
    "page_type",
    "source_url",
    "suspicious_reason",
)

MISSING_KEY_ROLE_FIELDS = (
    "municipality_id",
    "town_name",
    "missing_roles",
    "missing_count",
    "present_key_roles",
)

MANUAL_REVIEW_FIELDS = (
    "municipality_id",
    "town_name",
    "review_type",
    "role_normalized",
    "name",
    "email",
    "phone",
    "department",
    "page_type",
    "source_url",
    "missing_roles",
    "missing_count",
    "suspicious_reason",
)

SUMMARY_FIELDS = (
    "municipality_id",
    "town_name",
    "county",
    "raw_contacts",
    "clean_contacts",
    "role_winners",
    "service_links",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export batch QA CSVs for one manifest batch.")
    parser.add_argument("--batch-id", required=True, help="Batch ID to export (e.g. batch_1).")
    parser.add_argument(
        "--manifest",
        default=str(ROOT / "data" / "manifests" / "civicplus_manifest.csv"),
        help="Manifest CSV path.",
    )
    parser.add_argument(
        "--platform",
        default=None,
        help="Optional platform filter (e.g. CivicPlus) based on seed CSV platform column.",
    )
    parser.add_argument(
        "--seed-csv",
        default=str(ROOT / "config" / "municipalities_seed.csv"),
        help="Seed CSV path containing municipality_id + platform.",
    )
    parser.add_argument("--db", default=str(ROOT / "database" / "master.sqlite"), help="SQLite DB path.")
    parser.add_argument(
        "--outputs-root",
        default=str(ROOT / "outputs" / "batches"),
        help="Batch output base folder.",
    )
    return parser.parse_args()


def write_csv(path: Path, rows: list[dict], fieldnames: tuple[str, ...] | list[str]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames))
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})
    return len(rows)


def object_exists(conn, name: str, object_type: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = ? AND name = ? LIMIT 1",
        (object_type, name),
    ).fetchone()
    return row is not None


def placeholders(size: int) -> str:
    return ",".join("?" for _ in range(size))


def fetch_count_map(conn, object_name: str, municipality_ids: list[str]) -> dict[str, int]:
    if not municipality_ids:
        return {}
    sql = f"""
        SELECT municipality_id, COUNT(*) AS cnt
        FROM {object_name}
        WHERE municipality_id IN ({placeholders(len(municipality_ids))})
        GROUP BY municipality_id
    """
    rows = conn.execute(sql, tuple(municipality_ids)).fetchall()
    return {str(row["municipality_id"]): int(row["cnt"]) for row in rows}


def fetch_role_winners(conn, municipality_ids: list[str]) -> list[dict]:
    if not municipality_ids or not object_exists(conn, "vw_best_role_per_town", "view"):
        return []
    sql = f"""
        SELECT *
        FROM vw_best_role_per_town
        WHERE municipality_id IN ({placeholders(len(municipality_ids))})
        ORDER BY municipality_id, role_normalized
    """
    rows = conn.execute(sql, tuple(municipality_ids)).fetchall()
    return [dict(row) for row in rows]


def fetch_suspicious_winners(conn, municipality_ids: list[str]) -> list[dict]:
    if not municipality_ids or not object_exists(conn, "vw_best_role_per_town", "view"):
        return []
    sql = f"""
        SELECT
          municipality_id,
          role_normalized,
          name,
          email,
          phone,
          department,
          page_type,
          source_url,
          CASE
            WHEN email IS NULL OR TRIM(email) = '' THEN 'missing_email'
            WHEN LOWER(COALESCE(name, '')) LIKE '%assistant%' THEN 'assistant_name'
            WHEN role_normalized = 'Assessor' AND LOWER(COALESCE(source_url, '')) LIKE '%tax%' THEN 'assessor_tax_url_mismatch'
            WHEN role_normalized = 'Tax Collector' AND LOWER(COALESCE(source_url, '')) LIKE '%clerk%' THEN 'tax_collector_clerk_url_mismatch'
            WHEN role_normalized = 'Town Clerk' AND LOWER(COALESCE(source_url, '')) LIKE '%planning%' THEN 'town_clerk_planning_url_mismatch'
            ELSE 'review'
          END AS suspicious_reason
        FROM vw_best_role_per_town
        WHERE municipality_id IN ({placeholders(len(municipality_ids))})
          AND (
            email IS NULL OR TRIM(email) = ''
            OR LOWER(COALESCE(name, '')) LIKE '%assistant%'
            OR (role_normalized = 'Assessor' AND LOWER(COALESCE(source_url, '')) LIKE '%tax%')
            OR (role_normalized = 'Tax Collector' AND LOWER(COALESCE(source_url, '')) LIKE '%clerk%')
            OR (role_normalized = 'Town Clerk' AND LOWER(COALESCE(source_url, '')) LIKE '%planning%')
          )
        ORDER BY municipality_id, role_normalized
    """
    rows = conn.execute(sql, tuple(municipality_ids)).fetchall()
    return [dict(row) for row in rows]


def build_missing_key_roles(
    municipalities: list[dict[str, str]],
    winners: list[dict],
) -> list[dict[str, str | int]]:
    role_map: dict[str, set[str]] = {}
    for row in winners:
        municipality_id = str(row.get("municipality_id") or "")
        role = str(row.get("role_normalized") or "")
        if not municipality_id or not role:
            continue
        role_map.setdefault(municipality_id, set()).add(role)

    out: list[dict[str, str | int]] = []
    for municipality in municipalities:
        municipality_id = municipality["municipality_id"]
        present = role_map.get(municipality_id, set())
        missing = [role for role in KEY_ROLES if role not in present]
        if not missing:
            continue
        present_key = [role for role in KEY_ROLES if role in present]
        out.append(
            {
                "municipality_id": municipality_id,
                "town_name": municipality["town_name"],
                "missing_roles": "; ".join(missing),
                "missing_count": len(missing),
                "present_key_roles": "; ".join(present_key),
            }
        )
    return out


def build_manual_review_rows(
    municipalities: list[dict[str, str]],
    suspicious_rows: list[dict],
    missing_role_rows: list[dict[str, str | int]],
) -> list[dict[str, str | int]]:
    town_name_by_id = {row["municipality_id"]: row["town_name"] for row in municipalities}
    out: list[dict[str, str | int]] = []

    for row in suspicious_rows:
        municipality_id = str(row.get("municipality_id") or "")
        out.append(
            {
                "municipality_id": municipality_id,
                "town_name": town_name_by_id.get(municipality_id, ""),
                "review_type": "suspicious_winner",
                "role_normalized": row.get("role_normalized"),
                "name": row.get("name"),
                "email": row.get("email"),
                "phone": row.get("phone"),
                "department": row.get("department"),
                "page_type": row.get("page_type"),
                "source_url": row.get("source_url"),
                "missing_roles": "",
                "missing_count": "",
                "suspicious_reason": row.get("suspicious_reason"),
            }
        )

    for row in missing_role_rows:
        missing_count = int(row.get("missing_count") or 0)
        if missing_count < 2:
            continue
        out.append(
            {
                "municipality_id": row["municipality_id"],
                "town_name": row["town_name"],
                "review_type": "missing_key_roles_2plus",
                "role_normalized": "",
                "name": "",
                "email": "",
                "phone": "",
                "department": "",
                "page_type": "",
                "source_url": "",
                "missing_roles": row["missing_roles"],
                "missing_count": missing_count,
                "suspicious_reason": "",
            }
        )

    out.sort(key=lambda r: (str(r["municipality_id"]), str(r["review_type"]), str(r["role_normalized"])))
    return out


def select_batch_municipalities(
    manifest_path: str | Path,
    batch_id: str,
    platform: str | None,
    seed_csv_path: str | Path,
) -> list[dict[str, str]]:
    rows = load_manifest_rows(manifest_path)
    selected = [row for row in rows if row["batch_id"].strip().lower() == batch_id.strip().lower()]
    if not selected:
        raise SystemExit(f"No rows found in manifest for batch_id={batch_id}")

    if platform:
        platform_map = load_seed_platform_map(seed_csv_path)
        wanted = platform.strip().lower()
        selected = [
            row
            for row in selected
            if (platform_map.get(row["municipality_id"]) or "").strip().lower() == wanted
        ]
        if not selected:
            raise SystemExit(f"No rows left after platform filter: {platform}")
    return selected


def main() -> None:
    args = parse_args()
    municipalities = select_batch_municipalities(
        manifest_path=args.manifest,
        batch_id=args.batch_id,
        platform=args.platform,
        seed_csv_path=args.seed_csv,
    )
    municipality_ids = [row["municipality_id"] for row in municipalities]

    batch_paths = ensure_batch_qa_scaffold(args.outputs_root, args.batch_id)
    batch_dir = Path(args.outputs_root) / args.batch_id

    conn = get_connection(args.db)
    try:
        raw_counts = fetch_count_map(conn, "contacts", municipality_ids)
        clean_counts = (
            fetch_count_map(conn, "vw_contacts_clean", municipality_ids)
            if object_exists(conn, "vw_contacts_clean", "view")
            else {}
        )
        winner_counts = (
            fetch_count_map(conn, "vw_best_role_per_town", municipality_ids)
            if object_exists(conn, "vw_best_role_per_town", "view")
            else {}
        )
        service_counts = fetch_count_map(conn, "service_links", municipality_ids)

        role_winners = fetch_role_winners(conn, municipality_ids)
        suspicious_winners = fetch_suspicious_winners(conn, municipality_ids)
        missing_key_roles = build_missing_key_roles(municipalities, role_winners)
        manual_review_rows = build_manual_review_rows(municipalities, suspicious_winners, missing_key_roles)
    finally:
        conn.close()

    summary_rows: list[dict[str, str | int]] = []
    for row in municipalities:
        municipality_id = row["municipality_id"]
        summary_rows.append(
            {
                "municipality_id": municipality_id,
                "town_name": row["town_name"],
                "county": row["county"],
                "raw_contacts": raw_counts.get(municipality_id, 0),
                "clean_contacts": clean_counts.get(municipality_id, 0),
                "role_winners": winner_counts.get(municipality_id, 0),
                "service_links": service_counts.get(municipality_id, 0),
            }
        )

    counts = {
        "qa_batch_summary.csv": write_csv(
            batch_dir / "qa_batch_summary.csv",
            summary_rows,
            SUMMARY_FIELDS,
        ),
        "qa_role_winners.csv": write_csv(
            batch_dir / "qa_role_winners.csv",
            role_winners,
            ROLE_WINNER_FIELDS,
        ),
        "qa_suspicious_winners.csv": write_csv(
            batch_dir / "qa_suspicious_winners.csv",
            suspicious_winners,
            SUSPICIOUS_FIELDS,
        ),
        "qa_missing_key_roles.csv": write_csv(
            batch_dir / "qa_missing_key_roles.csv",
            missing_key_roles,
            MISSING_KEY_ROLE_FIELDS,
        ),
        "qa_manual_review.csv": write_csv(
            batch_dir / "qa_manual_review.csv",
            manual_review_rows,
            MANUAL_REVIEW_FIELDS,
        ),
    }

    print(f"Batch QA export complete for {args.batch_id}")
    print(f"Municipalities in scope: {len(municipalities)}")
    for filename, count in counts.items():
        print(f"{filename}: {count} rows -> {batch_paths[filename]}")


if __name__ == "__main__":
    main()
