from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.discover import classify_page_type, select_contact_child_links, select_high_value_links


class DiscoverTests(unittest.TestCase):
    def test_classify_page_type_contact_and_official(self) -> None:
        self.assertEqual(
            classify_page_type("https://example.org/government/first-selectman", ""),
            "official_page",
        )
        self.assertEqual(
            classify_page_type("https://example.org/town-clerk", "Town Clerk"),
            "department_page",
        )
        self.assertEqual(
            classify_page_type("https://example.org/staff-directory", "Staff Directory"),
            "directory_page",
        )
        self.assertEqual(
            classify_page_type("https://example.org/pay-taxes-online", "Pay Taxes"),
            "service_page",
        )

    def test_classify_directory_category_page_from_directory_parent(self) -> None:
        self.assertEqual(
            classify_page_type(
                "https://townct.gov/departments/animal-control",
                "Animal Control",
                parent_page_type="directory_page",
            ),
            "directory_category_page",
        )

    def test_select_contact_child_links_same_domain_only(self) -> None:
        links = [
            {"url": "https://townct.gov/town-clerk", "label": "Town Clerk"},
            {"url": "https://townct.gov/animal-control", "label": "Animal Control"},
            {"url": "https://vendor.example.com/portal", "label": "Portal"},
        ]
        selected = select_contact_child_links(
            links,
            municipality_domain="townct.gov",
            parent_page_type="directory_page",
            max_links=10,
        )
        urls = {str(item["url"]) for item in selected}
        self.assertIn("https://townct.gov/town-clerk", urls)
        self.assertIn("https://townct.gov/animal-control", urls)
        self.assertNotIn("https://vendor.example.com/portal", urls)
        child_types = {str(item["page_type"]) for item in selected}
        self.assertIn("directory_category_page", child_types)

    def test_high_value_links_promote_contact_keywords(self) -> None:
        links = [
            {"url": "https://townct.gov/town-clerk", "label": "Town Clerk", "source_url": "https://townct.gov/"},
            {"url": "https://townct.gov/permits", "label": "Permits", "source_url": "https://townct.gov/"},
        ]
        selected = select_high_value_links(links, min_score=2.5, max_links=10)
        urls = {str(item["url"]) for item in selected}
        self.assertIn("https://townct.gov/town-clerk", urls)


if __name__ == "__main__":
    unittest.main()
