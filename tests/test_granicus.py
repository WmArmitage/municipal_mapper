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

from src.granicus import (
    build_granicus_candidate_urls,
    classify_granicus_page,
    extract_granicus_contacts,
    is_granicus_directory_page,
    run_granicus_strategy_for_municipality,
)
from src.http_client import FetchResult


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


class GranicusStrategyTests(unittest.TestCase):
    def test_build_granicus_candidate_urls_contains_expected_paths_and_is_deduped(self) -> None:
        candidates = build_granicus_candidate_urls("https://www.example.gov", did_max=2)
        urls = [str(item.get("url") or "") for item in candidates]
        self.assertEqual(len(urls), len(set(urls)))
        self.assertIn("https://www.example.gov/Directory.aspx", urls)
        self.assertIn("https://www.example.gov/directory.aspx", urls)
        self.assertIn("https://www.example.gov/Government/Directory.aspx", urls)
        self.assertIn("https://www.example.gov/government/directory.aspx", urls)
        self.assertIn("https://www.example.gov/TownHall/Directory.aspx", urls)
        self.assertIn("https://www.example.gov/town-hall/directory.aspx", urls)
        self.assertIn("https://www.example.gov/Directory.aspx?did=1", urls)
        self.assertIn("https://www.example.gov/Directory.aspx?DID=1", urls)
        self.assertIn("https://www.example.gov/directory.aspx?did=2", urls)

    def test_detect_and_extract_granicus_directory_table(self) -> None:
        html = """
        <html>
          <body>
            <h1>Staff Directory</h1>
            <a href="/Directory.aspx">Return to Staff Directory</a>
            <table>
              <thead>
                <tr><th>Name</th><th>Title</th><th>Department</th><th>Phone</th><th>Email</th></tr>
              </thead>
              <tbody>
                <tr>
                  <td>Jane Doe</td>
                  <td>Town Clerk</td>
                  <td>Town Clerk Office</td>
                  <td>(860) 555-1100</td>
                  <td><a href="mailto:jane.doe@example.gov">Email</a></td>
                </tr>
              </tbody>
            </table>
          </body>
        </html>
        """
        matched, signals = is_granicus_directory_page(html, "https://www.example.gov/Directory.aspx?did=1")
        self.assertTrue(matched)
        self.assertIn("text:staff_directory", signals)

        contacts = extract_granicus_contacts(
            html_text=html,
            source_url="https://www.example.gov/Directory.aspx?did=1",
            source_kind="did_page",
        )
        self.assertGreaterEqual(len(contacts), 1)
        first = contacts[0]
        self.assertEqual(first["phone"], "8605551100")
        self.assertEqual(first["department"], "Town Clerk Office")
        self.assertNotEqual(str(first.get("name") or "").strip().lower(), "email")
        self.assertEqual(first["granicus_source_kind"], "did_page")

    def test_classify_js_shell_page(self) -> None:
        js_shell_html = """
        <html>
          <body>
            <h1>Staff Directory</h1>
            <p>Enter search terms to display a list of entries in the Staff Directory.</p>
            <p>JavaScript is required to use this page.</p>
            <h2>Categories</h2>
            <ul><li>Town Clerk</li><li>Finance</li></ul>
          </body>
        </html>
        """
        classified = classify_granicus_page(
            html_text=js_shell_html,
            url="https://www.example.gov/Directory.aspx?did=1",
            status_code=200,
            response_headers={},
        )
        self.assertEqual(classified["page_kind"], "js_shell")
        self.assertIn("text:js_shell_marker", set(classified["signals"]))

    def test_classify_blocked_page(self) -> None:
        blocked_html = "<html><body>Access Denied by Cloudflare</body></html>"
        classified = classify_granicus_page(
            html_text=blocked_html,
            url="https://www.example.gov/Directory.aspx",
            status_code=403,
            response_headers={"server": "cloudflare"},
        )
        self.assertEqual(classified["page_kind"], "blocked")
        self.assertIn("http:blocked_status", set(classified["signals"]))

    def test_strategy_tracks_fetch_outcomes_and_counters(self) -> None:
        directory_html = """
        <html>
          <body>
            <h1>Staff Directory</h1>
            <table>
              <thead>
                <tr><th>Name</th><th>Title</th><th>Email</th><th>Phone</th></tr>
              </thead>
              <tbody>
                <tr>
                  <td>John Smith</td>
                  <td>Assessor</td>
                  <td><a href="mailto:john.smith@example.gov">Email</a></td>
                  <td>(860) 555-2100</td>
                </tr>
              </tbody>
            </table>
          </body>
        </html>
        """
        js_shell_html = """
        <html>
          <body>
            <h1>Staff Directory</h1>
            <p>Enter search terms to display a list of entries in the Staff Directory.</p>
            <p>JavaScript is required to use this page.</p>
            <h2>Categories</h2>
            <ul><li>Town Clerk</li></ul>
          </body>
        </html>
        """

        def fake_fetch(url: str, referer: str | None, headers: dict[str, str] | None) -> FetchResult:
            if url.endswith("/Directory.aspx"):
                return _build_fetch_result(url, 403, text="<html>Access Denied</html>")
            if "did=1" in url.lower():
                return _build_fetch_result(url, 200, text=directory_html)
            if "did=2" in url.lower():
                return _build_fetch_result(url, 200, text=js_shell_html)
            return _build_fetch_result(url, 404)

        result = run_granicus_strategy_for_municipality(
            municipality_homepage="https://www.example.gov",
            did_max=2,
            fetch_fn=fake_fetch,
            max_total_candidates=30,
        )

        self.assertGreaterEqual(int(result["candidate_urls_generated_count"]), 1)
        self.assertGreaterEqual(int(result["candidate_urls_attempted_count"]), 1)
        self.assertGreaterEqual(int(result["http_responses_received_count"]), 1)
        self.assertGreaterEqual(int(result["pages_fetched_with_body_count"]), 1)
        self.assertGreaterEqual(int(result["pages_classified_blocked_count"]), 1)
        self.assertGreaterEqual(int(result["pages_classified_js_shell_count"]), 1)
        self.assertGreaterEqual(int(result["pages_classified_parseable_directory_count"]), 1)
        self.assertGreaterEqual(len(result["blocked_urls"]), 1)
        self.assertGreaterEqual(len(result["js_shell_urls"]), 1)
        self.assertGreaterEqual(len(result["matched_directory_urls"]), 1)
        self.assertGreaterEqual(int(result["contacts_total"]), 1)
        self.assertGreaterEqual(int(dict(result["outcome_counts"]).get("ok_js_shell", 0)), 1)
        self.assertGreaterEqual(int(dict(result["outcome_counts"]).get("blocked_or_forbidden", 0)), 1)
        self.assertGreaterEqual(int(dict(result["outcome_counts"]).get("not_found", 0)), 1)
        source_counts = dict(result["extraction_source_counts"])
        self.assertGreaterEqual(int(source_counts.get("did_page", 0)), 1)

        attempted_rows = list(result["attempted_rows"])
        self.assertTrue(any(str(row.get("fetch_outcome")) == "ok_directory" for row in attempted_rows))
        self.assertTrue(any(str(row.get("fetch_outcome")) == "ok_js_shell" for row in attempted_rows))
        self.assertTrue(any(str(row.get("fetch_outcome")) == "blocked_or_forbidden" for row in attempted_rows))


if __name__ == "__main__":
    unittest.main()
