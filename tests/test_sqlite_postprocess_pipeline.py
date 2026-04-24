from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.export_batch_qa import ensure_required_postprocess_views, fetch_count_map
from scripts.postprocess_batch import (
    count_metrics,
    ensure_postprocess_columns,
    refresh_views,
    run_batch_enrichment,
    verify_required_postprocess_objects,
)


class SqlitePostprocessPipelineTests(unittest.TestCase):
    def test_postprocess_creates_required_views_for_base_schema_db(self) -> None:
        conn = self._build_base_schema_db()
        municipality_id = "ct_test"
        self._seed_minimum_rows(conn, municipality_id)

        ensure_postprocess_columns(conn)
        run_batch_enrichment(conn, [municipality_id])
        refresh_views(conn)
        verification = verify_required_postprocess_objects(conn, [municipality_id], strict=True)

        self.assertEqual(int(verification["vw_contacts_clean_exists"]), 1)
        self.assertEqual(int(verification["vw_best_role_per_town_exists"]), 1)
        self.assertGreaterEqual(int(verification["rows_in_vw_contacts_clean_scope"]), 1)
        self.assertGreaterEqual(int(verification["rows_in_vw_best_role_per_town_scope"]), 1)
        conn.close()

    def test_verify_required_postprocess_objects_fails_loudly_when_views_missing(self) -> None:
        conn = self._build_base_schema_db()
        municipality_id = "ct_missing"
        self._seed_minimum_rows(conn, municipality_id)
        ensure_postprocess_columns(conn)
        run_batch_enrichment(conn, [municipality_id])
        with self.assertRaises(RuntimeError):
            verify_required_postprocess_objects(conn, [municipality_id], strict=True)
        conn.close()

    def test_export_batch_qa_requires_postprocess_views(self) -> None:
        conn = self._build_base_schema_db()
        municipality_id = "ct_export"
        self._seed_minimum_rows(conn, municipality_id)
        ensure_postprocess_columns(conn)
        run_batch_enrichment(conn, [municipality_id])

        with self.assertRaises(RuntimeError):
            ensure_required_postprocess_views(conn, strict=True)

        refresh_views(conn)
        missing = ensure_required_postprocess_views(conn, strict=True)
        self.assertEqual(missing, [])
        clean_counts = fetch_count_map(conn, "vw_contacts_clean", [municipality_id])
        winner_counts = fetch_count_map(conn, "vw_best_role_per_town", [municipality_id])
        self.assertGreaterEqual(int(clean_counts.get(municipality_id, 0)), 1)
        self.assertGreaterEqual(int(winner_counts.get(municipality_id, 0)), 1)
        conn.close()

    def test_count_metrics_does_not_fail_when_forced_fallback_column_absent(self) -> None:
        conn = self._build_base_schema_db()
        municipality_id = "ct_legacy_view"
        self._seed_minimum_rows(conn, municipality_id)
        ensure_postprocess_columns(conn)
        run_batch_enrichment(conn, [municipality_id])

        conn.execute("DROP VIEW IF EXISTS vw_best_role_per_town")
        conn.execute(
            """
            CREATE VIEW vw_best_role_per_town AS
            SELECT
                contact_id,
                municipality_id,
                role_normalized
            FROM contacts
            WHERE NULLIF(TRIM(COALESCE(role_normalized, '')), '') IS NOT NULL
            """
        )

        metrics = count_metrics(conn, [municipality_id])
        self.assertIn("revize_roles_with_forced_fallback", metrics)
        self.assertEqual(int(metrics["revize_roles_with_forced_fallback"]), 0)
        conn.close()

    def _build_base_schema_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        schema_sql = (ROOT / "database" / "schema.sql").read_text(encoding="utf-8")
        conn.executescript(schema_sql)
        return conn

    def _seed_minimum_rows(self, conn: sqlite3.Connection, municipality_id: str) -> None:
        conn.execute(
            """
            INSERT INTO municipalities (municipality_id, name, county, website_url, domain)
            VALUES (?, ?, ?, ?, ?)
            """,
            (municipality_id, "Test Town", "Test County", "https://example.gov", "example.gov"),
        )
        conn.execute(
            """
            INSERT INTO contacts (
                contact_id,
                municipality_id,
                name,
                title,
                department,
                email,
                email_type,
                phone,
                phone_ext,
                address,
                hours,
                source_context,
                source_url,
                confidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{municipality_id}_contact_1",
                municipality_id,
                "Jane Doe",
                "Town Clerk",
                "Town Clerk",
                "jane.doe@example.gov",
                "direct",
                "8605551000",
                None,
                None,
                None,
                "Town Clerk contact",
                "https://example.gov/departments/town-clerk",
                0.88,
            ),
        )
        conn.commit()


if __name__ == "__main__":
    unittest.main()
