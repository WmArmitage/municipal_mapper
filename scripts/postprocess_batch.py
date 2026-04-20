from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.batch_manifest import load_manifest_rows, load_seed_platform_map


FALLBACK_VW_CONTACTS_CLEAN_SQL = """
CREATE VIEW vw_contacts_clean AS
SELECT
    c.contact_id,
    c.municipality_id,
    c.entity_type,
    c.name,
    c.title,
    c.role_normalized,
    c.role_family,
    c.department,
    c.department_normalized,
    c.email,
    c.email_type,
    c.phone,
    c.phone_ext,
    c.address,
    c.hours,
    c.page_type,
    c.source_url,
    c.display_confidence
FROM contacts c
WHERE COALESCE(c.record_rank, 1) = 1
  AND (
      COALESCE(c.is_likely_noise, 0) = 0
      OR NOT EXISTS (
          SELECT 1
          FROM contacts c2
          WHERE COALESCE(c2.dedupe_key, '') = COALESCE(c.dedupe_key, '')
            AND COALESCE(c2.is_likely_noise, 0) = 0
      )
  )
""".strip()


FALLBACK_VW_BEST_ROLE_PER_TOWN_SQL = """
CREATE VIEW vw_best_role_per_town AS
WITH ranked AS (
  SELECT
    v.contact_id,
    v.municipality_id,
    v.entity_type,
    v.name,
    v.title,
    v.role_normalized,
    v.role_family,
    v.department,
    v.department_normalized,
    v.email,
    v.email_type,
    v.phone,
    v.phone_ext,
    v.address,
    v.hours,
    v.page_type,
    v.source_url,
    v.display_confidence,
    c.is_likely_noise,
    c.suspicious_reason,
    ROW_NUMBER() OVER (
      PARTITION BY v.municipality_id, v.role_normalized
      ORDER BY
        CASE WHEN v.email IS NOT NULL AND TRIM(v.email) <> '' THEN 1 ELSE 0 END DESC,
        CASE
          WHEN v.role_normalized = 'Assessor'
               AND (
                   LOWER(COALESCE(v.source_url, '')) LIKE '%tax%'
                   OR LOWER(COALESCE(v.email, '')) LIKE '%tax%'
               )
          THEN 1
          WHEN v.role_normalized = 'Tax Collector'
               AND (
                   LOWER(COALESCE(v.source_url, '')) LIKE '%clerk%'
                   OR LOWER(COALESCE(v.source_url, '')) LIKE '%planning%'
               )
          THEN 1
          WHEN v.role_normalized = 'Town Clerk'
               AND LOWER(COALESCE(v.source_url, '')) LIKE '%planning%'
          THEN 1
          WHEN v.role_normalized = 'First Selectman'
               AND (
                   LOWER(COALESCE(v.source_url, '')) LIKE '%finance%'
                   OR LOWER(COALESCE(v.source_url, '')) LIKE '%building%'
               )
          THEN 1
          ELSE 0
        END ASC,
        CASE
          WHEN (v.email IS NULL OR TRIM(v.email) = '')
               AND LOWER(COALESCE(v.source_url, '')) LIKE '%directory.aspx%'
          THEN 1
          ELSE 0
        END ASC,
        CASE
          WHEN v.role_normalized = 'Tax Collector'
               AND (
                   LOWER(COALESCE(v.source_url, '')) LIKE '%tax-collector%'
                   OR LOWER(COALESCE(v.source_url, '')) LIKE '%/tax%'
               )
          THEN 2
          WHEN v.role_normalized = 'Assessor'
               AND LOWER(COALESCE(v.source_url, '')) LIKE '%assessor%'
          THEN 2
          WHEN v.role_normalized = 'Town Clerk'
               AND LOWER(COALESCE(v.source_url, '')) LIKE '%town-clerk%'
          THEN 2
          WHEN v.role_normalized = 'Building Official'
               AND LOWER(COALESCE(v.source_url, '')) LIKE '%building%'
          THEN 2
          WHEN v.role_normalized IN ('Planner', 'Land Use')
               AND (
                   LOWER(COALESCE(v.source_url, '')) LIKE '%planning%'
                   OR LOWER(COALESCE(v.source_url, '')) LIKE '%land-use%'
               )
          THEN 2
          WHEN v.role_normalized IN ('Finance Director', 'Treasurer')
               AND LOWER(COALESCE(v.source_url, '')) LIKE '%finance%'
          THEN 2
          WHEN v.role_normalized = 'First Selectman'
               AND (
                   LOWER(COALESCE(v.source_url, '')) LIKE '%first-selectman%'
                   OR LOWER(COALESCE(v.source_url, '')) LIKE '%board-of-selectmen%'
                   OR LOWER(COALESCE(v.source_url, '')) LIKE '%selectman%'
               )
          THEN 2
          WHEN LOWER(COALESCE(v.source_url, '')) LIKE '%directory.aspx%'
          THEN 0
          ELSE 1
        END DESC,
        CASE
          WHEN v.role_normalized IN ('First Selectman', 'Mayor', 'Town Manager')
               AND (
                   LOWER(COALESCE(v.name, '')) LIKE '%assistant%'
                   OR LOWER(COALESCE(v.title, '')) LIKE '%assistant%'
                   OR LOWER(COALESCE(v.department, '')) LIKE '%assistant%'
                   OR LOWER(COALESCE(v.name, '')) LIKE '%admin assistant%'
                   OR LOWER(COALESCE(v.title, '')) LIKE '%admin assistant%'
                   OR LOWER(COALESCE(v.name, '')) LIKE '%administrative%'
                   OR LOWER(COALESCE(v.title, '')) LIKE '%administrative%'
                   OR LOWER(COALESCE(v.name, '')) LIKE '%executive assistant%'
                   OR LOWER(COALESCE(v.title, '')) LIKE '%executive assistant%'
               )
          THEN 1
          ELSE 0
        END ASC,
        CASE WHEN v.phone IS NOT NULL AND TRIM(v.phone) <> '' THEN 1 ELSE 0 END DESC,
        CASE WHEN v.name IS NOT NULL AND TRIM(v.name) <> '' THEN 1 ELSE 0 END DESC,
        CASE WHEN c.is_likely_noise = 0 THEN 1 ELSE 0 END DESC,
        v.display_confidence DESC
    ) AS rn
  FROM vw_contacts_clean v
  JOIN contacts c
    ON v.contact_id = c.contact_id
  WHERE v.role_normalized IS NOT NULL
)
SELECT *
FROM ranked
WHERE rn = 1
""".strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run post-processing enrichment for one manifest batch.")
    parser.add_argument("--batch-id", required=True, help="Batch ID, e.g. batch_1")
    parser.add_argument(
        "--manifest",
        default=str(ROOT / "data" / "manifests" / "civicplus_manifest.csv"),
        help="Manifest CSV path.",
    )
    parser.add_argument(
        "--platform",
        default=None,
        help="Optional platform filter (e.g. CivicPlus) based on municipalities_seed.csv platform column.",
    )
    parser.add_argument(
        "--seed-csv",
        default=str(ROOT / "config" / "municipalities_seed.csv"),
        help="Seed CSV path containing municipality_id + platform columns.",
    )
    parser.add_argument("--db", default=str(ROOT / "database" / "master.sqlite"), help="SQLite DB path.")
    return parser.parse_args()


def placeholders(size: int) -> str:
    return ",".join("?" for _ in range(size))


def select_batch_municipality_ids(
    manifest_path: str | Path,
    batch_id: str,
    platform: str | None,
    seed_csv_path: str | Path,
) -> list[str]:
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
    municipality_ids = [row["municipality_id"] for row in selected]
    if not municipality_ids:
        raise SystemExit("No municipalities selected for post-processing.")
    return municipality_ids


def view_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'view' AND name = ? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def get_view_sql(conn: sqlite3.Connection, name: str) -> str | None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'view' AND name = ?",
        (name,),
    ).fetchone()
    if not row:
        return None
    sql = row[0]
    return str(sql).strip() if sql else None


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, column_type: str) -> None:
    existing = {
        str(row["name"]).strip().lower()
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name.strip().lower() in existing:
        return
    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")


def ensure_hygiene_columns(conn: sqlite3.Connection) -> None:
    _ensure_column(conn, "contacts", "suspicious_reason", "TEXT")


def count_metrics(conn: sqlite3.Connection, municipality_ids: list[str]) -> dict[str, int]:
    params = tuple(municipality_ids)
    where_in = placeholders(len(municipality_ids))

    metrics = {
        "raw_contacts": conn.execute(
            f"SELECT COUNT(*) FROM contacts WHERE municipality_id IN ({where_in})",
            params,
        ).fetchone()[0],
        "contacts_with_entity_type": conn.execute(
            f"""
            SELECT COUNT(*)
            FROM contacts
            WHERE municipality_id IN ({where_in})
              AND NULLIF(TRIM(COALESCE(entity_type, '')), '') IS NOT NULL
            """,
            params,
        ).fetchone()[0],
        "contacts_with_role_normalized": conn.execute(
            f"""
            SELECT COUNT(*)
            FROM contacts
            WHERE municipality_id IN ({where_in})
              AND NULLIF(TRIM(COALESCE(role_normalized, '')), '') IS NOT NULL
            """,
            params,
        ).fetchone()[0],
        "rows_in_vw_contacts_clean": 0,
        "rows_in_vw_best_role_per_town": 0,
    }

    if view_exists(conn, "vw_contacts_clean"):
        metrics["rows_in_vw_contacts_clean"] = conn.execute(
            f"SELECT COUNT(*) FROM vw_contacts_clean WHERE municipality_id IN ({where_in})",
            params,
        ).fetchone()[0]
    if view_exists(conn, "vw_best_role_per_town"):
        metrics["rows_in_vw_best_role_per_town"] = conn.execute(
            f"SELECT COUNT(*) FROM vw_best_role_per_town WHERE municipality_id IN ({where_in})",
            params,
        ).fetchone()[0]
    return {key: int(value) for key, value in metrics.items()}


def run_batch_enrichment(conn: sqlite3.Connection, municipality_ids: list[str]) -> None:
    params = tuple(municipality_ids)
    where_in = placeholders(len(municipality_ids))
    where_contacts = f"municipality_id IN ({where_in})"
    where_services = f"municipality_id IN ({where_in})"

    conn.execute(
        f"""
        UPDATE contacts
        SET
            has_name = CASE WHEN NULLIF(TRIM(COALESCE(name, '')), '') IS NOT NULL THEN 1 ELSE 0 END,
            has_email = CASE WHEN NULLIF(TRIM(COALESCE(email, '')), '') IS NOT NULL THEN 1 ELSE 0 END,
            has_phone = CASE WHEN NULLIF(TRIM(COALESCE(phone, '')), '') IS NOT NULL THEN 1 ELSE 0 END,
            has_department = CASE WHEN NULLIF(TRIM(COALESCE(department, '')), '') IS NOT NULL THEN 1 ELSE 0 END
        WHERE {where_contacts}
        """,
        params,
    )

    conn.execute(
        f"""
        UPDATE contacts
        SET entity_type = CASE
            WHEN
                NULLIF(TRIM(COALESCE(name, '')), '') IS NOT NULL
                AND LOWER(TRIM(COALESCE(name, ''))) NOT LIKE '%email%'
                AND LOWER(TRIM(COALESCE(name, ''))) NOT LIKE '%contact%'
                AND LOWER(TRIM(COALESCE(name, ''))) NOT LIKE '%office%'
                AND LOWER(TRIM(COALESCE(name, ''))) NOT LIKE '%department%'
                AND LOWER(TRIM(COALESCE(name, ''))) NOT LIKE '%town hall%'
                AND LOWER(TRIM(COALESCE(name, ''))) NOT LIKE '%click here%'
                AND LOWER(TRIM(COALESCE(name, ''))) NOT LIKE '%phone%'
                AND LOWER(TRIM(COALESCE(name, ''))) NOT LIKE '%fax%'
                AND LOWER(TRIM(COALESCE(name, ''))) NOT LIKE '%hours%'
                AND LOWER(TRIM(COALESCE(name, ''))) NOT LIKE '%board of%'
                AND LOWER(TRIM(COALESCE(name, ''))) NOT LIKE '%commission%'
                AND LOWER(TRIM(COALESCE(name, ''))) NOT LIKE '%committee%'
            THEN 'person'
            WHEN
                (
                    NULLIF(TRIM(COALESCE(name, '')), '') IS NULL
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%email%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%contact%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%office%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%department%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%town hall%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%click here%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%phone%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%fax%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%hours%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%board of%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%commission%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%committee%'
                )
                AND NULLIF(TRIM(COALESCE(title, '')), '') IS NOT NULL
            THEN 'role'
            WHEN
                (
                    NULLIF(TRIM(COALESCE(name, '')), '') IS NULL
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%email%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%contact%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%office%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%department%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%town hall%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%click here%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%phone%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%fax%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%hours%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%board of%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%commission%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%committee%'
                )
                AND NULLIF(TRIM(COALESCE(title, '')), '') IS NULL
                AND NULLIF(TRIM(COALESCE(department, '')), '') IS NOT NULL
            THEN 'department_contact'
            ELSE 'unknown'
        END
        WHERE {where_contacts}
        """,
        params,
    )

    conn.execute(
        f"""
        UPDATE contacts
        SET is_role_only = CASE
            WHEN entity_type = 'role' THEN 1
            WHEN has_name = 0
                 AND (
                     NULLIF(TRIM(COALESCE(title, '')), '') IS NOT NULL
                     OR NULLIF(TRIM(COALESCE(department, '')), '') IS NOT NULL
                 )
            THEN 1
            ELSE 0
        END
        WHERE {where_contacts}
        """,
        params,
    )

    conn.execute(
        f"""
        UPDATE contacts
        SET is_likely_noise = CASE
            WHEN
                LOWER(COALESCE(name, '')) LIKE 'email %'
                OR LOWER(COALESCE(title, '')) LIKE 'email %'
                OR LOWER(COALESCE(department, '')) LIKE 'email %'
                OR LOWER(COALESCE(name, '')) LIKE '%click here%'
                OR LOWER(COALESCE(title, '')) LIKE '%click here%'
                OR LOWER(COALESCE(department, '')) LIKE '%click here%'
                OR LOWER(COALESCE(name, '')) LIKE '%title:%'
                OR LOWER(COALESCE(title, '')) LIKE '%title:%'
                OR LOWER(COALESCE(department, '')) LIKE '%title:%'
                OR (
                    (LOWER(COALESCE(name, '')) LIKE '%board%' OR LOWER(COALESCE(title, '')) LIKE '%board%')
                    AND (LOWER(COALESCE(name, '')) LIKE '%phone%' OR LOWER(COALESCE(title, '')) LIKE '%phone%')
                )
                OR (
                    (LOWER(COALESCE(name, '')) LIKE '%commission%' OR LOWER(COALESCE(title, '')) LIKE '%commission%')
                    AND (LOWER(COALESCE(name, '')) LIKE '%phone%' OR LOWER(COALESCE(title, '')) LIKE '%phone%')
                )
                OR (
                    (LOWER(COALESCE(name, '')) LIKE '%committee%' OR LOWER(COALESCE(title, '')) LIKE '%committee%')
                    AND (LOWER(COALESCE(name, '')) LIKE '%phone%' OR LOWER(COALESCE(title, '')) LIKE '%phone%')
                )
                OR LOWER(TRIM(COALESCE(name, ''))) LIKE 'the %'
                OR LOWER(COALESCE(name, '')) LIKE '%google maps%'
                OR LOWER(COALESCE(name, '')) LIKE '%requested%'
                OR LOWER(COALESCE(name, '')) LIKE '%hours%'
                OR LOWER(COALESCE(name, '')) LIKE '%office%'
                OR LOWER(COALESCE(name, '')) LIKE '%department%'
                OR LOWER(COALESCE(name, '')) LIKE '%click%'
                OR LOWER(COALESCE(name, '')) LIKE '%view%'
                OR LOWER(COALESCE(name, '')) LIKE '%faq%'
                OR LENGTH(TRIM(COALESCE(name, ''))) > 80
                OR (
                    NULLIF(TRIM(COALESCE(name, '')), '') IS NOT NULL
                    AND LOWER(TRIM(COALESCE(name, ''))) NOT GLOB '*[a-z]*'
                )
                OR (
                    NULLIF(TRIM(COALESCE(name, '')), '') IS NOT NULL
                    AND NULLIF(TRIM(COALESCE(title, '')), '') IS NOT NULL
                    AND LOWER(
                        TRIM(
                            REPLACE(
                                REPLACE(
                                    REPLACE(REPLACE(COALESCE(name, ''), '.', ''), ',', ''),
                                    '-',
                                    ' '
                                ),
                                '  ',
                                ' '
                            )
                        )
                    ) = LOWER(
                        TRIM(
                            REPLACE(
                                REPLACE(
                                    REPLACE(REPLACE(COALESCE(title, ''), '.', ''), ',', ''),
                                    '-',
                                    ' '
                                ),
                                '  ',
                                ' '
                            )
                        )
                    )
                )
            THEN 1
            ELSE 0
        END
        WHERE {where_contacts}
        """,
        params,
    )

    conn.execute(
        f"""
        UPDATE contacts
        SET page_type = CASE
            WHEN LOWER(COALESCE(source_url, '')) LIKE '%staff%'
                 OR LOWER(COALESCE(source_url, '')) LIKE '%directory%'
                 OR LOWER(COALESCE(source_url, '')) LIKE '%departments%'
                 OR LOWER(COALESCE(source_url, '')) LIKE '%directory.aspx%'
                 OR LOWER(COALESCE(source_context, '')) LIKE '%staff%'
                 OR LOWER(COALESCE(source_context, '')) LIKE '%directory%'
                 OR LOWER(COALESCE(source_context, '')) LIKE '%departments%'
            THEN 'staff_directory'
            WHEN LOWER(COALESCE(source_url, '')) LIKE '%assessor%'
                 OR LOWER(COALESCE(source_url, '')) LIKE '%tax%'
                 OR LOWER(COALESCE(source_url, '')) LIKE '%clerk%'
                 OR LOWER(COALESCE(source_url, '')) LIKE '%building%'
                 OR LOWER(COALESCE(source_url, '')) LIKE '%planning%'
                 OR LOWER(COALESCE(source_url, '')) LIKE '%zoning%'
            THEN 'department_page'
            WHEN
                NULLIF(TRIM(COALESCE(source_url, '')), '') IS NOT NULL
                AND (
                    (LENGTH(TRIM(COALESCE(source_url, ''))) - LENGTH(REPLACE(TRIM(COALESCE(source_url, '')), '/', ''))) <= 3
                    OR LOWER(TRIM(COALESCE(source_url, ''))) LIKE '%/index.%'
                    OR LOWER(TRIM(COALESCE(source_url, ''))) LIKE '%/home%'
                )
            THEN 'homepage'
            WHEN LOWER(COALESCE(source_url, '')) LIKE '%board%'
                 OR LOWER(COALESCE(source_url, '')) LIKE '%commission%'
                 OR LOWER(COALESCE(source_url, '')) LIKE '%committee%'
                 OR LOWER(COALESCE(source_context, '')) LIKE '%board%'
                 OR LOWER(COALESCE(source_context, '')) LIKE '%commission%'
                 OR LOWER(COALESCE(source_context, '')) LIKE '%committee%'
            THEN 'board_page'
            ELSE 'other'
        END
        WHERE {where_contacts}
        """,
        params,
    )

    conn.execute(
        f"""
        UPDATE contacts
        SET
            role_normalized = CASE
                WHEN LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%first selectman%'
                     OR LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%selectman%'
                THEN 'First Selectman'
                WHEN LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%mayor%'
                THEN 'Mayor'
                WHEN LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%town manager%'
                THEN 'Town Manager'
                WHEN LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%town administrator%'
                THEN 'Town Administrator'
                WHEN LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%assessor%'
                THEN 'Assessor'
                WHEN LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%tax collector%'
                THEN 'Tax Collector'
                WHEN LOWER(TRIM(COALESCE(title, ''))) IN (
                    'town and city clerk, registrar of vital statistics',
                    'town and city clerk',
                    'deputy town and city clerk',
                    'deputy town and city clerk, cctc',
                    'city clerk'
                )
                THEN 'Town Clerk'
                WHEN LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%town clerk%'
                THEN 'Town Clerk'
                WHEN LOWER(TRIM(COALESCE(title, ''))) IN (
                    'chief building official',
                    'acting building official',
                    'assistant building official'
                )
                THEN 'Building Official'
                WHEN LOWER(TRIM(COALESCE(title, ''))) IN (
                    'code enforcement officer',
                    'zoning/code enforcement officer',
                    'zoning/ code enforcement officer'
                )
                     AND LOWER(COALESCE(NULLIF(TRIM(department), ''), '')) LIKE '%building%'
                THEN 'Building Official'
                WHEN LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%building official%'
                     OR LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%building department%'
                     OR LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%building%'
                THEN 'Building Official'
                WHEN LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%land use%'
                THEN 'Land Use'
                WHEN LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%planner%'
                     OR LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%planning%'
                     OR LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%zoning%'
                THEN 'Planner'
                WHEN LOWER(TRIM(COALESCE(title, ''))) IN (
                    'director of finance',
                    'title: director of finance',
                    'assistant director of finance',
                    'assistant director of finance - budget & grants',
                    'assistant director of finance - operations',
                    'director of finance and revenue',
                    'director of finance and administration'
                )
                     OR LOWER(TRIM(COALESCE(title, ''))) LIKE 'assistant director of finance -%'
                     OR LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%finance director%'
                THEN 'Finance Director'
                WHEN LOWER(TRIM(COALESCE(title, ''))) IN (
                    'treasurer',
                    'town treasurer',
                    'city treasurer',
                    'borough treasurer'
                )
                     OR LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%treasurer%'
                THEN 'Treasurer'
                ELSE NULL
            END,
            role_family = CASE
                WHEN LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%first selectman%'
                     OR LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%selectman%'
                     OR LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%mayor%'
                     OR LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%town manager%'
                     OR LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%town administrator%'
                THEN 'chief_executive'
                WHEN LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%assessor%'
                THEN 'assessor'
                WHEN LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%tax collector%'
                THEN 'tax_collector'
                WHEN LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%town clerk%'
                THEN 'town_clerk'
                WHEN LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%building%'
                THEN 'building'
                WHEN LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%planner%'
                     OR LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%planning%'
                     OR LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%zoning%'
                     OR LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%land use%'
                THEN 'planning_zoning'
                WHEN LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%finance director%'
                     OR LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%treasurer%'
                     OR LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%finance%'
                THEN 'finance'
                ELSE NULL
            END
        WHERE {where_contacts}
        """,
        params,
    )

    conn.execute(
        f"""
        UPDATE contacts
        SET role_normalized = CASE
            WHEN LOWER(TRIM(COALESCE(title, ''))) IN (
                'director of finance',
                'title: director of finance',
                'assistant director of finance',
                'assistant director of finance - budget & grants',
                'assistant director of finance - operations',
                'director of finance and revenue',
                'director of finance and administration',
                'comptroller',
                'chief financial officer',
                'cfo'
            ) THEN 'Finance Director'
            WHEN LOWER(TRIM(COALESCE(title, ''))) LIKE 'assistant director of finance -%'
            THEN 'Finance Director'
            WHEN LOWER(TRIM(COALESCE(title, ''))) IN (
                'treasurer',
                'town treasurer',
                'city treasurer',
                'borough treasurer'
            ) THEN 'Treasurer'
            ELSE role_normalized
        END
        WHERE {where_contacts}
          AND NULLIF(TRIM(COALESCE(role_normalized, '')), '') IS NULL
          AND role_family = 'finance'
          AND (
                LOWER(TRIM(COALESCE(title, ''))) IN (
                    'director of finance',
                    'title: director of finance',
                    'assistant director of finance',
                    'assistant director of finance - budget & grants',
                    'assistant director of finance - operations',
                    'director of finance and revenue',
                    'director of finance and administration',
                    'comptroller',
                    'chief financial officer',
                    'cfo',
                    'treasurer',
                    'town treasurer',
                    'city treasurer',
                    'borough treasurer'
                )
                OR LOWER(TRIM(COALESCE(title, ''))) LIKE 'assistant director of finance -%'
          )
        """,
        params,
    )

    conn.execute(
        f"""
        UPDATE contacts
        SET department_normalized = CASE
            WHEN LOWER(COALESCE(NULLIF(TRIM(department), ''), NULLIF(TRIM(title), ''), '')) LIKE '%assessor%'
            THEN 'Assessor'
            WHEN LOWER(COALESCE(NULLIF(TRIM(department), ''), NULLIF(TRIM(title), ''), '')) LIKE '%tax collector%'
            THEN 'Tax Collector'
            WHEN LOWER(COALESCE(NULLIF(TRIM(department), ''), NULLIF(TRIM(title), ''), '')) LIKE '%town clerk%'
            THEN 'Town Clerk'
            WHEN LOWER(COALESCE(NULLIF(TRIM(department), ''), NULLIF(TRIM(title), ''), '')) LIKE '%building%'
            THEN 'Building'
            WHEN LOWER(COALESCE(NULLIF(TRIM(department), ''), NULLIF(TRIM(title), ''), '')) LIKE '%planning%'
                 OR LOWER(COALESCE(NULLIF(TRIM(department), ''), NULLIF(TRIM(title), ''), '')) LIKE '%zoning%'
            THEN 'Planning & Zoning'
            WHEN LOWER(COALESCE(NULLIF(TRIM(department), ''), NULLIF(TRIM(title), ''), '')) LIKE '%finance%'
                 OR LOWER(COALESCE(NULLIF(TRIM(department), ''), NULLIF(TRIM(title), ''), '')) LIKE '%treasurer%'
            THEN 'Finance'
            WHEN LOWER(COALESCE(NULLIF(TRIM(department), ''), NULLIF(TRIM(title), ''), '')) LIKE '%land use%'
            THEN 'Land Use'
            WHEN LOWER(COALESCE(NULLIF(TRIM(department), ''), NULLIF(TRIM(title), ''), '')) LIKE '%board of selectmen%'
                 OR LOWER(COALESCE(NULLIF(TRIM(department), ''), NULLIF(TRIM(title), ''), '')) LIKE '%selectmen%'
            THEN 'Board of Selectmen'
            WHEN LOWER(COALESCE(NULLIF(TRIM(department), ''), NULLIF(TRIM(title), ''), '')) LIKE '%mayor%'
            THEN 'Mayor''s Office'
            WHEN LOWER(COALESCE(NULLIF(TRIM(department), ''), NULLIF(TRIM(title), ''), '')) LIKE '%town manager%'
                 OR LOWER(COALESCE(NULLIF(TRIM(department), ''), NULLIF(TRIM(title), ''), '')) LIKE '%town administrator%'
            THEN 'Town Manager'
            WHEN NULLIF(TRIM(COALESCE(department, '')), '') IS NOT NULL
            THEN TRIM(department)
            ELSE NULL
        END
        WHERE {where_contacts}
        """,
        params,
    )

    conn.execute(
        f"""
        UPDATE contacts
        SET suspicious_reason = CASE
            WHEN role_normalized = 'Assessor'
                 AND LOWER(COALESCE(department, '')) NOT LIKE '%assessor%'
            THEN 'role_department_mismatch'
            WHEN role_normalized = 'Tax Collector'
                 AND LOWER(COALESCE(department, '')) NOT LIKE '%tax%'
            THEN 'role_department_mismatch'
            WHEN role_normalized = 'Town Clerk'
                 AND LOWER(COALESCE(department, '')) NOT LIKE '%clerk%'
            THEN 'role_department_mismatch'
            WHEN role_normalized = 'Building Official'
                 AND LOWER(COALESCE(department, '')) NOT LIKE '%building%'
            THEN 'role_department_mismatch'
            WHEN role_normalized = 'Finance Director'
                 AND LOWER(COALESCE(department, '')) NOT LIKE '%finance%'
            THEN 'role_department_mismatch'
            ELSE NULL
        END
        WHERE {where_contacts}
        """,
        params,
    )

    conn.execute(
        f"""
        UPDATE contacts
        SET semantic_confidence = MAX(
            0.0,
            MIN(
                1.0,
                COALESCE(confidence, 0.0)
                + CASE WHEN has_name = 1 THEN 0.15 ELSE 0.0 END
                + CASE WHEN has_email = 1 THEN 0.10 ELSE 0.0 END
                + CASE WHEN has_phone = 1 THEN 0.05 ELSE 0.0 END
                + CASE WHEN NULLIF(TRIM(COALESCE(role_normalized, '')), '') IS NOT NULL THEN 0.10 ELSE 0.0 END
                + CASE WHEN NULLIF(TRIM(COALESCE(department_normalized, '')), '') IS NOT NULL THEN 0.05 ELSE 0.0 END
                - CASE WHEN is_likely_noise = 1 THEN 0.25 ELSE 0.0 END
                - CASE WHEN entity_type = 'unknown' THEN 0.10 ELSE 0.0 END
            )
        )
        WHERE {where_contacts}
        """,
        params,
    )

    conn.execute(
        f"""
        UPDATE contacts
        SET dedupe_key = LOWER(
            COALESCE(TRIM(municipality_id), '')
            || '|'
            || COALESCE(NULLIF(TRIM(role_normalized), ''), NULLIF(TRIM(title), ''), '')
            || '|'
            || COALESCE(
                NULLIF(TRIM(email), ''),
                NULLIF(TRIM(phone), ''),
                NULLIF(TRIM(name), ''),
                ''
            )
        )
        WHERE {where_contacts}
        """,
        params,
    )

    conn.execute(
        f"""
        WITH ranked AS (
            SELECT
                contact_id,
                ROW_NUMBER() OVER (
                    PARTITION BY COALESCE(dedupe_key, '')
                    ORDER BY
                        COALESCE(is_likely_noise, 0) ASC,
                        COALESCE(has_name, 0) DESC,
                        COALESCE(has_email, 0) DESC,
                        COALESCE(has_phone, 0) DESC,
                        COALESCE(semantic_confidence, 0.0) DESC,
                        COALESCE(source_url, '') ASC
                ) AS rn
            FROM contacts
            WHERE {where_contacts}
        )
        UPDATE contacts
        SET record_rank = (
            SELECT ranked.rn
            FROM ranked
            WHERE ranked.contact_id = contacts.contact_id
        )
        WHERE {where_contacts}
        """,
        params + params,
    )

    conn.execute(
        f"""
        UPDATE contacts
        SET display_confidence = CASE
            WHEN COALESCE(record_rank, 1) = 1 THEN COALESCE(semantic_confidence, 0.0)
            ELSE MAX(0.0, MIN(1.0, COALESCE(semantic_confidence, 0.0) - 0.20))
        END
        WHERE {where_contacts}
        """,
        params,
    )

    conn.execute(
        f"""
        UPDATE contacts
        SET title = CASE
            WHEN NULLIF(TRIM(COALESCE(title, '')), '') IS NULL THEN NULL
            WHEN LOWER(COALESCE(title, '')) LIKE '%is responsible for%' THEN NULL
            WHEN (
                LENGTH(TRIM(COALESCE(title, ''))) - LENGTH(REPLACE(TRIM(COALESCE(title, '')), ' ', '')) + 1
            ) > 12 THEN NULL
            WHEN (
                (
                    LOWER(COALESCE(title, '')) LIKE '%.%'
                    OR LOWER(COALESCE(title, '')) LIKE '%?%'
                    OR LOWER(COALESCE(title, '')) LIKE '%!%'
                )
                AND (
                    LENGTH(TRIM(COALESCE(title, ''))) - LENGTH(REPLACE(TRIM(COALESCE(title, '')), ' ', '')) + 1
                ) >= 6
            ) THEN NULL
            ELSE TRIM(title)
        END
        WHERE {where_contacts}
        """,
        params,
    )

    conn.execute(
        f"""
        UPDATE service_links
        SET service_type = NULLIF(TRIM(COALESCE(category, '')), '')
        WHERE {where_services}
        """,
        params,
    )

    conn.execute(
        f"""
        UPDATE service_links
        SET service_type_normalized = CASE
            WHEN LOWER(COALESCE(category, '')) LIKE '%gis%'
                 OR LOWER(COALESCE(label, '')) LIKE '%gis%'
                 OR LOWER(COALESCE(url, '')) LIKE '%gis%'
            THEN 'gis'
            WHEN LOWER(COALESCE(category, '')) LIKE '%property%'
                 OR LOWER(COALESCE(category, '')) LIKE '%field card%'
                 OR LOWER(COALESCE(label, '')) LIKE '%property card%'
                 OR LOWER(COALESCE(label, '')) LIKE '%field card%'
                 OR LOWER(COALESCE(url, '')) LIKE '%property%'
            THEN 'property_cards'
            WHEN LOWER(COALESCE(category, '')) LIKE '%tax%'
                 OR LOWER(COALESCE(label, '')) LIKE '%tax payment%'
                 OR LOWER(COALESCE(url, '')) LIKE '%tax%'
            THEN 'tax_payment'
            WHEN LOWER(COALESCE(category, '')) LIKE '%job%'
                 OR LOWER(COALESCE(category, '')) LIKE '%employment%'
                 OR LOWER(COALESCE(label, '')) LIKE '%job%'
                 OR LOWER(COALESCE(label, '')) LIKE '%employment%'
            THEN 'jobs'
            WHEN LOWER(COALESCE(category, '')) LIKE '%permit%'
                 OR LOWER(COALESCE(label, '')) LIKE '%permit%'
                 OR LOWER(COALESCE(url, '')) LIKE '%permit%'
            THEN 'permits'
            WHEN LOWER(COALESCE(category, '')) LIKE '%agenda%'
                 OR LOWER(COALESCE(category, '')) LIKE '%minute%'
                 OR LOWER(COALESCE(label, '')) LIKE '%agenda%'
                 OR LOWER(COALESCE(label, '')) LIKE '%minute%'
                 OR LOWER(COALESCE(url, '')) LIKE '%agenda%'
                 OR LOWER(COALESCE(url, '')) LIKE '%minute%'
            THEN 'agendas_minutes'
            ELSE NULLIF(TRIM(COALESCE(category, '')), '')
        END
        WHERE {where_services}
        """,
        params,
    )

    conn.execute(
        f"""
        UPDATE service_links
        SET provider_normalized = CASE
            WHEN LOWER(COALESCE(vendor, '')) LIKE '%civicplus%' THEN 'CivicPlus'
            WHEN LOWER(COALESCE(vendor, '')) LIKE '%governmentjobs%' THEN 'GovernmentJobs'
            WHEN LOWER(COALESCE(vendor, '')) LIKE '%vision%' OR LOWER(COALESCE(vendor, '')) LIKE '%vision government solutions%' THEN 'Vision'
            ELSE NULLIF(TRIM(COALESCE(vendor, '')), '')
        END
        WHERE {where_services}
        """,
        params,
    )

    conn.execute(
        f"""
        UPDATE service_links
        SET is_external = CASE
            WHEN LOWER(COALESCE(service_page_type, '')) LIKE '%external%'
                 OR LOWER(COALESCE(service_page_type, '')) LIKE '%vendor%'
                 OR LOWER(COALESCE(service_page_type, '')) LIKE '%third%'
            THEN 1
            WHEN EXISTS (
                SELECT 1
                FROM municipalities m
                WHERE m.municipality_id = service_links.municipality_id
                  AND NULLIF(TRIM(COALESCE(service_links.domain, '')), '') IS NOT NULL
                  AND NULLIF(TRIM(COALESCE(m.domain, '')), '') IS NOT NULL
                  AND LOWER(TRIM(service_links.domain)) NOT LIKE '%' || LOWER(TRIM(m.domain))
                  AND LOWER(TRIM(m.domain)) NOT LIKE '%' || LOWER(TRIM(service_links.domain))
            )
            THEN 1
            ELSE 0
        END
        WHERE {where_services}
        """,
        params,
    )

    conn.execute(
        f"""
        UPDATE service_links
        SET display_confidence = MAX(
            0.0,
            MIN(
                1.0,
                COALESCE(confidence, 0.0)
                + CASE WHEN NULLIF(TRIM(COALESCE(provider_normalized, '')), '') IS NOT NULL THEN 0.05 ELSE 0.0 END
                + CASE WHEN NULLIF(TRIM(COALESCE(service_type_normalized, '')), '') IS NOT NULL THEN 0.05 ELSE 0.0 END
            )
        )
        WHERE {where_services}
        """,
        params,
    )


def refresh_views(conn: sqlite3.Connection) -> None:
    current_vw_contacts_clean = get_view_sql(conn, "vw_contacts_clean")

    conn.execute("DROP VIEW IF EXISTS vw_best_role_per_town")
    conn.execute("DROP VIEW IF EXISTS vw_contacts_clean")

    conn.execute(current_vw_contacts_clean or FALLBACK_VW_CONTACTS_CLEAN_SQL)
    conn.execute(FALLBACK_VW_BEST_ROLE_PER_TOWN_SQL)


def print_metrics(title: str, metrics: dict[str, int]) -> None:
    print(title)
    print(f"  raw contacts: {metrics['raw_contacts']}")
    print(f"  contacts with entity_type: {metrics['contacts_with_entity_type']}")
    print(f"  contacts with role_normalized: {metrics['contacts_with_role_normalized']}")
    print(f"  rows in vw_contacts_clean: {metrics['rows_in_vw_contacts_clean']}")
    print(f"  rows in vw_best_role_per_town: {metrics['rows_in_vw_best_role_per_town']}")


def main() -> None:
    args = parse_args()
    municipality_ids = select_batch_municipality_ids(
        manifest_path=args.manifest,
        batch_id=args.batch_id,
        platform=args.platform,
        seed_csv_path=args.seed_csv,
    )

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        ensure_hygiene_columns(conn)
        before = count_metrics(conn, municipality_ids)
        run_batch_enrichment(conn, municipality_ids)
        refresh_views(conn)
        conn.commit()
        after = count_metrics(conn, municipality_ids)
    finally:
        conn.close()

    print(f"Batch post-processing complete for {args.batch_id}")
    print(f"Municipalities in scope: {len(municipality_ids)}")
    print_metrics("Before:", before)
    print_metrics("After:", after)


if __name__ == "__main__":
    main()
