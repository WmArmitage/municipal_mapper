from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.parsers import extract_contacts, guess_department


class ParserDepartmentTests(unittest.TestCase):
    def test_guess_department_positive_examples(self) -> None:
        self.assertEqual(guess_department("Inland Wetlands and Watercourses Agency"), "Inland Wetlands and Watercourses Agency")
        self.assertEqual(guess_department("Planning and Zoning Commission"), "Planning and Zoning Commission")
        self.assertEqual(guess_department("Assessor"), "Assessor")

    def test_guess_department_rejects_navigation_text(self) -> None:
        self.assertIsNone(guess_department("Contact Us"))
        self.assertIsNone(guess_department("Quick Links"))
        self.assertIsNone(guess_department("Staff Directory"))

    def test_extract_department_phone_extension_contact(self) -> None:
        text = "Inland Wetlands and Watercourses Agency (860) 526-0013 Ext. 210"
        contacts = extract_contacts(text, "https://example.org/contact")
        self.assertEqual(len(contacts), 1)

        row = contacts[0]
        self.assertEqual(row["department"], "Inland Wetlands and Watercourses Agency")
        self.assertIsNone(row["name"])
        self.assertEqual(row["phone"], "8605260013")
        self.assertEqual(row["phone_ext"], "210")

    def test_extract_assessor_with_person(self) -> None:
        text = "Assessor John Smith (860) 555-1212 assessor@townct.gov"
        contacts = extract_contacts(text, "https://example.org/assessor")
        self.assertGreaterEqual(len(contacts), 1)

        row = contacts[0]
        self.assertEqual(row["department"], "Assessor")
        self.assertEqual(row["title"], "Assessor")
        self.assertEqual(row["name"], "John Smith")
        self.assertEqual(row["phone"], "8605551212")
        self.assertEqual(row["email"], "assessor@townct.gov")

    def test_avoid_noisy_name_when_department_context_is_strong(self) -> None:
        text = "Building Official Will Try To Return Calls bldgofficial@townct.gov"
        contacts = extract_contacts(text, "https://example.org/building")
        self.assertEqual(len(contacts), 1)
        self.assertIsNone(contacts[0]["name"])
        self.assertEqual(contacts[0]["department"], "Building Official")


if __name__ == "__main__":
    unittest.main()
