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

from scripts.blocked_recovery import (
    API_INVENTORY_PATHS,
    API_PROBE_PATHS,
    extract_get_endpoints_from_swagger,
    fetch_swagger_json,
    classify_api_inventory,
    classify_api_presence,
    probe_deep_paths,
    probe_api_endpoints,
    probe_swagger_get_endpoint,
    run_api_inventory,
    score_swagger_endpoint,
    select_api_probe_endpoints,
)
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
    def test_probe_deep_paths_budget_and_dedup(self) -> None:
        categorized = {
            "directory": ["/Directory", "/directory"],
            "finance": ["/finance", "/Finance"],
        }

        def fake_fetch(url: str) -> _FakeFetchResult:
            if url.lower().endswith("/directory"):
                return _FakeFetchResult(200, "text/html", "<html>Directory</html>")
            if url.lower().endswith("/finance"):
                return _FakeFetchResult(403, "text/html", "")
            return _FakeFetchResult(404, "text/html", "")

        rows = probe_deep_paths(
            base_url="https://example.gov",
            categorized_paths=categorized,
            fetch_fn=fake_fetch,
            probe_budget=2,
        )
        self.assertEqual(len(rows), 2)
        by_path = {str(row["path"]).lower(): row for row in rows}
        self.assertEqual(by_path["/directory"]["hit"], 1)
        self.assertEqual(by_path["/finance"]["hit"], 0)

    def test_probe_deep_paths_respects_priority_order(self) -> None:
        categorized = {
            "planning": ["/planning"],
            "directory": ["/directory"],
            "finance": ["/finance"],
        }

        def fake_fetch(url: str) -> _FakeFetchResult:
            return _FakeFetchResult(404, "text/html", "")

        rows = probe_deep_paths(
            base_url="https://example.gov",
            categorized_paths=categorized,
            fetch_fn=fake_fetch,
            probe_budget=3,
        )
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["category"], "directory")
        self.assertEqual(rows[1]["category"], "finance")
        self.assertEqual(rows[2]["category"], "planning")

    def test_probe_deep_paths_supports_early_callback_exit(self) -> None:
        categorized = {
            "directory": ["/directory"],
            "finance": ["/finance"],
            "clerk": ["/clerk"],
        }
        callback_hits: list[str] = []

        def fake_fetch(url: str) -> _FakeFetchResult:
            if url.lower().endswith("/directory"):
                return _FakeFetchResult(200, "text/html", "<html>Directory</html>")
            return _FakeFetchResult(200, "text/html", "<html>Other</html>")

        def on_result(row: dict) -> bool:
            callback_hits.append(str(row.get("path") or ""))
            return str(row.get("path") or "").lower() == "/directory"

        rows = probe_deep_paths(
            base_url="https://example.gov",
            categorized_paths=categorized,
            fetch_fn=fake_fetch,
            probe_budget=10,
            on_result=on_result,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(callback_hits, ["/directory"])

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

    def test_run_api_inventory_collects_expected_rows(self) -> None:
        def fake_fetch(url: str) -> _FakeFetchResult:
            if url.endswith("/swagger/v1/swagger.json"):
                return _FakeFetchResult(
                    200,
                    "application/json",
                    '{"openapi":"3.0.1","info":{"title":"Town API"}}',
                )
            if url.endswith("/swagger/index.html"):
                return _FakeFetchResult(200, "text/html", "<html>Swagger UI</html>")
            return _FakeFetchResult(404, "text/html", "")

        results = run_api_inventory("https://example.gov", fake_fetch)
        self.assertEqual(len(results), len(API_INVENTORY_PATHS))
        swagger_json = next(row for row in results if row["path"] == "/swagger/v1/swagger.json")
        self.assertEqual(swagger_json["status"], 200)
        self.assertEqual(swagger_json["is_json"], True)
        self.assertEqual(swagger_json["has_swagger_markers"], True)

    def test_classify_api_inventory_swagger_json(self) -> None:
        inventory_type, endpoint_count = classify_api_inventory(
            [
                {
                    "path": "/swagger/v1/swagger.json",
                    "status": 200,
                    "is_json": True,
                    "has_swagger_markers": True,
                },
                {
                    "path": "/api",
                    "status": 200,
                    "is_json": False,
                    "has_swagger_markers": False,
                },
            ]
        )
        self.assertEqual(inventory_type, "swagger_json")
        self.assertEqual(endpoint_count, 2)

    def test_fetch_swagger_json_parses_known_json_path(self) -> None:
        def fake_fetch(url: str) -> _FakeFetchResult:
            if url.endswith("/swagger/v1/swagger.json"):
                return _FakeFetchResult(
                    200,
                    "application/json",
                    '{"openapi":"3.0.1","paths":{"/api/directory":{"get":{"summary":"Directory"}}}}',
                )
            return _FakeFetchResult(404, "text/html", "")

        doc = fetch_swagger_json("https://example.gov", ["/swagger/v1/swagger.json"], fake_fetch)
        self.assertIsInstance(doc, dict)
        self.assertIn("paths", doc or {})

    def test_extract_score_select_and_probe_swagger_endpoints(self) -> None:
        swagger_doc = {
            "paths": {
                "/api/directory": {
                    "get": {
                        "summary": "Staff directory",
                        "operationId": "getDirectory",
                        "tags": ["Directory"],
                    }
                },
                "/api/auth/login": {
                    "get": {
                        "summary": "Auth endpoint",
                        "operationId": "login",
                        "tags": ["Auth"],
                    }
                },
                "/api/contact/{id}": {
                    "get": {
                        "summary": "Contact by id",
                        "operationId": "getContactById",
                        "tags": ["Contact"],
                    }
                },
            }
        }
        endpoints = extract_get_endpoints_from_swagger(swagger_doc)
        self.assertEqual(len(endpoints), 3)
        scored = []
        for endpoint in endpoints:
            score, endpoint_class = score_swagger_endpoint(endpoint)
            scored.append({**endpoint, "score": score, "endpoint_class": endpoint_class})
        selected = select_api_probe_endpoints(scored, max_count=3)
        selected_paths = [str(row.get("path") or "") for row in selected]
        self.assertIn("/api/directory", selected_paths)
        self.assertNotIn("/api/auth/login", selected_paths)
        self.assertNotIn("/api/contact/{id}", selected_paths)

        def fake_fetch(url: str) -> _FakeFetchResult:
            if url.endswith("/api/directory"):
                return _FakeFetchResult(200, "application/json", '[{"name":"Clerk"},{"name":"Assessor"}]')
            return _FakeFetchResult(404, "text/html", "")

        probe = probe_swagger_get_endpoint("https://example.gov", "/api/directory", fake_fetch)
        self.assertEqual(probe["status"], 200)
        self.assertEqual(probe["json_root_type"], "list")
        self.assertEqual(probe["likely_structured_data"], 1)

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
                "recovery_result": "api_structured_data_found",
                "notes": (
                    "api_paths_hit=/api,/api/help/index;"
                    "api_hit=1;"
                    "api_type=swagger;"
                    "api_inventory_paths=/api/help/index,/swagger/v1/swagger.json;"
                    "api_inventory_type=swagger_json;"
                    "api_endpoint_count=2;"
                    "swagger_json_path=/swagger/v1/swagger.json;"
                    "documented_get_count=18;"
                    "selected_api_probe_count=3;"
                    "successful_api_probe_count=2;"
                    "likely_structured_endpoint_count=1;"
                    "best_api_endpoint=/api/directory;"
                    "best_api_endpoint_class=contact_like"
                ),
            }
            api_inventory_payload = {
                "municipality_id": "town-1",
                "swagger_json_path": "/swagger/v1/swagger.json",
                "documented_get_count": 22,
                "selected_probe_count": 3,
                "probed_endpoints": [{"path": "/api/directory", "status": 200}],
                "successful_probe_count": 2,
                "likely_structured_endpoint_count": 1,
                "best_endpoint_path": "/api/departments",
                "best_endpoint_class": "department_like",
            }
            conn.execute(
                "INSERT INTO signals (municipality_id, signal_type, value) VALUES (?, ?, ?)",
                ("town-1", "blocked_recovery_status", json.dumps(payload)),
            )
            conn.execute(
                "INSERT INTO signals (municipality_id, signal_type, value) VALUES (?, ?, ?)",
                ("town-1", "api_ingestion_inventory", json.dumps(api_inventory_payload)),
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
        self.assertEqual(row["api_inventory_type"], "swagger_json")
        self.assertEqual(row["api_endpoint_count"], 2)
        self.assertEqual(row["api_inventory_paths"], "/api/help/index,/swagger/v1/swagger.json")
        self.assertEqual(row["swagger_json_path"], "/swagger/v1/swagger.json")
        self.assertEqual(row["documented_get_count"], 22)
        self.assertEqual(row["selected_api_probe_count"], 3)
        self.assertEqual(row["successful_api_probe_count"], 2)
        self.assertEqual(row["likely_structured_endpoint_count"], 1)
        self.assertEqual(row["best_api_endpoint"], "/api/departments")
        self.assertEqual(row["best_api_endpoint_class"], "department_like")
        self.assertEqual(row["recovery_result"], "api_structured_data_found")

    def test_export_parses_deep_path_fields(self) -> None:
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
                "municipality_id": "town-2",
                "batch_id": "batch_1",
                "blocked_reason": "http_forbidden",
                "recovery_mode_attempted": "true",
                "recovery_result": "deep_path_present_no_extract",
                "deep_path_hits": 2,
                "first_deep_category": "directory",
                "first_deep_path": "/Directory.aspx",
                "deep_hit_directory": 1,
                "deep_hit_finance": 1,
                "deep_extraction_path_count": 1,
                "first_deep_extraction_category": "directory",
                "first_deep_extraction_path": "/Directory.aspx",
                "deep_extraction_paths": "/Directory.aspx",
                "notes": (
                    "deep_path_hits=2;"
                    "deep_hit_directory=1;"
                    "deep_hit_finance=1;"
                    "first_deep_path=/Directory.aspx;"
                    "first_deep_category=directory;"
                    "deep_extraction_path_count=1;"
                    "first_deep_extraction_category=directory;"
                    "first_deep_extraction_path=/Directory.aspx;"
                    "deep_extraction_paths=/Directory.aspx"
                ),
            }
            conn.execute(
                "INSERT INTO signals (municipality_id, signal_type, value) VALUES (?, ?, ?)",
                ("town-2", "blocked_recovery_status", json.dumps(payload)),
            )

            rows = build_blocked_recovery_status_rows(
                conn,
                blocked_towns=[
                    {
                        "municipality_id": "town-2",
                        "batch_id": "batch_1",
                        "blocked_reason": "http_forbidden",
                    }
                ],
            )
        finally:
            conn.close()

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["deep_path_hits"], 2)
        self.assertEqual(row["first_deep_category"], "directory")
        self.assertEqual(row["first_deep_path"], "/Directory.aspx")
        self.assertEqual(row["deep_hit_directory"], 1)
        self.assertEqual(row["deep_hit_finance"], 1)
        self.assertEqual(row["deep_extraction_path_count"], 1)
        self.assertEqual(row["first_deep_extraction_category"], "directory")
        self.assertEqual(row["first_deep_extraction_path"], "/Directory.aspx")
        self.assertEqual(row["deep_extraction_paths"], "/Directory.aspx")
        self.assertEqual(row["recovery_result"], "deep_path_present_no_extract")


if __name__ == "__main__":
    unittest.main()
