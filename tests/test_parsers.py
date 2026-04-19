from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.parsers import (
    classify_service_link,
    extract_contacts,
    extract_locations,
    guess_department,
    infer_email_type,
    normalize_address_text,
)


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

    def test_no_contact_row_without_email_or_phone(self) -> None:
        text = "Public Works Department"
        contacts = extract_contacts(text, "https://example.org/public-works")
        self.assertEqual(contacts, [])

    def test_permit_false_positive_reduced(self) -> None:
        category, _ = classify_service_link(
            "https://example.org/Calendar.aspx?EID=370",
            "Zoning Board of Appeals Meeting CANCELLED",
        )
        self.assertNotEqual(category, "permits")

    def test_role_email_classified_as_role_based(self) -> None:
        self.assertEqual(infer_email_type("bldgofficial@chesterct.org"), "role_based")
        self.assertEqual(infer_email_type("taxcollector@chesterct.org"), "role_based")
        self.assertEqual(infer_email_type("john.smith@chesterct.org"), "direct")

    def test_address_suffix_normalization(self) -> None:
        self.assertEqual(
            normalize_address_text("203 Middlesex Ave."),
            normalize_address_text("203 Middlesex Avenue"),
        )

    def test_hours_extraction_prefers_complete_string(self) -> None:
        text = """
        Office Hours:
        Monday: 9am - 12pm
        and Thursday: 9am - 12pm
        """
        rows = extract_locations(text, "https://example.org/town")
        self.assertEqual(len(rows), 1)
        self.assertIn("Monday", rows[0]["hours"] or "")
        self.assertIn("Thursday", rows[0]["hours"] or "")

    def test_staff_page_allows_name_plus_phone_without_email(self) -> None:
        text = "Jane Doe\nTown Clerk\n(860) 555-1212"
        contacts = extract_contacts(text, "https://example.org/town-clerk", page_type="department_page")
        self.assertGreaterEqual(len(contacts), 1)
        self.assertEqual(contacts[0]["name"], "Jane Doe")
        self.assertEqual(contacts[0]["phone"], "8605551212")

    def test_structured_contact_block_from_html(self) -> None:
        html = """
        <table>
            <tr>
                <td>John Smith</td>
                <td>Assessor</td>
                <td><a href="mailto:john.smith@townct.gov">Email</a></td>
                <td>(860) 555-1212 Ext. 210</td>
            </tr>
        </table>
        """
        contacts = extract_contacts(html, "https://example.org/directory", page_type="directory_page")
        self.assertTrue(any(c.get("phone") == "8605551212" for c in contacts))
        self.assertTrue(any((c.get("title") or "").lower() == "assessor" for c in contacts))

    def test_table_rows_with_shared_email_keep_multiple_people(self) -> None:
        html = """
        <table>
            <thead>
                <tr><th>Name</th><th>Title</th><th>Email</th><th>Phone</th></tr>
            </thead>
            <tbody>
                <tr>
                    <td>Jane Doe</td>
                    <td>Town Clerk</td>
                    <td><a href="mailto:staff@townct.gov">Email</a></td>
                    <td>(860) 555-1111</td>
                </tr>
                <tr>
                    <td>John Roe</td>
                    <td>Assistant Town Clerk</td>
                    <td><a href="mailto:staff@townct.gov">Email</a></td>
                    <td>(860) 555-2222</td>
                </tr>
            </tbody>
        </table>
        """
        contacts = extract_contacts(html, "https://example.org/directory", page_type="directory_page")
        names = {str(c.get("name") or "") for c in contacts if c.get("email") == "staff@townct.gov"}
        self.assertIn("Jane Doe", names)
        self.assertIn("John Roe", names)

    def test_contact_row_includes_address_and_hours_context(self) -> None:
        text = """
        Town Clerk
        123 Main Street
        Office Hours: Monday 8am - 4pm
        clerk@townct.gov
        """
        contacts = extract_contacts(text, "https://example.org/town-clerk", page_type="department_page")
        self.assertGreaterEqual(len(contacts), 1)
        self.assertEqual(contacts[0].get("address"), "123 Main Street")
        self.assertIn("Monday", contacts[0].get("hours") or "")


if __name__ == "__main__":
    unittest.main()
