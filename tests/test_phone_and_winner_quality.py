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
from scripts.postprocess_batch import (
    FALLBACK_VW_BEST_ROLE_PER_TOWN_SQL,
    FALLBACK_VW_CONTACTS_CLEAN_SQL,
    FALLBACK_VW_ROLE_CANDIDATES_SCORED_SQL,
    FALLBACK_VW_UNRESOLVED_ROLES_SQL,
)
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
    "source_context",
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
            source_context="revize:profile_block|page_class=department_page",
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
        review_candidate = conn.execute(
            """
            SELECT contact_id, candidate_state, winner_disqualifier_reason
            FROM vw_role_candidates_scored
            WHERE municipality_id = ? AND role_normalized = ?
            """,
            ("ct_only", "Finance Director"),
        ).fetchone()
        unresolved = conn.execute(
            """
            SELECT role_normalized, forced_fallback_blocked
            FROM vw_unresolved_roles
            WHERE municipality_id = ? AND role_normalized = ?
            """,
            ("ct_only", "Finance Director"),
        ).fetchone()
        conn.close()
        self.assertIsNone(winner)
        self.assertIsNotNone(review_candidate)
        self.assertEqual(review_candidate["contact_id"], "only_finance")
        self.assertEqual(review_candidate["candidate_state"], "candidate_for_review")
        self.assertEqual(review_candidate["winner_disqualifier_reason"], "role_department_mismatch")
        self.assertIsNotNone(unresolved)
        self.assertEqual(int(unresolved["forced_fallback_blocked"]), 1)

    def test_clean_review_candidate_promotes_when_no_high_confidence_winner_exists(self) -> None:
        conn = self._build_postprocess_test_db()
        self._insert_contact(
            conn,
            contact_id="review_assessor",
            municipality_id="ct_review_promote",
            role_normalized="Assessor",
            role_family="assessor",
            name="Jane Doe",
            title="",
            department="",
            email="",
            phone="8605550100",
            page_type="staff_directory",
            source_url="https://town.example.org/departments/assessor/index.php",
            source_context="revize:labeled_staff|page_class=staff_directory",
            display_confidence=0.81,
            suspicious_reason=None,
        )
        winner = conn.execute(
            """
            SELECT contact_id, candidate_state, forced_fallback
            FROM vw_best_role_per_town
            WHERE municipality_id = ? AND role_normalized = ?
            """,
            ("ct_review_promote", "Assessor"),
        ).fetchone()
        review_candidate = conn.execute(
            """
            SELECT contact_id, candidate_state, winner_disqualifier_reason
            FROM vw_role_candidates_scored
            WHERE municipality_id = ? AND role_normalized = ?
            """,
            ("ct_review_promote", "Assessor"),
        ).fetchone()
        unresolved = conn.execute(
            """
            SELECT role_normalized
            FROM vw_unresolved_roles
            WHERE municipality_id = ? AND role_normalized = ?
            """,
            ("ct_review_promote", "Assessor"),
        ).fetchone()
        conn.close()
        self.assertIsNotNone(winner)
        self.assertEqual(winner["contact_id"], "review_assessor")
        self.assertEqual(winner["candidate_state"], "candidate_for_review")
        self.assertEqual(int(winner["forced_fallback"]), 1)
        self.assertIsNotNone(review_candidate)
        self.assertEqual(review_candidate["candidate_state"], "candidate_for_review")
        self.assertEqual(review_candidate["winner_disqualifier_reason"], "")
        self.assertIsNone(unresolved)

    def test_structural_priority_beats_higher_score_with_weaker_contact_shape(self) -> None:
        conn = self._build_postprocess_test_db()
        self._insert_contact(
            conn,
            contact_id="score_favored_assessor",
            municipality_id="ct_structural_priority",
            role_normalized="Assessor",
            role_family="assessor",
            name="Jordan Strong",
            title="Assessor",
            department="Assessor's Office",
            email="",
            phone="",
            page_type="department_page",
            source_url="https://town.example.org/assessor",
            source_context="revize:contact_card|page_class=department_page",
            display_confidence=0.88,
            suspicious_reason=None,
        )
        self._insert_contact(
            conn,
            contact_id="structure_favored_assessor",
            municipality_id="ct_structural_priority",
            role_normalized="Assessor",
            role_family="assessor",
            name="Taylor Contact",
            title="Assessor",
            department="",
            email="",
            phone="8605550102",
            page_type="department_page",
            source_url="https://town.example.org/assessor",
            source_context="revize:contact_card|page_class=department_page",
            display_confidence=0.72,
            suspicious_reason=None,
        )
        candidates = conn.execute(
            """
            SELECT contact_id, candidate_state, candidate_score
            FROM vw_role_candidates_scored
            WHERE municipality_id = ? AND role_normalized = ?
            ORDER BY contact_id
            """,
            ("ct_structural_priority", "Assessor"),
        ).fetchall()
        winner = conn.execute(
            """
            SELECT contact_id, candidate_state
            FROM vw_best_role_per_town
            WHERE municipality_id = ? AND role_normalized = ?
            """,
            ("ct_structural_priority", "Assessor"),
        ).fetchone()
        conn.close()
        self.assertEqual(
            [(row["contact_id"], row["candidate_state"]) for row in candidates],
            [
                ("score_favored_assessor", "candidate_for_review"),
                ("structure_favored_assessor", "candidate_for_review"),
            ],
        )
        candidate_scores = {row["contact_id"]: int(row["candidate_score"]) for row in candidates}
        self.assertGreater(
            candidate_scores["score_favored_assessor"],
            candidate_scores["structure_favored_assessor"],
        )
        self.assertIsNotNone(winner)
        self.assertEqual(winner["contact_id"], "structure_favored_assessor")

    def test_title_tiebreak_prefers_director_over_manager_when_other_fields_tie(self) -> None:
        conn = self._build_postprocess_test_db()
        self._insert_contact(
            conn,
            contact_id="a_manager",
            municipality_id="ct_title_tiebreak",
            role_normalized="Finance Director",
            role_family="finance",
            name="Avery Blake",
            title="Finance Manager",
            department="Finance Department",
            email="",
            phone="8605550300",
            page_type="department_page",
            source_url="https://town.example.org/finance",
            source_context="revize:contact_card|page_class=department_page",
            display_confidence=0.72,
            suspicious_reason=None,
        )
        self._insert_contact(
            conn,
            contact_id="z_director",
            municipality_id="ct_title_tiebreak",
            role_normalized="Finance Director",
            role_family="finance",
            name="Taylor Brooks",
            title="Director of Finance",
            department="Finance Department",
            email="",
            phone="8605550301",
            page_type="department_page",
            source_url="https://town.example.org/finance",
            source_context="revize:contact_card|page_class=department_page",
            display_confidence=0.72,
            suspicious_reason=None,
        )
        candidates = conn.execute(
            """
            SELECT contact_id, candidate_state, candidate_score, display_confidence
            FROM vw_role_candidates_scored
            WHERE municipality_id = ? AND role_normalized = ?
            ORDER BY contact_id
            """,
            ("ct_title_tiebreak", "Finance Director"),
        ).fetchall()
        winner = conn.execute(
            """
            SELECT contact_id, title, candidate_state
            FROM vw_best_role_per_town
            WHERE municipality_id = ? AND role_normalized = ?
            """,
            ("ct_title_tiebreak", "Finance Director"),
        ).fetchone()
        conn.close()
        self.assertEqual(
            [(row["contact_id"], row["candidate_state"]) for row in candidates],
            [
                ("a_manager", "candidate_for_review"),
                ("z_director", "candidate_for_review"),
            ],
        )
        self.assertEqual(int(candidates[0]["candidate_score"]), int(candidates[1]["candidate_score"]))
        self.assertEqual(float(candidates[0]["display_confidence"]), float(candidates[1]["display_confidence"]))
        self.assertIsNotNone(winner)
        self.assertEqual(winner["contact_id"], "z_director")
        self.assertEqual(winner["title"], "Director of Finance")

    def test_role_group_ranking_keeps_single_winner_when_high_confidence_exists_in_family(self) -> None:
        conn = self._build_postprocess_test_db()
        self._insert_contact(
            conn,
            contact_id="planner_winner",
            municipality_id="ct_group_rank",
            role_normalized="Planner",
            role_family="planning_zoning",
            name="Pat Planner",
            title="Town Planner",
            department="Planning & Zoning",
            email="planner@town.org",
            phone="8605550200",
            page_type="staff_directory",
            source_url="https://town.example.org/planning",
            source_context="revize:labeled_staff|page_class=staff_directory",
            display_confidence=0.83,
            suspicious_reason=None,
        )
        self._insert_contact(
            conn,
            contact_id="land_use_review",
            municipality_id="ct_group_rank",
            role_normalized="Land Use",
            role_family="planning_zoning",
            name="Lana Use",
            title="Land Use Administrator",
            department="Planning & Zoning",
            email="",
            phone="8605550201",
            page_type="staff_directory",
            source_url="https://town.example.org/land-use",
            source_context="revize:labeled_staff|page_class=staff_directory",
            display_confidence=0.79,
            suspicious_reason=None,
        )
        winners = conn.execute(
            """
            SELECT contact_id, role_normalized, forced_fallback
            FROM vw_best_role_per_town
            WHERE municipality_id = ?
            ORDER BY contact_id
            """,
            ("ct_group_rank",),
        ).fetchall()
        unresolved = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM vw_unresolved_roles
            WHERE municipality_id = ?
            """,
            ("ct_group_rank",),
        ).fetchone()
        conn.close()
        self.assertEqual(len(winners), 1)
        self.assertEqual(winners[0]["contact_id"], "planner_winner")
        self.assertEqual(winners[0]["role_normalized"], "Planner")
        self.assertEqual(int(winners[0]["forced_fallback"]), 0)
        self.assertEqual(int(unresolved["cnt"]), 0)

    def test_building_primary_and_secondary_subgroups_can_both_win(self) -> None:
        conn = self._build_postprocess_test_db()
        self._insert_contact(
            conn,
            contact_id="building_primary",
            municipality_id="ct_building_subgroups",
            role_normalized="Building Official",
            role_family="building",
            name="Pat Official",
            title="Building Official",
            department="Building",
            email="official@town.org",
            phone="8605550400",
            page_type="staff_directory",
            source_url="https://town.example.org/building",
            source_context="revize:labeled_staff|page_class=staff_directory",
            display_confidence=0.82,
            suspicious_reason=None,
        )
        self._insert_contact(
            conn,
            contact_id="building_secondary",
            municipality_id="ct_building_subgroups",
            role_normalized="Building Official",
            role_family="building",
            name="Sam Inspector",
            title="Building Inspector",
            department="Building",
            email="inspector@town.org",
            phone="8605550401",
            page_type="staff_directory",
            source_url="https://town.example.org/building",
            source_context="revize:labeled_staff|page_class=staff_directory",
            display_confidence=0.79,
            suspicious_reason=None,
        )
        winners = conn.execute(
            """
            SELECT contact_id, role_group
            FROM vw_best_role_per_town
            WHERE municipality_id = ?
            ORDER BY role_group
            """,
            ("ct_building_subgroups",),
        ).fetchall()
        conn.close()
        self.assertEqual(len(winners), 2)
        self.assertEqual(
            [(row["contact_id"], row["role_group"]) for row in winners],
            [
                ("building_primary", "building_primary"),
                ("building_secondary", "building_secondary"),
            ],
        )

    def test_planning_director_with_zeo_stays_primary(self) -> None:
        conn = self._build_postprocess_test_db()
        self._insert_contact(
            conn,
            contact_id="planning_director_primary",
            municipality_id="ct_planning_subgroups",
            role_normalized="Planner",
            role_family="planning_zoning",
            name="Aarti Paranjape",
            title="Director of Planning & Zoning Chief Zoning Enforcement Officer",
            department="Planning And Zoning",
            email="planner@town.org",
            phone="8605550402",
            page_type="department_page",
            source_url="https://town.example.org/planning",
            source_context="revize:contact_card|page_class=department_page",
            display_confidence=0.74,
            suspicious_reason=None,
        )
        self._insert_contact(
            conn,
            contact_id="planning_secondary",
            municipality_id="ct_planning_subgroups",
            role_normalized="Planner",
            role_family="planning_zoning",
            name="Emily Kyle",
            title="Assistant Zoning Enforcement Officer",
            department="Planning And Zoning",
            email="assistant@town.org",
            phone="8605550403",
            page_type="staff_directory",
            source_url="https://town.example.org/planning",
            source_context="revize:labeled_staff|page_class=staff_directory",
            display_confidence=0.79,
            suspicious_reason=None,
        )
        winners = conn.execute(
            """
            SELECT contact_id, role_group
            FROM vw_best_role_per_town
            WHERE municipality_id = ?
            ORDER BY role_group
            """,
            ("ct_planning_subgroups",),
        ).fetchall()
        conn.close()
        self.assertEqual(
            [(row["contact_id"], row["role_group"]) for row in winners],
            [
                ("planning_director_primary", "planning_zoning_primary"),
                ("planning_secondary", "planning_zoning_secondary"),
            ],
        )

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

    def test_staff_directory_page_type_beats_contact_hub_for_same_role(self) -> None:
        conn = self._build_postprocess_test_db()
        self._insert_contact(
            conn,
            contact_id="staff_winner",
            municipality_id="ct_revize",
            role_normalized="Town Clerk",
            role_family="town_clerk",
            department="Town Clerk",
            department_normalized="Town Clerk",
            email="clerk@town.org",
            phone="8601002000",
            page_type="staff_directory",
            source_url="https://town.example.org/staff_directory/index.php",
            display_confidence=0.71,
            suspicious_reason=None,
        )
        self._insert_contact(
            conn,
            contact_id="hub_candidate",
            municipality_id="ct_revize",
            role_normalized="Town Clerk",
            role_family="town_clerk",
            department="General Contact",
            department_normalized="",
            email="contact@town.org",
            phone="8601002001",
            page_type="contact_hub",
            source_url="https://town.example.org/contact_us/index.php",
            display_confidence=0.96,
            suspicious_reason="contact_hub_candidate",
        )
        winner = conn.execute(
            """
            SELECT contact_id
            FROM vw_best_role_per_town
            WHERE municipality_id = ? AND role_normalized = ?
            """,
            ("ct_revize", "Town Clerk"),
        ).fetchone()
        conn.close()
        self.assertIsNotNone(winner)
        self.assertEqual(winner["contact_id"], "staff_winner")

    def test_reconstructed_row_beats_affidavit_garbage(self) -> None:
        conn = self._build_postprocess_test_db()
        self._insert_contact(
            conn,
            contact_id="groton_assessor_garbage",
            municipality_id="ct_groton",
            role_normalized="Assessor",
            role_family="assessor",
            name="Antique Motor Vehicle Affidavit",
            title="Assessor Forms",
            department="Assessor",
            source_url="https://town.example.org/contact_us/index.php",
            source_context="revize:profile_block|page_class=contact_hub",
            page_type="contact_hub",
            email="forms@town.org",
            phone="8602001000",
            display_confidence=0.99,
            suspicious_reason=None,
        )
        self._insert_contact(
            conn,
            contact_id="groton_assessor_reconstructed",
            municipality_id="ct_groton",
            role_normalized="Assessor",
            role_family="assessor",
            name="Mary Gardner",
            title="Assessor",
            department="Assessor",
            source_url="https://town.example.org/departments/assessor/index.php",
            source_context="revize:reconstructed_contact_block|page_class=department_page",
            page_type="department_page",
            email="mgardner@town.org",
            phone="8602001001",
            display_confidence=0.72,
            suspicious_reason=None,
        )
        winner = conn.execute(
            """
            SELECT contact_id
            FROM vw_best_role_per_town
            WHERE municipality_id = ? AND role_normalized = ?
            """,
            ("ct_groton", "Assessor"),
        ).fetchone()
        conn.close()
        self.assertIsNotNone(winner)
        self.assertEqual(winner["contact_id"], "groton_assessor_reconstructed")

    def test_reconstructed_row_beats_building_label_name(self) -> None:
        conn = self._build_postprocess_test_db()
        self._insert_contact(
            conn,
            contact_id="marlborough_building_garbage",
            municipality_id="ct_marlborough",
            role_normalized="Building Official",
            role_family="building",
            name="Building",
            title="Building Official",
            department="Building",
            source_url="https://town.example.org/contact_us/index.php",
            source_context="revize:profile_block|page_class=contact_hub",
            page_type="contact_hub",
            email="building@town.org",
            phone="8603001000",
            display_confidence=0.97,
            suspicious_reason=None,
        )
        self._insert_contact(
            conn,
            contact_id="marlborough_building_reconstructed",
            municipality_id="ct_marlborough",
            role_normalized="Building Official",
            role_family="building",
            name="Carl Brown",
            title="Building Official",
            department="Building",
            source_url="https://town.example.org/departments/building/index.php",
            source_context="revize:reconstructed_contact_block|page_class=department_page",
            page_type="department_page",
            email="cbrown@town.org",
            phone="8603001001",
            display_confidence=0.70,
            suspicious_reason=None,
        )
        winner = conn.execute(
            """
            SELECT contact_id
            FROM vw_best_role_per_town
            WHERE municipality_id = ? AND role_normalized = ?
            """,
            ("ct_marlborough", "Building Official"),
        ).fetchone()
        conn.close()
        self.assertIsNotNone(winner)
        self.assertEqual(winner["contact_id"], "marlborough_building_reconstructed")

    def test_reconstructed_row_beats_filling_vacancies(self) -> None:
        conn = self._build_postprocess_test_db()
        self._insert_contact(
            conn,
            contact_id="stafford_tax_garbage",
            municipality_id="ct_stafford",
            role_normalized="Tax Collector",
            role_family="tax_collector",
            name="Filling Vacancies",
            title="Tax Office",
            department="Tax Collector",
            source_url="https://town.example.org/contact_us/index.php",
            source_context="revize:profile_block|page_class=contact_hub",
            page_type="contact_hub",
            email="tax@town.org",
            phone="8604001000",
            display_confidence=0.98,
            suspicious_reason=None,
        )
        self._insert_contact(
            conn,
            contact_id="stafford_tax_reconstructed",
            municipality_id="ct_stafford",
            role_normalized="Tax Collector",
            role_family="tax_collector",
            name="Jane Smith",
            title="Tax Collector",
            department="Tax Collector",
            source_url="https://town.example.org/departments/tax_collector/index.php",
            source_context="revize:reconstructed_contact_block|page_class=department_page",
            page_type="department_page",
            email="jsmith@town.org",
            phone="8604001001",
            display_confidence=0.71,
            suspicious_reason=None,
        )
        winner = conn.execute(
            """
            SELECT contact_id
            FROM vw_best_role_per_town
            WHERE municipality_id = ? AND role_normalized = ?
            """,
            ("ct_stafford", "Tax Collector"),
        ).fetchone()
        conn.close()
        self.assertIsNotNone(winner)
        self.assertEqual(winner["contact_id"], "stafford_tax_reconstructed")

    def test_role_only_name_loses_to_real_name(self) -> None:
        conn = self._build_postprocess_test_db()
        self._insert_contact(
            conn,
            contact_id="role_only_name_row",
            municipality_id="ct_role_only",
            role_normalized="Tax Collector",
            role_family="tax_collector",
            name="Tax Collector",
            title="Tax Collector",
            department="Tax Collector",
            source_url="https://town.example.org/contact_us/index.php",
            source_context="revize:profile_block|page_class=contact_hub",
            page_type="contact_hub",
            email="taxoffice@town.org",
            phone="8605001000",
            display_confidence=0.96,
            suspicious_reason=None,
        )
        self._insert_contact(
            conn,
            contact_id="real_name_row",
            municipality_id="ct_role_only",
            role_normalized="Tax Collector",
            role_family="tax_collector",
            name="Mary Gardner",
            title="Tax Collector",
            department="Tax Collector",
            source_url="https://town.example.org/departments/tax_collector/index.php",
            source_context="revize:reconstructed_contact_block|page_class=department_page",
            page_type="department_page",
            email="mgardner@town.org",
            phone="8605001001",
            display_confidence=0.74,
            suspicious_reason=None,
        )
        winner = conn.execute(
            """
            SELECT contact_id
            FROM vw_best_role_per_town
            WHERE municipality_id = ? AND role_normalized = ?
            """,
            ("ct_role_only", "Tax Collector"),
        ).fetchone()
        conn.close()
        self.assertIsNotNone(winner)
        self.assertEqual(winner["contact_id"], "real_name_row")

    def test_your_link_name_loses_to_real_person(self) -> None:
        conn = self._build_postprocess_test_db()
        self._insert_contact(
            conn,
            contact_id="bad_link_row",
            municipality_id="ct_link_case",
            role_normalized="Assessor",
            role_family="assessor",
            name="Your Link Name",
            title="Assessor",
            department="Assessor",
            source_url="https://town.example.org/contact_us/index.php",
            source_context="revize:profile_block|page_class=contact_hub",
            page_type="contact_hub",
            email="contact@town.org",
            phone="8606001000",
            display_confidence=0.99,
            suspicious_reason=None,
        )
        self._insert_contact(
            conn,
            contact_id="good_link_case",
            municipality_id="ct_link_case",
            role_normalized="Assessor",
            role_family="assessor",
            name="Mary Gardner",
            title="Assessor",
            department="Assessor",
            source_url="https://town.example.org/departments/assessor/index.php",
            source_context="revize:reconstructed_contact_block|page_class=department_page",
            page_type="department_page",
            email="mgardner@town.org",
            phone="8606001001",
            display_confidence=0.73,
            suspicious_reason=None,
        )
        winner = conn.execute(
            """
            SELECT contact_id
            FROM vw_best_role_per_town
            WHERE municipality_id = ? AND role_normalized = ?
            """,
            ("ct_link_case", "Assessor"),
        ).fetchone()
        conn.close()
        self.assertIsNotNone(winner)
        self.assertEqual(winner["contact_id"], "good_link_case")

    def test_only_hard_disqualified_candidate_leaves_role_unresolved(self) -> None:
        conn = self._build_postprocess_test_db()
        self._insert_contact(
            conn,
            contact_id="fallback_only_candidate",
            municipality_id="ct_fallback_case",
            role_normalized="Town Clerk",
            role_family="town_clerk",
            name="Your Link Name",
            title="Town Clerk",
            department="Town Clerk",
            source_url="https://town.example.org/contact_us/index.php",
            source_context="revize:profile_block|page_class=contact_hub",
            page_type="contact_hub",
            email="contact@town.org",
            phone="8607001000",
            display_confidence=0.95,
            suspicious_reason=None,
        )
        winner = conn.execute(
            """
            SELECT contact_id
            FROM vw_best_role_per_town
            WHERE municipality_id = ? AND role_normalized = ?
            """,
            ("ct_fallback_case", "Town Clerk"),
        ).fetchone()
        candidate = conn.execute(
            """
            SELECT candidate_state, invalid_candidate_disqualified
            FROM vw_role_candidates_scored
            WHERE municipality_id = ? AND role_normalized = ?
            """,
            ("ct_fallback_case", "Town Clerk"),
        ).fetchone()
        unresolved = conn.execute(
            """
            SELECT forced_fallback_blocked, top_candidate_winner_block_reason, unresolved_reason
            FROM vw_unresolved_roles
            WHERE municipality_id = ? AND role_normalized = ?
            """,
            ("ct_fallback_case", "Town Clerk"),
        ).fetchone()
        conn.close()
        self.assertIsNone(winner)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["candidate_state"], "disqualified")
        self.assertEqual(int(candidate["invalid_candidate_disqualified"]), 1)
        self.assertIsNotNone(unresolved)
        self.assertEqual(int(unresolved["forced_fallback_blocked"]), 1)
        self.assertEqual(unresolved["top_candidate_winner_block_reason"], "artifact_name")
        self.assertEqual(unresolved["unresolved_reason"], "fragmented_contact_structure")

    def test_weak_artifact_candidate_leaves_role_unresolved(self) -> None:
        conn = self._build_postprocess_test_db()
        self._insert_contact(
            conn,
            contact_id="north_haven_land_use",
            municipality_id="ct_north_haven",
            role_normalized="Building Official",
            role_family="building",
            name="Land Use",
            title="",
            department="",
            source_url="https://town.example.org/contact_us/index.php",
            source_context="revize:profile_block|page_class=contact_hub",
            page_type="contact_hub",
            email="",
            phone="",
            display_confidence=0.45,
            suspicious_reason=None,
        )
        winner = conn.execute(
            """
            SELECT contact_id
            FROM vw_best_role_per_town
            WHERE municipality_id = ? AND role_normalized = ?
            """,
            ("ct_north_haven", "Building Official"),
        ).fetchone()
        candidate = conn.execute(
            """
            SELECT candidate_state, invalid_candidate_disqualified
            FROM vw_role_candidates_scored
            WHERE municipality_id = ? AND role_normalized = ?
            """,
            ("ct_north_haven", "Building Official"),
        ).fetchone()
        unresolved = conn.execute(
            """
            SELECT top_candidate_name, top_candidate_state, forced_fallback_blocked, unresolved_reason
            FROM vw_unresolved_roles
            WHERE municipality_id = ? AND role_normalized = ?
            """,
            ("ct_north_haven", "Building Official"),
        ).fetchone()
        conn.close()
        self.assertIsNone(winner)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["candidate_state"], "disqualified")
        self.assertEqual(int(candidate["invalid_candidate_disqualified"]), 1)
        self.assertIsNotNone(unresolved)
        self.assertEqual(unresolved["top_candidate_name"], "Land Use")
        self.assertEqual(unresolved["top_candidate_state"], "disqualified")
        self.assertEqual(int(unresolved["forced_fallback_blocked"]), 1)
        self.assertEqual(unresolved["unresolved_reason"], "no_person_contact_available")

    def test_department_only_unresolved_is_classified(self) -> None:
        conn = self._build_postprocess_test_db()
        self._insert_contact(
            conn,
            contact_id="dept_only_planning",
            municipality_id="ct_dept_only",
            role_normalized="Planner",
            role_family="planning_zoning",
            name="",
            title="Planning Department",
            department="Planning & Development",
            source_url="https://town.example.org/contact_us/index.php",
            source_context="revize:table_directory|page_class=staff_directory",
            page_type="staff_directory",
            email="",
            phone="",
            display_confidence=0.51,
            suspicious_reason="non_person_role_candidate",
        )
        unresolved = conn.execute(
            """
            SELECT top_candidate_title, unresolved_reason
            FROM vw_unresolved_roles
            WHERE municipality_id = ? AND role_normalized = ?
            """,
            ("ct_dept_only", "Planner"),
        ).fetchone()
        conn.close()
        self.assertIsNotNone(unresolved)
        self.assertEqual(unresolved["top_candidate_title"], "Planning Department")
        self.assertEqual(unresolved["unresolved_reason"], "department_only")

    def test_land_use_label_cannot_win_land_use_role(self) -> None:
        conn = self._build_postprocess_test_db()
        self._insert_contact(
            conn,
            contact_id="land_use_label_only",
            municipality_id="ct_land_use_only",
            role_normalized="Land Use",
            role_family="planning",
            name="Land Use",
            title="",
            department="Planning & Zoning",
            source_url="https://town.example.org/contact_us/index.php",
            source_context="revize:profile_block|page_class=contact_hub",
            page_type="contact_hub",
            email="",
            phone="",
            display_confidence=0.52,
            suspicious_reason=None,
        )
        winner = conn.execute(
            """
            SELECT contact_id
            FROM vw_best_role_per_town
            WHERE municipality_id = ? AND role_normalized = ?
            """,
            ("ct_land_use_only", "Land Use"),
        ).fetchone()
        unresolved = conn.execute(
            """
            SELECT top_candidate_name, forced_fallback_blocked
            FROM vw_unresolved_roles
            WHERE municipality_id = ? AND role_normalized = ?
            """,
            ("ct_land_use_only", "Land Use"),
        ).fetchone()
        conn.close()
        self.assertIsNone(winner)
        self.assertIsNotNone(unresolved)
        self.assertEqual(unresolved["top_candidate_name"], "Land Use")
        self.assertEqual(int(unresolved["forced_fallback_blocked"]), 1)

    def test_structured_role_label_with_contact_details_can_fallback(self) -> None:
        conn = self._build_postprocess_test_db()
        self._insert_contact(
            conn,
            contact_id="structured_building_label",
            municipality_id="ct_structured_building",
            role_normalized="Building Official",
            role_family="building",
            name="Building",
            title="Building Inspector",
            department="Building Department",
            source_url="https://town.example.org/contact_us/index.php",
            source_context="revize:table_directory|page_class=staff_directory",
            page_type="staff_directory",
            email="inspector@town.org",
            phone="8605550202",
            display_confidence=0.77,
            suspicious_reason="invalid_person_name",
        )
        winner = conn.execute(
            """
            SELECT contact_id, candidate_state, forced_fallback
            FROM vw_best_role_per_town
            WHERE municipality_id = ? AND role_normalized = ?
            """,
            ("ct_structured_building", "Building Official"),
        ).fetchone()
        candidate = conn.execute(
            """
            SELECT contact_id, candidate_state, winner_disqualifier_reason, invalid_candidate_disqualified
            FROM vw_role_candidates_scored
            WHERE municipality_id = ? AND role_normalized = ?
            """,
            ("ct_structured_building", "Building Official"),
        ).fetchone()
        unresolved = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM vw_unresolved_roles
            WHERE municipality_id = ? AND role_normalized = ?
            """,
            ("ct_structured_building", "Building Official"),
        ).fetchone()
        conn.close()
        self.assertIsNotNone(winner)
        self.assertEqual(winner["contact_id"], "structured_building_label")
        self.assertEqual(winner["candidate_state"], "candidate_for_review")
        self.assertEqual(int(winner["forced_fallback"]), 1)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["candidate_state"], "candidate_for_review")
        self.assertEqual(candidate["winner_disqualifier_reason"], "")
        self.assertEqual(int(candidate["invalid_candidate_disqualified"]), 0)
        self.assertEqual(int(unresolved["cnt"]), 0)

    def test_vacancy_label_stays_disqualified(self) -> None:
        conn = self._build_postprocess_test_db()
        self._insert_contact(
            conn,
            contact_id="vacancy_tax_label",
            municipality_id="ct_vacancy_label",
            role_normalized="Tax Collector",
            role_family="tax_collector",
            name="Filling Vacancies",
            title="Filling Vacancies for Town Clerk and Tax Collector",
            department="Office of the First Selectman",
            source_url="https://town.example.org/departments/first_selectman.php",
            source_context="revize:labeled_staff|page_class=department_page",
            page_type="department_page",
            email="",
            phone="",
            display_confidence=0.63,
            suspicious_reason="invalid_person_name",
        )
        winner = conn.execute(
            """
            SELECT contact_id
            FROM vw_best_role_per_town
            WHERE municipality_id = ? AND role_normalized = ?
            """,
            ("ct_vacancy_label", "Tax Collector"),
        ).fetchone()
        candidate = conn.execute(
            """
            SELECT candidate_state, winner_disqualifier_reason, invalid_candidate_disqualified
            FROM vw_role_candidates_scored
            WHERE municipality_id = ? AND role_normalized = ?
            """,
            ("ct_vacancy_label", "Tax Collector"),
        ).fetchone()
        conn.close()
        self.assertIsNone(winner)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["candidate_state"], "disqualified")
        self.assertEqual(candidate["winner_disqualifier_reason"], "artifact_name")
        self.assertEqual(int(candidate["invalid_candidate_disqualified"]), 1)

    def test_revize_contact_card_with_direct_contact_clears_weak_source_match(self) -> None:
        conn = self._build_postprocess_test_db()
        self._insert_contact(
            conn,
            contact_id="weak_source_assessor",
            municipality_id="ct_weak_source_allow",
            role_normalized="Assessor",
            role_family="assessor",
            name="Erin O'Connell",
            title="Assessor",
            department="Assessors",
            source_url="https://town.example.org/departments/assessors/index.php",
            source_context="revize:labeled_staff|page_class=department_page",
            page_type="department_page",
            email="assessor@town.org",
            phone="8605550303",
            display_confidence=0.71,
            suspicious_reason=None,
        )
        winner = conn.execute(
            """
            SELECT contact_id, candidate_state, forced_fallback
            FROM vw_best_role_per_town
            WHERE municipality_id = ? AND role_normalized = ?
            """,
            ("ct_weak_source_allow", "Assessor"),
        ).fetchone()
        candidate = conn.execute(
            """
            SELECT candidate_state, winner_disqualifier_reason
            FROM vw_role_candidates_scored
            WHERE municipality_id = ? AND role_normalized = ?
            """,
            ("ct_weak_source_allow", "Assessor"),
        ).fetchone()
        conn.close()
        self.assertIsNotNone(winner)
        self.assertEqual(winner["contact_id"], "weak_source_assessor")
        self.assertEqual(winner["candidate_state"], "candidate_for_review")
        self.assertEqual(int(winner["forced_fallback"]), 1)
        self.assertEqual(candidate["candidate_state"], "candidate_for_review")
        self.assertEqual(candidate["winner_disqualifier_reason"], "")

    def test_blank_name_office_contact_becomes_role_only_fallback(self) -> None:
        conn = self._build_postprocess_test_db()
        self._insert_contact(
            conn,
            contact_id="office_contact_review",
            municipality_id="ct_review",
            entity_type="role",
            role_normalized="Tax Collector",
            role_family="tax_collector",
            name="",
            title="Tax Collector",
            department="Tax Collector",
            source_url="https://town.example.org/departments/tax_collector/index.php",
            source_context="revize:department_contact_block|page_class=department_page",
            page_type="department_page",
            email="taxoffice@town.org",
            phone="8605550101",
            display_confidence=0.66,
            suspicious_reason="non_person_role_candidate",
        )
        winner = conn.execute(
            """
            SELECT contact_id, candidate_state, forced_fallback
            FROM vw_best_role_per_town
            WHERE municipality_id = ? AND role_normalized = ?
            """,
            ("ct_review", "Tax Collector"),
        ).fetchone()
        role_only_candidate = conn.execute(
            """
            SELECT contact_id, candidate_state, winner_disqualifier_reason
            FROM vw_role_candidates_scored
            WHERE municipality_id = ? AND role_normalized = ?
            """,
            ("ct_review", "Tax Collector"),
        ).fetchone()
        unresolved = conn.execute(
            """
            SELECT top_candidate_contact_id, top_candidate_winner_block_reason
            FROM vw_unresolved_roles
            WHERE municipality_id = ? AND role_normalized = ?
            """,
            ("ct_review", "Tax Collector"),
        ).fetchone()
        conn.close()
        self.assertIsNotNone(winner)
        self.assertEqual(winner["contact_id"], "office_contact_review")
        self.assertEqual(winner["candidate_state"], "role_only_fallback")
        self.assertEqual(int(winner["forced_fallback"]), 1)
        self.assertIsNotNone(role_only_candidate)
        self.assertEqual(role_only_candidate["contact_id"], "office_contact_review")
        self.assertEqual(role_only_candidate["candidate_state"], "role_only_fallback")
        self.assertEqual(role_only_candidate["winner_disqualifier_reason"], "blank_name")
        self.assertIsNone(unresolved)

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
                source_context TEXT,
                display_confidence REAL,
                record_rank INTEGER,
                is_likely_noise INTEGER,
                dedupe_key TEXT,
                suspicious_reason TEXT
            )
            """
        )
        conn.execute(FALLBACK_VW_CONTACTS_CLEAN_SQL)
        conn.execute(FALLBACK_VW_ROLE_CANDIDATES_SCORED_SQL)
        conn.execute(FALLBACK_VW_BEST_ROLE_PER_TOWN_SQL)
        conn.execute(FALLBACK_VW_UNRESOLVED_ROLES_SQL)
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
