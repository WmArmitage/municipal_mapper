from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "requests" not in sys.modules:
    class _FakeSession:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}

        def get(self, *args, **kwargs):  # pragma: no cover - not used in these tests
            raise RuntimeError("not implemented")

    class _FakeRequestException(Exception):
        pass

    fake_requests = types.SimpleNamespace(
        Session=_FakeSession,
        RequestException=_FakeRequestException,
        structures=types.SimpleNamespace(CaseInsensitiveDict=dict),
    )
    sys.modules["requests"] = fake_requests

from src.http_client import FetchResult
from src.revize import (
    build_revize_candidate_urls,
    classify_revize_page,
    classify_revize_page_class_for_url,
    discover_revize_department_candidates,
    extract_revize_contacts,
    is_revize_staff_page,
    score_revize_page_class,
    run_revize_strategy_for_municipality,
)


def _build_fetch_result(
    request_url: str,
    status_code: int | None,
    text: str | None = None,
    content_type: str | None = "text/html",
) -> FetchResult:
    error = None
    payload = text
    if status_code is None:
        error = "request_error:test"
        payload = None
    elif status_code >= 400:
        error = "http_error"
    return FetchResult(
        request_url=request_url,
        final_url=request_url,
        status_code=status_code,
        redirect_count=0,
        content_type=content_type,
        text=payload,
        error=error,
        elapsed_ms=2,
        response_headers={},
    )


class RevizeStrategyTests(unittest.TestCase):
    def test_build_revize_candidate_urls_contains_expected_paths_and_harvested_links(self) -> None:
        candidates = build_revize_candidate_urls(
            municipality_homepage="https://www.example.gov",
            harvested_links=[
                {"url": "https://www.example.gov/departments/finance", "label": "Finance Department"},
                {"url": "https://other-domain.example/directory", "label": "Directory"},
            ],
        )
        urls = [str(item.get("url") or "") for item in candidates]
        self.assertEqual(len(urls), len(set(urls)))
        self.assertIn("https://www.example.gov/government/staff_directory.php", urls)
        self.assertIn("https://www.example.gov/departments/staff_directory.php", urls)
        self.assertIn("https://www.example.gov/staff_directory.php", urls)
        self.assertIn("https://www.example.gov/departments/finance", urls)
        self.assertNotIn("https://other-domain.example/directory", urls)

    def test_detect_and_extract_revize_table_directory(self) -> None:
        html = """
        <html>
          <body>
            <h1>Staff Directory</h1>
            <table>
              <thead>
                <tr><th>Department</th><th>Name</th><th>Title</th><th>Phone</th><th>Email</th></tr>
              </thead>
              <tbody>
                <tr>
                  <td>Finance</td>
                  <td>Jane Doe</td>
                  <td>Finance Director</td>
                  <td>(860) 555-1122</td>
                  <td><a href="mailto:jane.doe@example.gov">Email</a></td>
                </tr>
                <tr>
                  <td>Administration</td>
                  <td>Administration</td>
                  <td>Town Administrator</td>
                  <td>(860) 555-1100</td>
                  <td><a href="mailto:admin@example.gov">Email</a></td>
                </tr>
              </tbody>
            </table>
          </body>
        </html>
        """
        matched, signals = is_revize_staff_page(
            html_text=html,
            url="https://www.example.gov/government/staff_directory.php",
        )
        self.assertTrue(matched)
        self.assertIn("table:contact_headers", signals)

        contacts = extract_revize_contacts(
            html_text=html,
            source_url="https://www.example.gov/government/staff_directory.php",
            source_kind="staff_directory_php",
        )
        self.assertGreaterEqual(len(contacts), 1)
        self.assertTrue(any(str(row.get("name") or "") == "Jane Doe" for row in contacts))
        self.assertFalse(any(str(row.get("name") or "").strip().lower() == "administration" for row in contacts))

    def test_classify_profile_block_page(self) -> None:
        html = """
        <html>
          <body>
            <h2>Administration</h2>
            <div class="staff-card">
              <h3>John Smith</h3>
              <p>Town Clerk</p>
              <p>Phone: (860) 555-2200</p>
              <p><a href="mailto:john.smith@example.gov">Email</a></p>
              <a href="/government/staff_profile.php?id=4">Read More</a>
            </div>
            <div class="staff-card">
              <h3>Mary Jones</h3>
              <p>Assistant Town Clerk</p>
              <p>Phone: (860) 555-2201</p>
              <p><a href="mailto:mary.jones@example.gov">Email</a></p>
              <a href="/government/staff_profile.php?id=5">Read More</a>
            </div>
          </body>
        </html>
        """
        classified = classify_revize_page(
            html_text=html,
            url="https://www.example.gov/departments/staff-directory",
            status_code=200,
        )
        self.assertEqual(classified["page_kind"], "staff_directory_or_profile")
        self.assertEqual(classified["source_type"], "profile_block")

    def test_strategy_tracks_revize_diagnostics(self) -> None:
        directory_html = """
        <html>
          <body>
            <h1>Staff Directory</h1>
            <table>
              <thead>
                <tr><th>Name</th><th>Title</th><th>Department</th><th>Phone</th><th>Email</th></tr>
              </thead>
              <tbody>
                <tr>
                  <td>Jane Doe</td>
                  <td>Tax Collector</td>
                  <td>Finance</td>
                  <td>(860) 555-1200</td>
                  <td><a href="mailto:jane.doe@example.gov">Email</a></td>
                </tr>
              </tbody>
            </table>
            <a href="/government/staff_profile.php?id=9">Read More</a>
          </body>
        </html>
        """
        single_profile_html = """
        <html>
          <body>
            <h1>Robert Brown</h1>
            <p>Title: Building Official</p>
            <p>Department: Building Department</p>
            <p>Phone: (860) 555-9900</p>
            <p>Email: robert.brown@example.gov</p>
          </body>
        </html>
        """

        def fake_fetch(url: str, referer: str | None, headers: dict[str, str] | None) -> FetchResult:
            lowered = url.lower()
            if lowered.endswith("/government/staff_directory.php"):
                return _build_fetch_result(url, 200, text=directory_html)
            if "staff_profile.php" in lowered:
                return _build_fetch_result(url, 200, text=single_profile_html)
            return _build_fetch_result(url, 404)

        result = run_revize_strategy_for_municipality(
            municipality_homepage="https://www.example.gov",
            harvested_links=[{"url": "https://www.example.gov/departments/staff-directory", "label": "Staff Directory"}],
            fetch_fn=fake_fetch,
            max_total_candidates=18,
        )

        self.assertGreaterEqual(int(result["candidate_urls_generated_count"]), 1)
        self.assertGreaterEqual(int(result["candidate_urls_attempted_count"]), 1)
        self.assertGreaterEqual(int(result["http_responses_received_count"]), 1)
        self.assertGreaterEqual(int(result["pages_fetched_with_body_count"]), 1)
        self.assertGreaterEqual(int(result["pages_classified_detected_count"]), 1)
        self.assertGreaterEqual(int(result["contacts_total"]), 1)
        self.assertTrue(bool(result.get("revize_pass_produced_contacts")))

        source_counts = dict(result.get("extraction_source_counts") or {})
        self.assertGreaterEqual(int(source_counts.get("table_directory", 0)), 1)
        self.assertGreaterEqual(int(dict(result.get("outcome_counts") or {}).get("not_found", 0)), 1)
        self.assertGreaterEqual(len(list(result.get("matched_urls") or [])), 1)
        self.assertTrue(any(str(row.get("fetch_outcome")) == "ok_detected" for row in list(result.get("attempted_rows") or [])))

    def test_sidebar_staff_h4_span_tel_mailto_extraction(self) -> None:
        html = """
        <html>
          <body>
            <h1>Building Department</h1>
            <aside id="staff-dr">
              <div class="staff">
                <div class="staff-head"><h4>Alex Carter<span>Building Official</span></h4></div>
                <div class="staff-details">
                  <a class="staff-link" href="tel:(860)555-3000">Phone</a>
                  <a class="staff-link" href="mailto:alex.carter@example.gov">Email</a>
                </div>
              </div>
            </aside>
          </body>
        </html>
        """
        rows = extract_revize_contacts(
            html_text=html,
            source_url="https://www.example.gov/departments/building/index.php",
            source_kind="department_page",
        )
        self.assertGreaterEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(str(row.get("name") or ""), "Alex Carter")
        self.assertEqual(str(row.get("title") or ""), "Building Official")
        self.assertEqual(str(row.get("phone") or ""), "8605553000")
        self.assertEqual(str(row.get("email") or ""), "alex.carter@example.gov")
        self.assertEqual(str(row.get("department") or ""), "Building")
        self.assertEqual(str(row.get("revize_source_type") or ""), "sidebar_staff")

    def test_sidebar_staff_vacancy_is_suppressed(self) -> None:
        html = """
        <html>
          <body>
            <aside id="staff-dr">
              <div class="staff">
                <div class="staff-head"><h4>VACANT<span>Building Official</span></h4></div>
                <div class="staff-details">
                  <a class="staff-link" href="tel:(860)555-3000">Phone</a>
                  <a class="staff-link" href="mailto:building@example.gov">Email</a>
                </div>
              </div>
            </aside>
          </body>
        </html>
        """
        result = run_revize_strategy_for_municipality(
            municipality_homepage="https://www.example.gov/departments/building/index.php",
            harvested_links=["https://www.example.gov/departments/building/index.php"],
            fetch_fn=lambda url, referer, headers: _build_fetch_result(url, 200, text=html),
            max_total_candidates=6,
            max_generated_candidates=6,
        )
        rows = list(result.get("contacts") or [])
        self.assertFalse(any(str(row.get("name") or "").strip().lower() == "vacant" for row in rows))
        self.assertGreaterEqual(int(result.get("suppressed_vacancy_rows") or 0), 1)

    def test_department_contact_block_extraction(self) -> None:
        html = """
        <html>
          <body>
            <h1>Building Department</h1>
            <section>
              <h3>Contact Info</h3>
              <p>Phone: (860) 555-4444</p>
              <p>Email: building@example.gov</p>
              <p>17 Main St, Avon, CT</p>
              <p>Hours: Mon-Fri 8:30 AM - 4:30 PM</p>
            </section>
          </body>
        </html>
        """
        rows = extract_revize_contacts(
            html_text=html,
            source_url="https://www.example.gov/departments/building/index.php",
            source_kind="department_page",
        )
        dept_rows = [row for row in rows if str(row.get("revize_source_type") or "") == "department_contact_block"]
        self.assertGreaterEqual(len(dept_rows), 1)
        dept = dept_rows[0]
        self.assertEqual(str(dept.get("name") or ""), "")
        self.assertEqual(str(dept.get("title") or ""), "Department Contact")
        self.assertEqual(str(dept.get("department") or ""), "Building")
        self.assertEqual(str(dept.get("email") or ""), "building@example.gov")

    def test_department_inference_from_url_and_title(self) -> None:
        html = """
        <html>
          <head><title>Town of Example | Building Department</title></head>
          <body>
            <aside id="staff-dr">
              <div class="staff">
                <div class="staff-head"><h4>Jamie Stone<span>Inspector</span></h4></div>
                <div class="staff-details">
                  <a class="staff-link" href="mailto:jamie.stone@example.gov">Email</a>
                </div>
              </div>
            </aside>
          </body>
        </html>
        """
        rows = extract_revize_contacts(
            html_text=html,
            source_url="https://www.example.gov/departments/building/index.php",
            source_kind="department_page",
        )
        self.assertGreaterEqual(len(rows), 1)
        self.assertEqual(str(rows[0].get("department") or ""), "Building")

    def test_footer_blocks_are_ignored(self) -> None:
        html = """
        <html>
          <body>
            <h1>Finance Department</h1>
            <aside id="staff-dr">
              <div class="staff">
                <h4>Jane Analyst<span>Finance Director</span></h4>
                <a class="staff-link" href="mailto:jane.analyst@example.gov">Email</a>
              </div>
            </aside>
            <footer>
              <div class="staff-card">
                <h3>Footer Noise</h3>
                <a href="mailto:footer@example.gov">Email</a>
              </div>
            </footer>
          </body>
        </html>
        """
        result = run_revize_strategy_for_municipality(
            municipality_homepage="https://www.example.gov/departments/finance/index.php",
            harvested_links=["https://www.example.gov/departments/finance/index.php"],
            fetch_fn=lambda url, referer, headers: _build_fetch_result(url, 200, text=html),
            max_total_candidates=6,
            max_generated_candidates=6,
        )
        self.assertGreaterEqual(int(result.get("revize_footer_blocks_ignored") or 0), 1)
        self.assertFalse(any(str(row.get("name") or "").strip() == "Footer Noise" for row in list(result.get("contacts") or [])))

    def test_hours_blocks_are_ignored(self) -> None:
        html = """
        <html>
          <body>
            <h1>Building Department</h1>
            <div id="hours-wrap">
              <h3>Office Hours</h3>
              <p>Mon-Fri 8:30 AM - 4:30 PM</p>
              <p>Phone: (860) 555-9999</p>
            </div>
            <aside id="staff-dr">
              <div class="staff">
                <h4>Jamie Stone<span>Inspector</span></h4>
                <a class="staff-link" href="mailto:jamie.stone@example.gov">Email</a>
              </div>
            </aside>
          </body>
        </html>
        """
        result = run_revize_strategy_for_municipality(
            municipality_homepage="https://www.example.gov/departments/building/index.php",
            harvested_links=["https://www.example.gov/departments/building/index.php"],
            fetch_fn=lambda url, referer, headers: _build_fetch_result(url, 200, text=html),
            max_total_candidates=6,
            max_generated_candidates=6,
        )
        self.assertGreaterEqual(int(result.get("revize_hours_blocks_ignored") or 0), 1)
        self.assertFalse(any(str(row.get("phone") or "") == "8605559999" and not str(row.get("name") or "") for row in list(result.get("contacts") or [])))

    def test_contact_card_pattern_extraction(self) -> None:
        html = """
        <html>
          <body>
            <h1>Administration</h1>
            <div class="card">
              <div class="contact-name">Alex Carter</div>
              <div class="contact-position">Town Manager</div>
            </div>
            <div id="contact-info">
              <a href="tel:(860)555-1010">Phone</a>
              <a href="mailto:alex.carter@example.gov">Email</a>
            </div>
          </body>
        </html>
        """
        rows = extract_revize_contacts(
            html_text=html,
            source_url="https://www.example.gov/government/administration/index.php",
            source_kind="department_page",
        )
        matching = [row for row in rows if str(row.get("name") or "") == "Alex Carter"]
        self.assertTrue(matching)
        self.assertEqual(str(matching[0].get("title") or ""), "Town Manager")
        self.assertEqual(str(matching[0].get("email") or ""), "alex.carter@example.gov")

    def test_inline_staff_list_pattern_extraction(self) -> None:
        html = """
        <html>
          <body>
            <h1>Town Hall</h1>
            <p>Jane Doe, Town Clerk – <a href="mailto:jane.doe@example.gov">Email</a></p>
          </body>
        </html>
        """
        rows = extract_revize_contacts(
            html_text=html,
            source_url="https://www.example.gov/town_hall/staff.php",
            source_kind="department_page",
        )
        matching = [row for row in rows if str(row.get("name") or "") == "Jane Doe"]
        self.assertTrue(matching)
        self.assertEqual(str(matching[0].get("title") or ""), "Town Clerk")
        self.assertEqual(str(matching[0].get("revize_source_type") or ""), "inline_staff_list")

    def test_office_contact_block_does_not_become_person(self) -> None:
        html = """
        <html>
          <body>
            <h1>Finance Department</h1>
            <section id="contact-info">
              <h3>Contact Info</h3>
              <p>Phone: (860) 555-4400</p>
              <p>Email: finance@example.gov</p>
              <p>172 Main Street, Example, CT 06001</p>
            </section>
          </body>
        </html>
        """
        rows = extract_revize_contacts(
            html_text=html,
            source_url="https://www.example.gov/departments/finance/index.php",
            source_kind="department_page",
        )
        office_rows = [row for row in rows if str(row.get("revize_source_type") or "") == "department_contact_block"]
        self.assertTrue(office_rows)
        self.assertTrue(all(not str(row.get("name") or "").strip() for row in office_rows))

    def test_address_like_name_is_rejected(self) -> None:
        html = """
        <html>
          <body>
            <h1>Contact</h1>
            <table>
              <tr><th>Name</th><th>Title</th><th>Phone</th></tr>
              <tr>
                <td>Main Street Killingly</td>
                <td>Town Manager</td>
                <td>(860) 555-2222</td>
              </tr>
            </table>
          </body>
        </html>
        """
        rows = extract_revize_contacts(
            html_text=html,
            source_url="https://www.example.gov/contact-us",
            source_kind="contact_path",
        )
        self.assertFalse(any("Main Street" in str(row.get("name") or "") for row in rows))

    def test_phone_is_preserved_as_digit_string(self) -> None:
        html = """
        <html>
          <body>
            <h1>Staff Contacts</h1>
            <table>
              <tr><th>Name</th><th>Title</th><th>Phone</th><th>Email</th></tr>
              <tr>
                <td>Riley Hart</td>
                <td>Assessor</td>
                <td>(860) 555-3333 ext 12</td>
                <td><a href="mailto:riley.hart@example.gov">Email</a></td>
              </tr>
            </table>
          </body>
        </html>
        """
        rows = extract_revize_contacts(
            html_text=html,
            source_url="https://www.example.gov/departments/assessor/index.php",
            source_kind="department_page",
        )
        self.assertTrue(rows)
        phone = str(rows[0].get("phone") or "")
        self.assertRegex(phone, r"^\d+$")
        self.assertNotIn("E+", phone.upper())

    def test_split_phone_fragment_reconstruction_and_extension_capture(self) -> None:
        html = """
        <html>
          <body>
            <h1>Building &amp; Zoning Enforcement</h1>
            <aside id="staff-dr">
              <div class="staff">
                <div class="staff-head">
                  <h4>Carl Brown<span>Building &amp; Zoning Enforcement Officer</span></h4>
                </div>
                <div class="staff-details">
                  <span>86</span><span>0-376-7060x2109</span>
                  <a href="mailto:buildingdepartment@griswold-ct.org">Email</a>
                </div>
              </div>
            </aside>
          </body>
        </html>
        """
        rows = extract_revize_contacts(
            html_text=html,
            source_url="https://www.example.gov/departments/building/index.php",
            source_kind="department_page",
        )
        matching = [row for row in rows if str(row.get("name") or "") == "Carl Brown"]
        self.assertTrue(matching)
        self.assertEqual(str(matching[0].get("phone") or ""), "8603767060")
        self.assertEqual(str(matching[0].get("phone_ext") or ""), "2109")

    def test_phone_extension_formats_parse_consistently(self) -> None:
        html = """
        <html>
          <body>
            <table>
              <tr><th>Name</th><th>Title</th><th>Phone</th><th>Email</th></tr>
              <tr>
                <td>Kaitlyn Olszewski</td>
                <td>Assistant to the Building Official</td>
                <td>860-376-7060 x2110</td>
                <td><a href="mailto:buildingdepartment@griswold-ct.org">Email</a></td>
              </tr>
              <tr>
                <td>Amy Traversa</td>
                <td>Planning Assistant</td>
                <td>860.376.7060 x2111</td>
                <td><a href="mailto:planningdepartment@griswold-ct.org">Email</a></td>
              </tr>
            </table>
          </body>
        </html>
        """
        rows = extract_revize_contacts(
            html_text=html,
            source_url="https://www.example.gov/departments/building/index.php",
            source_kind="department_page",
        )
        by_name = {str(row.get("name") or ""): row for row in rows if str(row.get("name") or "")}
        self.assertEqual(str(by_name["Kaitlyn Olszewski"].get("phone_ext") or ""), "2110")
        self.assertEqual(str(by_name["Amy Traversa"].get("phone_ext") or ""), "2111")

    def test_valid_name_variants_are_preserved(self) -> None:
        html = """
        <html>
          <body>
            <table>
              <tr><th>Name</th><th>Title</th><th>Email</th></tr>
              <tr><td>Mario J. Tristany, Jr.</td><td>Town Manager</td><td>mario@example.gov</td></tr>
              <tr><td>Alex J. Ricciardone</td><td>Assessor</td><td>alex@example.gov</td></tr>
              <tr><td>Kaitlyn Olszewski</td><td>Assistant to the Building Official</td><td>kaitlyn@example.gov</td></tr>
            </table>
          </body>
        </html>
        """
        rows = extract_revize_contacts(
            html_text=html,
            source_url="https://www.example.gov/departments/administration/index.php",
            source_kind="department_page",
        )
        names = {str(row.get("name") or "") for row in rows}
        self.assertIn("Mario J. Tristany, Jr.", names)
        self.assertIn("Alex J. Ricciardone", names)
        self.assertIn("Kaitlyn Olszewski", names)

    def test_role_only_contact_is_demoted_to_office_row(self) -> None:
        html = """
        <html>
          <body>
            <table>
              <tr><th>Name</th><th>Phone</th><th>Email</th></tr>
              <tr>
                <td>Planning Assistant</td>
                <td>860-376-7060x2112</td>
                <td>planningdepartment@griswold-ct.org</td>
              </tr>
            </table>
          </body>
        </html>
        """
        rows = extract_revize_contacts(
            html_text=html,
            source_url="https://www.example.gov/departments/planning/index.php",
            source_kind="department_page",
        )
        self.assertTrue(rows)
        self.assertTrue(all(not str(row.get("name") or "").strip() for row in rows))
        self.assertTrue(all(str(row.get("revize_source_type") or "") == "department_contact_block" for row in rows))

    def test_invalid_non_person_names_are_rejected(self) -> None:
        html = """
        <html>
          <body>
            <table>
              <tr><th>Name</th><th>Title</th><th>Email</th></tr>
              <tr><td>TAX PAYMENTS</td><td>Finance Director</td><td>tax@example.gov</td></tr>
              <tr><td>Level Ridgefield</td><td>Planner</td><td>planner@example.gov</td></tr>
              <tr><td>Clintonville Elementary</td><td>Manager</td><td>elem@example.gov</td></tr>
              <tr><td>Main Level</td><td>Assessor</td><td>main@example.gov</td></tr>
              <tr><td>Groton Long Point</td><td>Treasurer</td><td>groton@example.gov</td></tr>
              <tr><td>Carl Brown</td><td>Building Official</td><td>carl.brown@example.gov</td></tr>
            </table>
          </body>
        </html>
        """
        rows = extract_revize_contacts(
            html_text=html,
            source_url="https://www.example.gov/departments/building/index.php",
            source_kind="department_page",
        )
        names = {str(row.get("name") or "") for row in rows if str(row.get("name") or "")}
        self.assertEqual(names, {"Carl Brown"})

    def test_breadcrumb_department_context_is_preferred(self) -> None:
        html = """
        <html>
          <body>
            <nav class="breadcrumb">Home > Departments > Building &amp; Zoning Enforcement</nav>
            <h1>Contact Info</h1>
            <aside id="staff-dr">
              <div class="staff">
                <h4>Kaitlyn Olszewski<span>Assistant to the Building Official</span></h4>
                <a class="staff-link" href="mailto:buildingdepartment@griswold-ct.org">Email</a>
              </div>
            </aside>
          </body>
        </html>
        """
        rows = extract_revize_contacts(
            html_text=html,
            source_url="https://www.example.gov/government/contact.php",
            source_kind="department_page",
        )
        self.assertTrue(rows)
        self.assertEqual(str(rows[0].get("department") or ""), "Building & Zoning Enforcement")

    def test_revize_v3_diagnostics_counters_increment(self) -> None:
        html = """
        <html>
          <body>
            <nav class="breadcrumb">Home > Departments > Building &amp; Zoning Enforcement</nav>
            <div id="hours-wrap"><h3>Office Hours</h3><p>Mon-Fri 8:00-4:00</p></div>
            <aside id="staff-dr">
              <div class="staff">
                <h4>Carl Brown<span>Building &amp; Zoning Enforcement Officer</span></h4>
                <div class="staff-details">
                  <span>86</span><span>0-376-7060x2109</span>
                  <a href="mailto:buildingdepartment@griswold-ct.org">Email</a>
                </div>
              </div>
              <div class="staff">
                <h4>Planning Assistant<span>Planning Assistant</span></h4>
                <div class="staff-details">
                  <a href="mailto:planningdepartment@griswold-ct.org">Email</a>
                </div>
              </div>
            </aside>
          </body>
        </html>
        """
        result = run_revize_strategy_for_municipality(
            municipality_homepage="https://www.example.gov/departments/building/index.php",
            harvested_links=["https://www.example.gov/departments/building/index.php"],
            fetch_fn=lambda url, referer, headers: _build_fetch_result(url, 200, text=html),
            max_total_candidates=6,
            max_generated_candidates=6,
        )
        self.assertGreaterEqual(int(result.get("revize_split_text_merged") or 0), 1)
        self.assertGreaterEqual(int(result.get("revize_phone_extensions_parsed") or 0), 1)
        self.assertGreaterEqual(int(result.get("revize_office_contact_rows_classified") or 0), 1)
        self.assertGreaterEqual(int(result.get("revize_person_rows_classified") or 0), 1)
        self.assertGreaterEqual(int(result.get("revize_role_only_rows_demoted") or 0), 1)

    def test_revize_page_classification_by_url_and_content(self) -> None:
        staff_html = """
        <html><body><h1>Staff Contacts</h1>
        <table><tr><th>Name</th><th>Title</th><th>Phone</th><th>Email</th></tr></table>
        </body></html>
        """
        staff_classified = classify_revize_page(
            html_text=staff_html,
            url="https://www.example.gov/staff_directory/index.php",
            status_code=200,
        )
        self.assertEqual(str(staff_classified.get("page_class") or ""), "staff_directory")
        self.assertGreater(float(staff_classified.get("page_priority_score") or 0.0), 0.9)

        hub_html = """
        <html><body><h1>Contact Us</h1><p>Directory of Services and office contacts.</p></body></html>
        """
        hub_classified = classify_revize_page(
            html_text=hub_html,
            url="https://www.example.gov/contact_us/index.php",
            status_code=200,
        )
        self.assertEqual(str(hub_classified.get("page_class") or ""), "contact_hub")

        dept_html = """
        <html><body>
        <nav class="breadcrumb">Home > Departments > Finance</nav>
        <div class="contact-name">Jane Doe</div><div class="contact-position">Finance Director</div>
        </body></html>
        """
        dept_classified = classify_revize_page(
            html_text=dept_html,
            url="https://www.example.gov/departments/finance/index.php",
            status_code=200,
        )
        self.assertEqual(str(dept_classified.get("page_class") or ""), "department_page")

    def test_revize_priority_candidate_ordering_prefers_staff_directory(self) -> None:
        candidates = build_revize_candidate_urls(
            municipality_homepage="https://www.example.gov",
            harvested_links=[
                {"url": "https://www.example.gov/contact_us/index.php", "label": "Contact Us"},
                {"url": "https://www.example.gov/government/directory_of_services/index.php", "label": "Directory of Services"},
                {"url": "https://www.example.gov/departments/finance/index.php", "label": "Finance Department"},
            ],
            max_candidates=40,
        )
        urls = [str(candidate.get("url") or "") for candidate in candidates]
        staff_idx = urls.index("https://www.example.gov/staff_directory/index.php")
        contact_idx = urls.index("https://www.example.gov/contact_us/index.php")
        services_idx = urls.index("https://www.example.gov/government/directory_of_services/index.php")
        self.assertLess(staff_idx, contact_idx)
        self.assertLess(staff_idx, services_idx)
        self.assertGreaterEqual(
            sum(1 for candidate in candidates if int(candidate.get("priority_candidate") or 0) == 1),
            3,
        )

    def test_department_index_discovery_finds_role_pages(self) -> None:
        html = """
        <html><body>
          <a href="/departments/assessor/index.php">Assessor</a>
          <a href="/departments/finance/index.php">Finance Department</a>
          <a href="/parks/index.php">Parks</a>
        </body></html>
        """
        discovered = discover_revize_department_candidates(
            html_text=html,
            base_url="https://www.example.gov/departments/index.php",
            max_candidates=10,
        )
        urls = {str(row.get("url") or "") for row in discovered}
        self.assertIn("https://www.example.gov/departments/assessor/index.php", urls)
        self.assertIn("https://www.example.gov/departments/finance/index.php", urls)
        self.assertTrue(all(str(row.get("candidate_page_class") or "") == "department_page" for row in discovered))

    def test_page_class_scoring_order(self) -> None:
        self.assertGreater(score_revize_page_class("staff_directory"), score_revize_page_class("department_page"))
        self.assertGreater(score_revize_page_class("department_page"), score_revize_page_class("contact_hub"))
        self.assertGreater(score_revize_page_class("contact_hub"), score_revize_page_class("generic"))

    def test_revize_result_tracks_page_class_diagnostics(self) -> None:
        html = """
        <html>
          <body>
            <h1>Staff Contacts</h1>
            <table>
              <tr><th>Name</th><th>Title</th><th>Email</th></tr>
              <tr><td>Jane Doe</td><td>Town Clerk</td><td>jane@example.gov</td></tr>
            </table>
          </body>
        </html>
        """
        result = run_revize_strategy_for_municipality(
            municipality_homepage="https://www.example.gov",
            harvested_links=["https://www.example.gov/staff_directory/index.php"],
            fetch_fn=lambda url, referer, headers: _build_fetch_result(url, 200, text=html),
            max_total_candidates=6,
            max_generated_candidates=10,
        )
        self.assertGreaterEqual(int(result.get("revize_staff_directory_pages_found") or 0), 1)
        self.assertGreaterEqual(int(result.get("revize_rows_from_staff_directory") or 0), 1)
        self.assertIn("staff_directory", dict(result.get("page_class_source_counts") or {}))

    def test_classify_revize_page_class_for_url_helper(self) -> None:
        self.assertEqual(
            classify_revize_page_class_for_url("https://www.example.gov/staff_directory/index.php"),
            "staff_directory",
        )
        self.assertEqual(
            classify_revize_page_class_for_url("https://www.example.gov/departments/finance/index.php"),
            "department_page",
        )
        self.assertEqual(
            classify_revize_page_class_for_url("https://www.example.gov/contact_us/index.php"),
            "contact_hub",
        )


if __name__ == "__main__":
    unittest.main()
