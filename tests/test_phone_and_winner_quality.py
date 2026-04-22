from __future__ import annotations

import csv
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.export_csvs import export_query
from scripts.postprocess_batch import FALLBACK_VW_BEST_ROLE_PER_TOWN_SQL, FALLBACK_VW_CONTACTS_CLEAN_SQL
from src.normalize import safe_phone_str


CONTACT_COLUMNS = (
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
    "record_rank",
    "is_likely_noise",
    "dedupe_key",
    "suspicious_reason",
)


class PhoneAndWinnerQualityTests(unittest.TestCase):
    def test_safe_phone_str_converts_scientific_notation(self) -> None:
        self.assertEqual(safe_phone_str("8.6E+09"), "8600000000")
        self.assertEqual(safe_phone_str(2.04e9), "2040000000")
        self.assertEqual(safe_phone_str("0123456789"), "0123456789")

    def test_export_query_writes_phone_fields_as_plain_strings(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE sample (phone TEXT, phone_ext TEXT, name TEXT)")
        conn.execute(
            "INSERT INTO sample (phone, phone_ext, name) VALUES (?, ?, ?)",
            ("8.6E+09", 210.0, "Finance"),
        )
        tmp_dir = ROOT / "outputs" / "test_tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        out_path = tmp_dir / "sample_export.csv"
        try:
            export_query(conn, "SELECT phone, phone_ext, name FROM sample", tuple(), out_path)
            with out_path.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
        finally:
            if out_path.exists():
                out_path.unlink()
        conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["phone"], "8600000000")
        self.assertEqual(rows[0]["phone_ext"], "210")

    def test_mismatch_candidate_loses_when_better_aligned_exists(self) -> None:
        conn = self._build_postprocess_test_db()
        self._insert_contact(
            conn,
            contact_id="bad_finance",
            municipality_id="ct_test",
            role_normalized="Finance Director",
            role_family="finance",
            department="Board of Education",
            department_normalized="Board of Education",
            email="finance@school.org",
            phone="8601112222",
            page_type="department_page",
            source_url="https://town.example.org/board-of-education/staff",
            display_confidence=0.98,
            suspicious_reason="role_department_mismatch",
        )
        self._insert_contact(
            conn,
            contact_id="good_finance",
            municipality_id="ct_test",
            role_normalized="Finance Director",
            role_family="finance",
            department="Finance Department",
            department_normalized="Finance",
            email="",
            phone="8603334444",
            page_type="department_page",
            source_url="https://town.example.org/finance-department",
            display_confidence=0.74,
            suspicious_reason=None,
        )
        winner = conn.execute(
            """
            SELECT contact_id
            FROM vw_best_role_per_town
            WHERE municipality_id = ? AND role_normalized = ?
            """,
            ("ct_test", "Finance Director"),
        ).fetchone()
        conn.close()
        self.assertIsNotNone(winner)
        self.assertEqual(winner["contact_id"], "good_finance")

    def test_only_mismatch_candidate_still_surfaces(self) -> None:
        conn = self._build_postprocess_test_db()
        self._insert_contact(
            conn,
            contact_id="only_finance",
            municipality_id="ct_only",
            role_normalized="Finance Director",
            role_family="finance",
            department="Board of Education",
            department_normalized="Board of Education",
            email="finance@school.org",
            phone="8605559999",
            page_type="department_page",
            source_url="https://town.example.org/board-of-education/staff",
            display_confidence=0.92,
            suspicious_reason="role_department_mismatch",
        )
        winner = conn.execute(
            """
            SELECT contact_id
            FROM vw_best_role_per_town
            WHERE municipality_id = ? AND role_normalized = ?
            """,
            ("ct_only", "Finance Director"),
        ).fetchone()
        conn.close()
        self.assertIsNotNone(winner)
        self.assertEqual(winner["contact_id"], "only_finance")

    def test_directory_context_not_over_penalized(self) -> None:
        conn = self._build_postprocess_test_db()
        self._insert_contact(
            conn,
            contact_id="dir_tax",
            municipality_id="ct_dir",
            role_normalized="Tax Collector",
            role_family="tax_collector",
            department="",
            department_normalized="",
            email="tax@town.org",
            phone="8604447777",
            page_type="staff_directory",
            source_url="https://town.example.org/Directory.aspx",
            display_confidence=0.80,
            suspicious_reason=None,
        )
        self._insert_contact(
            conn,
            contact_id="bad_tax",
            municipality_id="ct_dir",
            role_normalized="Tax Collector",
            role_family="tax_collector",
            department="Planning",
            department_normalized="Planning & Zoning",
            email="tax-planning@town.org",
            phone="8604447778",
            page_type="department_page",
            source_url="https://town.example.org/planning",
            display_confidence=0.93,
            suspicious_reason="role_department_mismatch",
        )
        winner = conn.execute(
            """
            SELECT contact_id
            FROM vw_best_role_per_town
            WHERE municipality_id = ? AND role_normalized = ?
            """,
            ("ct_dir", "Tax Collector"),
        ).fetchone()
        conn.close()
        self.assertIsNotNone(winner)
        self.assertEqual(winner["contact_id"], "dir_tax")

    def _build_postprocess_test_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE contacts (
                contact_id TEXT PRIMARY KEY,
                municipality_id TEXT,
                entity_type TEXT,
                name TEXT,
                title TEXT,
                role_normalized TEXT,
                role_family TEXT,
                department TEXT,
                department_normalized TEXT,
                email TEXT,
                email_type TEXT,
                phone TEXT,
                phone_ext TEXT,
                address TEXT,
                hours TEXT,
                page_type TEXT,
                source_url TEXT,
                display_confidence REAL,
                record_rank INTEGER,
                is_likely_noise INTEGER,
                dedupe_key TEXT,
                suspicious_reason TEXT
            )
            """
        )
        conn.execute(FALLBACK_VW_CONTACTS_CLEAN_SQL)
        conn.execute(FALLBACK_VW_BEST_ROLE_PER_TOWN_SQL)
        return conn

    def _insert_contact(self, conn: sqlite3.Connection, **overrides: object) -> None:
        payload: dict[str, object] = {
            "contact_id": "",
            "municipality_id": "",
            "entity_type": "person",
            "name": "Name",
            "title": "",
            "role_normalized": "",
            "role_family": "",
            "department": "",
            "department_normalized": "",
            "email": "",
            "email_type": "unknown",
            "phone": "",
            "phone_ext": "",
            "address": "",
            "hours": "",
            "page_type": "department_page",
            "source_url": "",
            "display_confidence": 0.5,
            "record_rank": 1,
            "is_likely_noise": 0,
            "dedupe_key": "",
            "suspicious_reason": None,
        }
        payload.update(overrides)
        if not payload["dedupe_key"]:
            payload["dedupe_key"] = f"{payload['contact_id']}_dedupe"
        placeholders = ", ".join(f":{column}" for column in CONTACT_COLUMNS)
        conn.execute(
            f"INSERT INTO contacts ({', '.join(CONTACT_COLUMNS)}) VALUES ({placeholders})",
            payload,
        )
        conn.commit()


if __name__ == "__main__":
    unittest.main()
