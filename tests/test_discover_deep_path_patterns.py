from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.discover_deep_path_patterns import infer_path_category


class DiscoverDeepPathPatternTests(unittest.TestCase):
    def test_path_hints_override_misaligned_role_for_finance(self) -> None:
        category = infer_path_category(
            role_normalized="Town Clerk",
            role_family="town_clerk",
            department_normalized="Town Clerk",
            title="Town Clerk",
            path="/finance-department",
        )
        self.assertEqual(category, "finance")

    def test_directory_path_stays_directory(self) -> None:
        category = infer_path_category(
            role_normalized="Finance Director",
            role_family="finance",
            department_normalized="Finance",
            title="Finance Director",
            path="/Directory.aspx",
        )
        self.assertEqual(category, "directory")

    def test_planning_path_not_misclassified_as_assessor(self) -> None:
        category = infer_path_category(
            role_normalized="Assessor",
            role_family="assessor",
            department_normalized="Assessor",
            title="Assessor",
            path="/planning-zoning",
        )
        self.assertEqual(category, "planning")


if __name__ == "__main__":
    unittest.main()
