from __future__ import annotations

import json
import sqlite3
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

from scripts.blocked_recovery import API_PROBE_PATHS, classify_api_presence, probe_api_endpoints
from scripts.export_batch_qa import build_blocked_recovery_status_rows


class _FakeFetchResult:
    def __init__(
        self,
        status_code: int | None,
        content_type: str | None = None,
        text: str | None = None,
        response_headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.content_type = content_type
        self.text = text
        self.response_headers = response_headers or {}


class BlockedRecoveryApiProbeTests(unittest.TestCase):
    def test_probe_api_endpoints_collects_expected_rows(self) -> None:
        def fake_fetch(url: str) -> _FakeFetchResult:
            if url.endswith("/api/help/index"):
                return _FakeFetchResult(200, "text/html", "<html>Swagger UI</html>")
            if url.endswith("/swagger/index.html"):
                return _FakeFetchResult(200, "text/html", "<html>openapi docs</html>")
            return _FakeFetchResult(404, "text/html", "")

        results = probe_api_endpoints("https://example.gov", fake_fetch)
        self.assertEqual(len(results), len(API_PROBE_PATHS))
        self.assertEqual(results[0]["path"], "/api")
        self.assertEqual(results[0]["status"], 404)
        self.assertEqual(results[-1]["path"], "/swagger/index.html")
        self.assertEqual(results[-1]["status"], 200)

    def test_classify_api_presence_swagger(self) -> None:
        hit, api_type = classify_api_presence(
            [
                {"path": "/api/help/index", "status": 200, "text": "Welcome to Swagger UI"},
                {"path": "/api", "status": 200, "text": "API root"},
            ]
        )
        self.assertEqual(hit, 1)
        self.assertEqual(api_type, "swagger")

    def test_classify_api_presence_rest_root(self) -> None:
        hit, api_type = classify_api_presence(
            [
                {"path": "/api", "status": 200, "text": "service root"},
                {"path": "/swagger", "status": 404, "text": ""},
            ]
        )
        self.assertEqual(hit, 1)
        self.assertEqual(api_type, "rest_root")

    def test_export_parses_api_fields_from_notes(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.execute(
                """
                CREATE TABLE signals (
                    municipality_id TEXT,
                    signal_type TEXT,
                    value TEXT
                )
                """
            )
            payload = {
                "municipality_id": "town-1",
                "batch_id": "batch_1",
                "blocked_reason": "http_forbidden",
                "recovery_mode_attempted": "true",
                "recovery_result": "api_available_no_scrape",
                "notes": "api_paths_hit=/api,/api/help/index;api_hit=1;api_type=swagger",
            }
            conn.execute(
                "INSERT INTO signals (municipality_id, signal_type, value) VALUES (?, ?, ?)",
                ("town-1", "blocked_recovery_status", json.dumps(payload)),
            )

            rows = build_blocked_recovery_status_rows(
                conn,
                blocked_towns=[
                    {
                        "municipality_id": "town-1",
                        "batch_id": "batch_1",
                        "blocked_reason": "http_forbidden",
                    }
                ],
            )
        finally:
            conn.close()

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["api_hit"], 1)
        self.assertEqual(row["api_type"], "swagger")
        self.assertEqual(row["api_paths_hit"], "/api,/api/help/index")
        self.assertEqual(row["recovery_result"], "api_available_no_scrape")


if __name__ == "__main__":
    unittest.main()
