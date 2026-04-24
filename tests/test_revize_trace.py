from __future__ import annotations

import csv
import json
import sqlite3
import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.postprocess_batch import FALLBACK_VW_BEST_ROLE_PER_TOWN_SQL, FALLBACK_VW_CONTACTS_CLEAN_SQL
from src.revize_trace import RevizeTraceCollector


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
    "source_context",
    "confidence",
    "display_confidence",
    "record_rank",
    "is_likely_noise",
    "dedupe_key",
    "suspicious_reason",
)


class RevizeTraceCollectorTests(unittest.TestCase):
    def test_trace_collector_writes_stage_counts_drop_reasons_and_row_trace(self) -> None:
        conn = self._build_postprocess_like_db()
        self._insert_contact(
            conn,
            contact_id="c1",
            municipality_id="ct_trace",
            name="Jane Doe",
            title="Town Clerk",
            role_normalized="Town Clerk",
            role_family="town_clerk",
            department="Town Clerk",
            email="jane@example.gov",
            phone="8605551111",
            source_url="https://example.gov/departments/town-clerk",
            display_confidence=0.92,
        )
        self._insert_contact(
            conn,
            contact_id="c2",
            municipality_id="ct_trace",
            name="John Roe",
            title="Assistant Town Clerk",
            role_normalized="Town Clerk",
            role_family="town_clerk",
            department="Town Clerk",
            email="john@example.gov",
            phone="8605552222",
            source_url="https://example.gov/departments/town-clerk/staff",
            display_confidence=0.81,
        )
        self._insert_contact(
            conn,
            contact_id="c3",
            municipality_id="ct_trace",
            name="Sam Public",
            title="Permit Technician",
            role_normalized="",
            role_family="",
            department="Building",
            email="sam@example.gov",
            phone="8605553333",
            source_url="https://example.gov/departments/building",
            display_confidence=0.77,
        )
        self._insert_contact(
            conn,
            contact_id="c4",
            municipality_id="ct_trace",
            name="Noise Row",
            title="Office",
            role_normalized="Assessor",
            role_family="assessor",
            department="Assessor",
            email="noise@example.gov",
            phone="8605554444",
            source_url="https://example.gov/departments/assessor",
            display_confidence=0.65,
            is_likely_noise=1,
            dedupe_key="c1_dedupe",
        )

        tmp_dir = self._make_tmp_dir("full_trace")
        try:
            collector = RevizeTraceCollector(output_dir=tmp_dir, sample_size=12)
            collector.register_municipality("ct_trace", "Revize")
            collector.register_municipality("ct_other", "CivicPlus")
            collector.record_revize_result(
                "ct_trace",
                {
                    "rows_extracted_total": 6,
                    "rows_normalized_seen": 6,
                    "rows_normalized_kept": 4,
                    "rows_normalized_rejected": 2,
                    "extracted_rows_sample": [
                        {
                            "name": "Jane Doe",
                            "title": "Town Clerk",
                            "department": "Town Clerk",
                            "email": "jane@example.gov",
                            "phone": "8605551111",
                            "source_url": "https://example.gov/departments/town-clerk",
                        }
                    ],
                    "normalized_rows_sample": [
                        {
                            "name": "Jane Doe",
                            "title": "Town Clerk",
                            "department": "Town Clerk",
                            "email": "jane@example.gov",
                            "phone": "8605551111",
                            "source_url": "https://example.gov/departments/town-clerk",
                        }
                    ],
                    "rejected_rows_sample": [
                        {
                            "row": {
                                "name": "Contact",
                                "title": "Contact Info",
                                "department": "Building",
                                "source_url": "https://example.gov/departments/building",
                            },
                            "drop_reason": "drop_name_literal_reject",
                        }
                    ],
                    "reconstructed_rows_sample": [
                        {
                            "source_url": "https://example.gov/departments/building",
                            "original_lines": [
                                "Carl Brown",
                                "Building & Zoning Enforcement Officer",
                                "86",
                                "0-376-7060x2109",
                            ],
                            "reconstructed_name": "Carl Brown",
                            "reconstructed_title": "Building & Zoning Enforcement Officer",
                            "reconstructed_email": "buildingdepartment@example.gov",
                            "reconstructed_phone": "8603767060",
                            "phone_ext": "2109",
                            "accepted": 1,
                            "rejection_reason": "",
                        },
                        {
                            "source_url": "https://example.gov/departments/building",
                            "original_lines": ["VACANT", "Planning Assistant"],
                            "reconstructed_name": "VACANT",
                            "reconstructed_title": "Planning Assistant",
                            "reconstructed_email": "",
                            "reconstructed_phone": "",
                            "phone_ext": "",
                            "accepted": 0,
                            "rejection_reason": "vacancy_name",
                        },
                    ],
                    "suspicious_reduction_counts": {"drop_name_literal_reject": 2},
                },
            )
            collector.record_insert_debug(
                municipality_id="ct_trace",
                insert_attempted=5,
                inserted_or_updated=4,
                debug_rows=[
                    {
                        "stage": "insert_precheck",
                        "drop_stage": "insert_precheck",
                        "drop_reason": "missing_contact_fields",
                        "row": {
                            "name": "No Contact",
                            "source_url": "https://example.gov/departments/building",
                        },
                    },
                    {
                        "stage": "insert_upsert",
                        "drop_stage": "",
                        "drop_reason": "",
                        "row": {
                            "name": "Jane Doe",
                            "title": "Town Clerk",
                            "department": "Town Clerk",
                            "email": "jane@example.gov",
                            "phone": "8605551111",
                            "source_url": "https://example.gov/departments/town-clerk",
                        },
                    },
                ],
            )
            collector.finalize_from_db(conn)
            paths = collector.write_outputs()

            stage_counts = self._read_csv(paths["revize_stage_counts.csv"])
            self.assertEqual(len(stage_counts), 1)
            self.assertEqual(stage_counts[0]["municipality_id"], "ct_trace")
            self.assertEqual(int(stage_counts[0]["revize_rows_extracted"]), 6)
            self.assertEqual(int(stage_counts[0]["revize_rows_normalized_seen"]), 6)
            self.assertEqual(int(stage_counts[0]["revize_rows_normalized_kept"]), 4)
            self.assertEqual(int(stage_counts[0]["revize_rows_normalized_rejected"]), 2)
            self.assertEqual(int(stage_counts[0]["revize_rows_insert_attempted"]), 5)
            self.assertEqual(int(stage_counts[0]["revize_rows_inserted_or_updated"]), 4)
            self.assertEqual(int(stage_counts[0]["revize_rows_in_clean_contacts"]), 3)
            self.assertEqual(int(stage_counts[0]["revize_rows_dropped_pre_clean_contacts"]), 1)
            self.assertEqual(int(stage_counts[0]["revize_rows_considered_for_role_winners"]), 2)
            self.assertEqual(int(stage_counts[0]["revize_rows_selected_as_role_winners"]), 1)

            drop_reasons = self._read_csv(paths["revize_drop_reasons.csv"])
            reason_counts = {
                (row["drop_stage"], row["drop_reason"]): int(row["count"])
                for row in drop_reasons
                if row["municipality_id"] == "ct_trace"
            }
            self.assertEqual(reason_counts.get(("normalization", "drop_name_literal_reject")), 2)
            self.assertEqual(reason_counts.get(("insert_precheck", "missing_contact_fields")), 1)
            self.assertEqual(reason_counts.get(("pre_clean_contacts", "failed_clean_contact_filter")), 1)
            self.assertEqual(
                reason_counts.get(("role_winner_selection", "failed_role_mapping_or_scoring")),
                1,
            )

            trace_rows = self._read_jsonl(paths["revize_row_trace.jsonl"])
            self.assertTrue(trace_rows)
            self.assertTrue(any("normalized_rejected" in row.get("stages", {}) for row in trace_rows))
            self.assertTrue(any("clean_contacts" in row.get("stages", {}) for row in trace_rows))
            self.assertTrue(any("role_winner" in row.get("stages", {}) for row in trace_rows))
            reconstructed_rows = [row for row in trace_rows if "reconstructed_contact_block" in row.get("stages", {})]
            self.assertTrue(reconstructed_rows)
            reconstructed_stage = reconstructed_rows[0]["stages"]["reconstructed_contact_block"]
            self.assertTrue(reconstructed_stage.get("original_lines"))
            self.assertIn("reconstructed_name", reconstructed_stage)
            self.assertIn("accepted", reconstructed_stage)
            self.assertEqual(str(reconstructed_stage.get("revize_source_type") or ""), "reconstructed_contact_block")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        conn.close()

    def test_trace_collector_marks_missing_clean_view(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE contacts (
                contact_id TEXT PRIMARY KEY,
                municipality_id TEXT,
                name TEXT,
                title TEXT,
                department TEXT,
                email TEXT,
                phone TEXT,
                source_url TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO contacts (contact_id, municipality_id, name, title, department, email, phone, source_url)
            VALUES ('x1', 'ct_trace_missing', 'Jane Doe', 'Town Clerk', 'Town Clerk', 'jane@example.gov', '8605551111', 'https://example.gov')
            """
        )
        conn.commit()

        tmp_dir = self._make_tmp_dir("missing_clean_view")
        try:
            collector = RevizeTraceCollector(output_dir=tmp_dir, sample_size=4)
            collector.register_municipality("ct_trace_missing", "Revize")
            collector.record_insert_debug(
                municipality_id="ct_trace_missing",
                insert_attempted=1,
                inserted_or_updated=1,
                debug_rows=[],
            )
            collector.finalize_from_db(conn)
            paths = collector.write_outputs()

            stage_counts = self._read_csv(paths["revize_stage_counts.csv"])
            self.assertEqual(len(stage_counts), 1)
            self.assertEqual(int(stage_counts[0]["revize_rows_inserted_or_updated"]), 1)
            self.assertEqual(int(stage_counts[0]["revize_rows_in_clean_contacts"]), 0)
            self.assertEqual(int(stage_counts[0]["revize_rows_dropped_pre_clean_contacts"]), 0)

            drop_reasons = self._read_csv(paths["revize_drop_reasons.csv"])
            reason_counts = {
                (row["drop_stage"], row["drop_reason"]): int(row["count"])
                for row in drop_reasons
                if row["municipality_id"] == "ct_trace_missing"
            }
            self.assertEqual(reason_counts.get(("pre_clean_contacts", "clean_contacts_view_missing")), 1)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        conn.close()

    def _build_postprocess_like_db(self) -> sqlite3.Connection:
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
                source_context TEXT,
                confidence REAL,
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
            "source_context": "",
            "confidence": 0.5,
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

    def _read_csv(self, path: Path) -> list[dict[str, str]]:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    def _read_jsonl(self, path: Path) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                out.append(json.loads(line))
        return out

    def _make_tmp_dir(self, suffix: str) -> Path:
        tmp_dir = ROOT / "outputs" / "test_tmp" / "revize_trace" / suffix
        shutil.rmtree(tmp_dir, ignore_errors=True)
        tmp_dir.mkdir(parents=True, exist_ok=True)
        return tmp_dir


if __name__ == "__main__":
    unittest.main()
