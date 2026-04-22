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
    extract_revize_contacts,
    is_revize_staff_page,
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


if __name__ == "__main__":
    unittest.main()
