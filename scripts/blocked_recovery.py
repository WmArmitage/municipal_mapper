from __future__ import annotations

import json
import re
from urllib.parse import urlparse

from src import db
from src.discover import classify_page_type, extract_links_from_sitemap_xml, is_contact_oriented_page_type
from src.http_client import FetchResult, create_session, fetch_url
from src.normalize import ensure_url_has_scheme, get_domain, make_id, normalize_url
from scripts.run_town import process_text_extractions

BLOCKED_RECOVERY_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

BLOCKED_RECOVERY_STATUS_FIELDS = (
    "municipality_id",
    "batch_id",
    "blocked_reason",
    "recovery_mode_attempted",
    "recovery_result",
    "homepage_status",
    "fallback_status",
    "sitemap_status",
    "robots_status",
    "known_path_hits",
    "directory_hit",
    "directory_fallback_attempted",
    "directory_fallback_contacts",
    "directory_source",
    "deep_path_hits",
    "first_deep_category",
    "first_deep_path",
    "deep_hit_directory",
    "deep_hit_finance",
    "deep_hit_clerk",
    "deep_hit_assessor",
    "deep_hit_tax",
    "deep_hit_building",
    "deep_hit_planning",
    "deep_extraction_path_count",
    "first_deep_extraction_category",
    "first_deep_extraction_path",
    "deep_extraction_paths",
    "deep_path_trust",
    "deep_path_legacy_suspected",
    "recovered_contact_count",
    "recovered_role_winner_count",
    "notes",
)

KNOWN_PATHS = [
    "/Directory",
    "/Directory.aspx",
    "/Directory.aspx?did=",
    "/staff-directory",
    "/directory",
    "/directory.aspx",
    "/StaffDirectory.aspx",
    "/staffdirectory.aspx",
    "/departments",
    "/government",
    "/town-clerk",
    "/tax-collector",
    "/assessor",
    "/building-department",
    "/planning-zoning",
    "/finance-department",
]

PLAYWRIGHT_FALLBACK_PATHS = [
    "/Directory.aspx",
    "/directory.aspx",
]

DEEP_PATH_PROBES = {
    "directory": [
        "/Directory",
        "/Directory.aspx",
        "/directory",
        "/directory.aspx",
        "/StaffDirectory.aspx",
        "/staffdirectory.aspx",
    ],
    "departments_root": [
        "/Departments",
        "/departments",
        "/government",
        "/Government",
    ],
    "finance": [
        "/Departments/Finance",
        "/departments/finance",
        "/finance",
        "/finance-department",
        "/treasurer",
        "/comptroller",
    ],
    "clerk": [
        "/Departments/Town-Clerk",
        "/departments/town-clerk",
        "/town-clerk",
        "/town-clerks-office",
        "/city-clerk",
        "/clerk",
    ],
    "assessor": [
        "/Departments/Assessor",
        "/departments/assessor",
        "/assessor",
        "/assessors-office",
    ],
    "tax": [
        "/Departments/Tax-Collector",
        "/departments/tax-collector",
        "/tax-collector",
        "/tax",
    ],
    "building": [
        "/Departments/Building",
        "/departments/building",
        "/building-department",
        "/building",
        "/inspectional-services",
    ],
    "planning": [
        "/Departments/Planning",
        "/departments/planning",
        "/planning-zoning",
        "/planning",
        "/land-use",
        "/zoning",
    ],
}

DEEP_PATH_EXPORT_CATEGORIES = (
    "directory",
    "finance",
    "clerk",
    "assessor",
    "tax",
    "building",
    "planning",
)

DEEP_PATH_EXTRACTION_CATEGORIES = frozenset(
    {
        "directory",
        "finance",
        "clerk",
        "assessor",
        "tax",
        "building",
        "planning",
    }
)

DEEP_PATH_PAGE_TYPE_BY_CATEGORY = {
    "directory": "staff_directory",
    "departments_root": "department_page",
    "finance": "department_page",
    "clerk": "department_page",
    "assessor": "department_page",
    "tax": "department_page",
    "building": "department_page",
    "planning": "department_page",
}

API_PROBE_PATHS = [
    "/api",
    "/api/",
    "/api/help",
    "/api/help/",
    "/api/help/index",
    "/api/docs",
    "/swagger",
    "/swagger/index.html",
]

API_INVENTORY_PATHS = [
    "/api",
    "/api/help",
    "/api/help/index",
    "/api/docs",
    "/swagger",
    "/swagger/index.html",
    "/swagger/v1/swagger.json",
    "/api/swagger.json",
]

SWAGGER_JSON_PATH_SUFFIXES = (
    "/swagger/v1/swagger.json",
    "/api/swagger.json",
)

API_ENDPOINT_EXCLUDE_KEYWORDS = (
    "auth",
    "login",
    "account",
    "token",
    "admin",
    "health",
    "ping",
    "telemetry",
)

RECOVERY_RESULT_VALUES = {
    "unrecovered_http_block",
    "recovered_known_path",
    "recovered_sitemap_path",
    "recovered_manual_seed_needed",
    "partial_recovery",
    "discovery_failure",
    "recovered_directory_hit",
    "directory_present_no_extract",
    "partial_directory_recovery",
    "api_available_no_scrape",
    "api_inventory_viable",
    "api_structured_data_found",
    "recovered_deep_path",
    "partial_deep_path_recovery",
    "deep_path_present_no_extract",
}

BLOCK_HTTP_STATUS_CODES = {403, 429}
BLOCK_STREAK_STOP_THRESHOLD = 3
DEEP_PATH_PROBE_BUDGET = 20
DEEP_PATH_PRIORITY_ORDER = (
    "directory",
    "finance",
    "clerk",
    "assessor",
    "tax",
    "building",
    "planning",
    "departments_root",
)
DEEP_PATH_MIN_CONTACT_THRESHOLD = 10
DISCOVERED_PROBE_LIMIT = 4
DISCOVERED_HIGH_VALUE_HINTS = (
    "directory",
    "staff",
    "department",
    "government",
    "town-clerk",
    "tax",
    "assessor",
    "building",
    "planning",
    "zoning",
    "finance",
    "contact",
)

DEEP_PATH_TRUST_HINTS: dict[str, tuple[str, ...]] = {
    "directory": ("directory", "staff", "contact"),
    "finance": ("finance", "treasurer", "comptroller"),
    "clerk": ("clerk", "town clerk", "city clerk"),
    "assessor": ("assessor", "assessment"),
    "tax": ("tax", "collector"),
    "building": ("building", "inspection", "code enforcement"),
    "planning": ("planning", "zoning", "land use"),
}

DEEP_PATH_LEGACY_HINTS = (
    "archive",
    "archived",
    "old version",
    "previous version",
    "legacy",
)


def run_blocked_recovery_pass(
    conn,
    municipalities: list[dict],
    batch_id: str = "",
    timeout: int = 20,
    probe_budget: int = 10,
) -> list[dict[str, object]]:
    if not municipalities:
        return []

    municipality_ids = [str(row.get("municipality_id") or "") for row in municipalities]
    diagnostics_by_id = _fetch_latest_crawl_diagnostics(conn, municipality_ids)
    winners_view_exists = _view_exists(conn, "vw_best_role_per_town")

    out: list[dict[str, object]] = []
    for municipality in municipalities:
        municipality_id = str(municipality.get("municipality_id") or "")
        if not municipality_id:
            continue
        diagnostics = diagnostics_by_id.get(municipality_id) or {}
        diagnostic_class = str(diagnostics.get("diagnostic_class") or "").strip().lower()
        homepage_status = _coerce_int(diagnostics.get("http_status"), default=-1)
        if diagnostic_class != "blocked_or_forbidden" and homepage_status != 403:
            continue

        status_row, signal_source_url = _run_recovery_for_municipality(
            conn=conn,
            municipality=municipality,
            diagnostics=diagnostics,
            batch_id=batch_id,
            timeout=timeout,
            probe_budget=max(0, probe_budget),
            winners_view_exists=winners_view_exists,
        )
        _upsert_blocked_recovery_signal(
            conn=conn,
            municipality_id=municipality_id,
            status_row=status_row,
            source_url=signal_source_url,
        )
        out.append(status_row)
        db.commit(conn)

    return out


def _run_recovery_for_municipality(
    conn,
    municipality: dict,
    diagnostics: dict[str, object],
    batch_id: str,
    timeout: int,
    probe_budget: int,
    winners_view_exists: bool,
) -> tuple[dict[str, object], str]:
    municipality_id = str(municipality.get("municipality_id") or "")
    website_url = str(municipality.get("website_url") or "").strip()
    blocked_reason = _derive_blocked_reason(diagnostics)
    status_row: dict[str, object] = {
        "municipality_id": municipality_id,
        "batch_id": batch_id,
        "blocked_reason": blocked_reason,
        "recovery_mode_attempted": "true",
        "recovery_result": "recovered_manual_seed_needed",
        "homepage_status": "",
        "fallback_status": "",
        "sitemap_status": "",
        "robots_status": "",
        "known_path_hits": 0,
        "directory_hit": 0,
        "directory_fallback_attempted": 0,
        "directory_fallback_contacts": 0,
        "directory_source": "",
        "deep_path_hits": 0,
        "first_deep_category": "",
        "first_deep_path": "",
        "deep_hit_directory": 0,
        "deep_hit_finance": 0,
        "deep_hit_clerk": 0,
        "deep_hit_assessor": 0,
        "deep_hit_tax": 0,
        "deep_hit_building": 0,
        "deep_hit_planning": 0,
        "deep_extraction_path_count": 0,
        "first_deep_extraction_category": "",
        "first_deep_extraction_path": "",
        "deep_extraction_paths": "",
        "deep_path_trust": "none",
        "deep_path_legacy_suspected": 0,
        "recovered_contact_count": 0,
        "recovered_role_winner_count": 0,
        "notes": "",
    }
    notes: list[str] = []

    if not website_url:
        status_row["notes"] = "missing_website_url"
        return status_row, ""

    home_target = normalize_url(ensure_url_has_scheme(website_url)) or ensure_url_has_scheme(website_url)
    municipality_domain = str(municipality.get("domain") or get_domain(home_target) or "").strip().lower()

    before_contact_count = _count_contacts(conn, municipality_id)
    before_winner_count = _count_role_winners(conn, municipality_id) if winners_view_exists else 0

    session = create_session()
    block_streak = 0
    blocked_responses = 0
    stopped_early = False
    known_path_hits = 0
    discovered_path_hits = 0
    known_path_results: list[dict[str, object]] = []
    api_probe_results: list[dict[str, object]] = []
    api_hit = 0
    api_type = "none"
    api_inventory_results: list[dict[str, object]] = []
    api_inventory_type = "none"
    api_endpoint_count = 0
    swagger_json_path = ""
    documented_get_count = 0
    selected_api_probe_count = 0
    successful_api_probe_count = 0
    likely_structured_endpoint_count = 0
    best_api_endpoint = ""
    best_api_endpoint_class = ""
    api_probe_details: list[dict[str, object]] = []
    deep_path_results: list[dict[str, object]] = []
    deep_path_hits = 0
    deep_path_hits_by_type = {category: 0 for category in DEEP_PATH_PROBES}
    hit_paths_by_type = {category: [] for category in DEEP_PATH_PROBES}
    first_successful_deep_path = ""
    first_successful_deep_category = ""
    deep_extraction_contact_total = 0
    deep_extraction_path_set: set[str] = set()
    deep_extraction_paths: list[str] = []
    first_deep_extraction_path = ""
    first_deep_extraction_category = ""
    first_directory_hit_url = ""
    first_directory_hit_path = ""
    first_directory_hit_html = ""
    playwright_attempted = False
    playwright_success = 0
    playwright_path = ""
    playwright_directory_hit = False
    diagnostic_class = str(diagnostics.get("diagnostic_class") or "").strip().lower()
    diagnostic_is_blocked = diagnostic_class == "blocked_or_forbidden"
    recovered_from_known = False
    recovered_from_sitemap = False
    latest_source_url = home_target
    followup_referer: str | None = None
    site_root = _site_root(home_target)

    def _attempt_playwright_once(block_detected: bool) -> None:
        nonlocal playwright_attempted
        nonlocal playwright_success
        nonlocal playwright_path
        nonlocal playwright_directory_hit
        nonlocal followup_referer
        nonlocal latest_source_url
        nonlocal recovered_from_known
        nonlocal site_root

        if not block_detected:
            return
        if playwright_attempted:
            return
        if not site_root:
            return

        playwright_attempted = True
        playwright_timeout_ms = max(5000, _coerce_int(timeout, default=20) * 1000)
        for path in PLAYWRIGHT_FALLBACK_PATHS:
            target_url = normalize_url(path, base_url=site_root) or f"{site_root}{path}"
            if not target_url:
                continue
            html = fetch_with_playwright(target_url, timeout=playwright_timeout_ms)
            if not html or len(html) <= 500:
                continue

            playwright_success = 1
            playwright_path = path
            if "directory" in path.lower():
                playwright_directory_hit = True

            active_url = normalize_url(target_url) or target_url
            followup_referer = active_url
            latest_source_url = active_url
            extracted_contacts, _ = process_text_extractions(
                conn,
                municipality_id,
                active_url,
                html,
                page_type="playwright_fallback",
            )
            if extracted_contacts > 0:
                recovered_from_known = True
            break
        if not playwright_path:
            playwright_path = PLAYWRIGHT_FALLBACK_PATHS[0]

    home_result = fetch_url(
        home_target,
        timeout=timeout,
        session=session,
        referer=None,
        retries=0,
        request_headers=BLOCKED_RECOVERY_HEADERS,
    )
    status_row["homepage_status"] = _status_label(home_result)
    followup_referer = normalize_url(home_result.final_url or home_target) or (home_result.final_url or home_target)
    latest_source_url = followup_referer or latest_source_url
    site_root = _site_root(followup_referer or home_target) or site_root
    home_status_code = _coerce_int(home_result.status_code, default=-1)
    if home_status_code == 403 or diagnostic_is_blocked:
        _attempt_playwright_once(block_detected=True)
    block_streak, blocked_responses = _update_block_tracking(home_result, block_streak, blocked_responses)

    fallback_url = _build_fallback_url(followup_referer or home_target)
    if fallback_url:
        fallback_result = fetch_url(
            fallback_url,
            timeout=timeout,
            session=session,
            referer=followup_referer,
            retries=0,
            request_headers=BLOCKED_RECOVERY_HEADERS,
        )
        status_row["fallback_status"] = _status_label(fallback_result)
        fallback_final = normalize_url(fallback_result.final_url or fallback_url) or (fallback_result.final_url or fallback_url)
        followup_referer = fallback_final or followup_referer
        latest_source_url = fallback_final or latest_source_url
        site_root = _site_root(followup_referer or home_target) or site_root
        fallback_status_code = _coerce_int(fallback_result.status_code, default=-1)
        if fallback_status_code == 403 or diagnostic_is_blocked:
            _attempt_playwright_once(block_detected=True)
        block_streak, blocked_responses = _update_block_tracking(fallback_result, block_streak, blocked_responses)
    else:
        status_row["fallback_status"] = "not_attempted"

    if site_root:
        api_probe_results = probe_api_endpoints(
            base_url=site_root,
            fetch_fn=lambda target_url: fetch_url(
                target_url,
                timeout=timeout,
                session=session,
                referer=followup_referer,
                retries=0,
                request_headers=BLOCKED_RECOVERY_HEADERS,
            ),
        )
        api_hit, api_type = classify_api_presence(api_probe_results)
        if api_hit == 1:
            api_inventory_results = run_api_inventory(
                base_url=site_root,
                fetch_fn=lambda target_url: fetch_url(
                    target_url,
                    timeout=timeout,
                    session=session,
                    referer=followup_referer,
                    retries=0,
                    request_headers=BLOCKED_RECOVERY_HEADERS,
                ),
            )
            api_inventory_type, api_endpoint_count = classify_api_inventory(api_inventory_results)
            if api_inventory_type == "swagger_json":
                api_inventory_paths = [
                    str(result.get("path") or "")
                    for result in api_inventory_results
                    if _coerce_int(result.get("status"), default=-1) == 200 and str(result.get("path") or "").strip()
                ]
                swagger_doc, swagger_json_path = _fetch_swagger_json_with_path(
                    base_url=site_root,
                    inventory_paths=api_inventory_paths,
                    fetch_fn=lambda target_url: fetch_url(
                        target_url,
                        timeout=timeout,
                        session=session,
                        referer=followup_referer,
                        retries=0,
                        request_headers=BLOCKED_RECOVERY_HEADERS,
                    ),
                )
                if swagger_doc:
                    documented_endpoints = extract_get_endpoints_from_swagger(swagger_doc)
                    scored_endpoints: list[dict[str, object]] = []
                    for endpoint in documented_endpoints:
                        score, endpoint_class = score_swagger_endpoint(endpoint)
                        scored_endpoints.append(
                            {
                                **endpoint,
                                "score": score,
                                "endpoint_class": endpoint_class,
                            }
                        )
                    documented_get_count = len(scored_endpoints)
                    selected_endpoints = select_api_probe_endpoints(scored_endpoints, max_count=3)
                    selected_api_probe_count = len(selected_endpoints)
                    for endpoint in selected_endpoints:
                        endpoint_path = str(endpoint.get("path") or "")
                        probe_result = probe_swagger_get_endpoint(
                            base_url=site_root,
                            endpoint_path=endpoint_path,
                            fetch_fn=lambda target_url: fetch_url(
                                target_url,
                                timeout=timeout,
                                session=session,
                                referer=followup_referer,
                                retries=0,
                                request_headers=BLOCKED_RECOVERY_HEADERS,
                            ),
                        )
                        if _coerce_int(probe_result.get("status"), default=-1) == 200:
                            successful_api_probe_count += 1
                        if _coerce_int(probe_result.get("likely_structured_data"), default=0) == 1:
                            likely_structured_endpoint_count += 1
                        api_probe_details.append(
                            {
                                "path": endpoint_path,
                                "endpoint_class": str(endpoint.get("endpoint_class") or "other"),
                                "score": _coerce_int(endpoint.get("score"), default=0),
                                "status": probe_result.get("status", ""),
                                "content_type": str(probe_result.get("content_type") or ""),
                                "json_root_type": str(probe_result.get("json_root_type") or "other"),
                                "item_count_estimate": _coerce_int(probe_result.get("item_count_estimate"), default=0),
                                "likely_structured_data": _coerce_int(probe_result.get("likely_structured_data"), default=0),
                            }
                        )
                    preferred_probes = [
                        row
                        for row in api_probe_details
                        if _coerce_int(row.get("likely_structured_data"), default=0) == 1
                    ]
                    ranked_probes = preferred_probes or api_probe_details
                    if ranked_probes:
                        ranked_probes.sort(
                            key=lambda row: (
                                _coerce_int(row.get("likely_structured_data"), default=0),
                                _coerce_int(row.get("score"), default=0),
                            ),
                            reverse=True,
                        )
                        best_api_endpoint = str(ranked_probes[0].get("path") or "")
                        best_api_endpoint_class = str(ranked_probes[0].get("endpoint_class") or "other")
                _upsert_api_ingestion_inventory_signal(
                    conn=conn,
                    municipality_id=municipality_id,
                    source_url=(normalize_url(swagger_json_path, base_url=site_root) or site_root),
                    payload={
                        "municipality_id": municipality_id,
                        "swagger_json_path": swagger_json_path,
                        "documented_get_count": documented_get_count,
                        "selected_probe_count": selected_api_probe_count,
                        "probed_endpoints": api_probe_details,
                        "successful_probe_count": successful_api_probe_count,
                        "likely_structured_endpoint_count": likely_structured_endpoint_count,
                        "best_endpoint_path": best_api_endpoint,
                        "best_endpoint_class": best_api_endpoint_class,
                    },
                )

    if site_root and block_streak < BLOCK_STREAK_STOP_THRESHOLD:
        def _handle_deep_probe_result(row: dict[str, object]) -> bool:
            nonlocal followup_referer
            nonlocal latest_source_url
            nonlocal deep_extraction_contact_total
            nonlocal first_deep_extraction_path
            nonlocal first_deep_extraction_category

            if _coerce_int(row.get("hit"), default=0) != 1:
                return False

            active_url = normalize_url(str(row.get("url") or "")) or str(row.get("url") or "")
            if active_url:
                followup_referer = active_url
                latest_source_url = active_url

            category = str(row.get("category") or "")
            if category not in DEEP_PATH_EXTRACTION_CATEGORIES:
                return False

            content_type = str(row.get("content_type") or "").lower()
            if "html" not in content_type:
                return False

            html_text = str(row.get("text") or "")
            if len(html_text) < 500:
                return False

            source_url = str(row.get("url") or "")
            if not source_url:
                return False

            deep_page_type = DEEP_PATH_PAGE_TYPE_BY_CATEGORY.get(category, "department_page")
            extracted_contacts, _ = process_text_extractions(
                conn,
                municipality_id,
                source_url,
                html_text,
                page_type=deep_page_type,
            )
            if extracted_contacts <= 0:
                return False

            deep_extraction_contact_total += extracted_contacts
            extraction_marker = str(row.get("path") or source_url)
            if extraction_marker and extraction_marker not in deep_extraction_path_set:
                deep_extraction_path_set.add(extraction_marker)
                deep_extraction_paths.append(extraction_marker)
            if not first_deep_extraction_path:
                first_deep_extraction_path = extraction_marker
                first_deep_extraction_category = category

            if deep_extraction_contact_total >= DEEP_PATH_MIN_CONTACT_THRESHOLD:
                return True
            return False

        deep_path_results = probe_deep_paths(
            base_url=site_root,
            categorized_paths=DEEP_PATH_PROBES,
            fetch_fn=lambda target_url: fetch_url(
                target_url,
                timeout=timeout,
                session=session,
                referer=followup_referer,
                retries=0,
                request_headers=BLOCKED_RECOVERY_HEADERS,
            ),
            probe_budget=DEEP_PATH_PROBE_BUDGET,
            on_result=_handle_deep_probe_result,
        )
        deep_path_hits = sum(_coerce_int(row.get("hit"), default=0) for row in deep_path_results)
        deep_path_hits_by_type = {
            category: sum(
                1
                for row in deep_path_results
                if str(row.get("category") or "") == category and _coerce_int(row.get("hit"), default=0) == 1
            )
            for category in DEEP_PATH_PROBES
        }
        hit_paths_by_type = {
            category: [
                str(row.get("path") or "")
                for row in deep_path_results
                if str(row.get("category") or "") == category
                and _coerce_int(row.get("hit"), default=0) == 1
                and str(row.get("path") or "").strip()
            ]
            for category in DEEP_PATH_PROBES
        }
        for row in deep_path_results:
            if _coerce_int(row.get("hit"), default=0) != 1:
                continue
            first_successful_deep_path = str(row.get("path") or "")
            first_successful_deep_category = str(row.get("category") or "")
            break

    discovered_candidates: list[tuple[str, str]] = []

    sitemap_status = "not_attempted"
    if site_root and block_streak < BLOCK_STREAK_STOP_THRESHOLD:
        sitemap_url = normalize_url("/sitemap.xml", base_url=site_root) or f"{site_root}/sitemap.xml"
        sitemap_result = fetch_url(
            sitemap_url,
            timeout=timeout,
            session=session,
            referer=followup_referer,
            retries=0,
            request_headers=BLOCKED_RECOVERY_HEADERS,
        )
        sitemap_status = _status_label(sitemap_result)
        block_streak, blocked_responses = _update_block_tracking(sitemap_result, block_streak, blocked_responses)
        if sitemap_result.ok and sitemap_result.text:
            sitemap_links = [str(link.get("url") or "") for link in extract_links_from_sitemap_xml(sitemap_result.text)]
            discovered_candidates.extend((url, "sitemap") for url in sitemap_links)
    status_row["sitemap_status"] = sitemap_status

    robots_status = "not_attempted"
    if site_root and block_streak < BLOCK_STREAK_STOP_THRESHOLD:
        robots_url = normalize_url("/robots.txt", base_url=site_root) or f"{site_root}/robots.txt"
        robots_result = fetch_url(
            robots_url,
            timeout=timeout,
            session=session,
            referer=followup_referer,
            retries=0,
            request_headers=BLOCKED_RECOVERY_HEADERS,
        )
        robots_status = _status_label(robots_result)
        block_streak, blocked_responses = _update_block_tracking(robots_result, block_streak, blocked_responses)
        if robots_result.ok and robots_result.text:
            robots_links = _extract_urls_from_robots(robots_result.text, site_root)
            discovered_candidates.extend((url, "robots") for url in robots_links)
    status_row["robots_status"] = robots_status

    discovered_probe_urls = _select_discovered_probe_urls(
        candidates=discovered_candidates,
        municipality_domain=municipality_domain,
        limit=DISCOVERED_PROBE_LIMIT,
    )
    known_probe_entries = _build_known_probe_urls(site_root or home_target)
    known_probe_urls = [entry["url"] for entry in known_probe_entries]
    known_probe_set = set(known_probe_urls)
    known_probe_path_by_url = {entry["url"]: entry["path"] for entry in known_probe_entries}

    probe_queue: list[tuple[str, str]] = []
    seen_probe_urls: set[str] = set()
    for url in discovered_probe_urls:
        if url in seen_probe_urls:
            continue
        seen_probe_urls.add(url)
        probe_queue.append(("sitemap", url))
    for url in known_probe_urls:
        if url in seen_probe_urls:
            continue
        seen_probe_urls.add(url)
        probe_queue.append(("known", url))

    probes_used = 0
    for source_kind, target_url in probe_queue:
        if probes_used >= probe_budget:
            break
        if block_streak >= BLOCK_STREAK_STOP_THRESHOLD:
            stopped_early = True
            break

        probe_referer = followup_referer or site_root or home_target
        result = fetch_url(
            target_url,
            timeout=timeout,
            session=session,
            referer=probe_referer,
            retries=0,
            request_headers=BLOCKED_RECOVERY_HEADERS,
        )
        probes_used += 1
        block_streak, blocked_responses = _update_block_tracking(result, block_streak, blocked_responses)
        if source_kind == "known":
            path_value = known_probe_path_by_url.get(target_url, "")
            known_path_results.append(
                {
                    "path": path_value,
                    "status": result.status_code if result.status_code is not None else "",
                    "hit": 1 if result.status_code == 200 else 0,
                }
            )
            if (
                not first_directory_hit_html
                and is_directory_hit(path_value, result.status_code)
                and bool(result.text)
            ):
                first_directory_hit_path = path_value
                first_directory_hit_url = normalize_url(result.final_url or target_url) or (result.final_url or target_url)
                first_directory_hit_html = result.text or ""

        if not result.ok or not result.text:
            continue

        active_url = normalize_url(result.final_url or target_url) or (result.final_url or target_url)
        followup_referer = active_url
        latest_source_url = active_url
        page_type = classify_page_type(active_url, active_url.rsplit("/", 1)[-1])
        page_id = make_id("page", municipality_id, active_url)
        db.upsert_page(
            conn,
            {
                "page_id": page_id,
                "municipality_id": municipality_id,
                "url": active_url,
                "page_type": page_type,
                "title": None,
                "discovered_from": f"blocked_recovery:{source_kind}",
            },
        )
        extracted_contacts = 0
        if not (source_kind == "known" and is_directory_hit(known_probe_path_by_url.get(target_url, ""), result.status_code)):
            extracted_contacts, _ = process_text_extractions(
                conn,
                municipality_id,
                active_url,
                result.text,
                page_type=page_type,
            )

        if target_url in known_probe_set:
            if extracted_contacts > 0:
                recovered_from_known = True
        else:
            discovered_path_hits += 1
            if extracted_contacts > 0:
                recovered_from_sitemap = True

    if stopped_early:
        notes.append("stopped_after_repeated_403_or_429")

    known_path_hits = sum(_coerce_int(result.get("hit")) for result in known_path_results)
    directory_hit = playwright_directory_hit or any(
        is_directory_hit(str(result.get("path") or ""), _coerce_int(result.get("status"), default=-1))
        for result in known_path_results
    )
    deep_path_trust, deep_path_legacy_suspected = assess_deep_path_recovery_trust(
        deep_path_results=deep_path_results,
        extracted_paths=deep_extraction_paths,
    )
    hit_paths = [
        str(result.get("path") or "")
        for result in known_path_results
        if _coerce_int(result.get("hit")) == 1 and str(result.get("path") or "").strip()
    ]
    notes_parts: list[str] = []
    if hit_paths:
        notes_parts.append(f"known_paths_hit={','.join(hit_paths)}")
    api_paths_hit = [
        str(result.get("path") or "")
        for result in api_probe_results
        if _coerce_int(result.get("status"), default=-1) == 200 and str(result.get("path") or "").strip()
    ]
    if api_paths_hit:
        notes_parts.append(f"api_paths_hit={','.join(api_paths_hit)}")
    if api_hit:
        notes_parts.append("api_hit=1")
    if api_type != "none":
        notes_parts.append(f"api_type={api_type}")
    if api_hit == 1:
        api_inventory_paths = [
            str(result.get("path") or "")
            for result in api_inventory_results
            if _coerce_int(result.get("status"), default=-1) == 200 and str(result.get("path") or "").strip()
        ]
        if api_inventory_paths:
            notes_parts.append(f"api_inventory_paths={','.join(api_inventory_paths)}")
        notes_parts.append(f"api_inventory_type={api_inventory_type}")
        notes_parts.append(f"api_endpoint_count={api_endpoint_count}")
    if swagger_json_path:
        notes_parts.append(f"swagger_json_path={swagger_json_path}")
    if api_hit == 1 and api_inventory_type == "swagger_json":
        notes_parts.append(f"documented_get_count={documented_get_count}")
        notes_parts.append(f"selected_api_probe_count={selected_api_probe_count}")
        notes_parts.append(f"successful_api_probe_count={successful_api_probe_count}")
        notes_parts.append(f"likely_structured_endpoint_count={likely_structured_endpoint_count}")
        if best_api_endpoint:
            notes_parts.append(f"best_api_endpoint={best_api_endpoint}")
        if best_api_endpoint_class:
            notes_parts.append(f"best_api_endpoint_class={best_api_endpoint_class}")
    if deep_path_hits > 0:
        notes_parts.append(f"deep_path_hits={deep_path_hits}")
        notes_parts.append(f"deep_path_trust={deep_path_trust}")
        notes_parts.append(f"deep_path_legacy_suspected={deep_path_legacy_suspected}")
    for category, hits in deep_path_hits_by_type.items():
        if _coerce_int(hits, default=0) > 0:
            notes_parts.append(f"deep_hit_{category}={hits}")
    for category, paths in hit_paths_by_type.items():
        clean_paths = [path for path in paths if str(path or "").strip()]
        if clean_paths:
            notes_parts.append(f"deep_paths_{category}={','.join(clean_paths)}")
    if first_successful_deep_path:
        notes_parts.append(f"first_deep_path={first_successful_deep_path}")
    if first_successful_deep_category:
        notes_parts.append(f"first_deep_category={first_successful_deep_category}")
    if deep_extraction_paths:
        notes_parts.append(f"deep_extraction_paths={','.join(deep_extraction_paths)}")
    notes_parts.append(f"deep_extraction_path_count={len(deep_extraction_paths)}")
    if first_deep_extraction_path:
        notes_parts.append(f"first_deep_extraction_path={first_deep_extraction_path}")
    if first_deep_extraction_category:
        notes_parts.append(f"first_deep_extraction_category={first_deep_extraction_category}")
    if directory_hit:
        notes_parts.append("directory_hit=1")
    if playwright_attempted:
        notes_parts.append("playwright_attempted=1")
        notes_parts.append(f"playwright_success={playwright_success}")
        if playwright_path:
            notes_parts.append(f"playwright_path={playwright_path}")
    status_row["directory_hit"] = 1 if directory_hit else 0
    status_row["deep_path_hits"] = deep_path_hits
    status_row["first_deep_category"] = first_successful_deep_category
    status_row["first_deep_path"] = first_successful_deep_path
    status_row["deep_extraction_path_count"] = len(deep_extraction_paths)
    status_row["first_deep_extraction_category"] = first_deep_extraction_category
    status_row["first_deep_extraction_path"] = first_deep_extraction_path
    status_row["deep_extraction_paths"] = ",".join(deep_extraction_paths)
    status_row["deep_path_trust"] = deep_path_trust
    status_row["deep_path_legacy_suspected"] = deep_path_legacy_suspected
    for category in DEEP_PATH_EXPORT_CATEGORIES:
        status_row[f"deep_hit_{category}"] = _coerce_int(deep_path_hits_by_type.get(category), default=0)

    directory_fallback_attempted = 0
    directory_fallback_contacts = 0
    directory_fallback_note = ""
    if directory_hit and first_directory_hit_html and first_directory_hit_url:
        directory_fallback_attempted = 1
        directory_fallback_contacts, _, directory_fallback_note = run_directory_hit_fallback(
            conn=conn,
            municipality_id=municipality_id,
            source_url=first_directory_hit_url,
            html_text=first_directory_hit_html,
        )
    status_row["directory_fallback_attempted"] = directory_fallback_attempted
    status_row["directory_fallback_contacts"] = directory_fallback_contacts
    status_row["directory_source"] = first_directory_hit_path or ""
    if directory_fallback_attempted:
        notes_parts.append("directory_fallback_attempted=1")
        notes_parts.append(f"directory_fallback_contacts={directory_fallback_contacts}")
        if first_directory_hit_path:
            notes_parts.append(f"directory_source={first_directory_hit_path}")
        if directory_fallback_note:
            notes_parts.append(directory_fallback_note)

    status_row["known_path_hits"] = known_path_hits

    if winners_view_exists:
        after_winner_count = _count_role_winners(conn, municipality_id)
        status_row["recovered_role_winner_count"] = max(0, after_winner_count - before_winner_count)
    else:
        notes.append("vw_best_role_per_town_missing")

    has_any_200 = (
        _status_is_200(status_row.get("homepage_status"))
        or _status_is_200(status_row.get("fallback_status"))
        or any(_coerce_int(result.get("status"), default=-1) == 200 for result in known_path_results)
    )

    status_row["recovered_contact_count"] = max(0, _count_contacts(conn, municipality_id) - before_contact_count)
    recovered_contact_count = _coerce_int(status_row["recovered_contact_count"])
    recovered_role_winner_count = _coerce_int(status_row.get("recovered_role_winner_count"))

    if directory_hit:
        if recovered_contact_count > 0 and recovered_role_winner_count == 0:
            status_row["recovery_result"] = "partial_directory_recovery"
        elif recovered_contact_count > 0:
            status_row["recovery_result"] = "recovered_directory_hit"
        else:
            status_row["recovery_result"] = "directory_present_no_extract"
    elif recovered_contact_count > 0:
        if recovered_from_sitemap:
            status_row["recovery_result"] = "recovered_sitemap_path"
        elif recovered_from_known:
            status_row["recovery_result"] = "recovered_known_path"
        else:
            status_row["recovery_result"] = "partial_recovery"
    elif has_any_200 and known_path_hits > 0 and recovered_contact_count == 0:
        status_row["recovery_result"] = "discovery_failure"
    elif known_path_hits > 0 or discovered_path_hits > 0:
        status_row["recovery_result"] = "partial_recovery"
    elif blocked_responses >= BLOCK_STREAK_STOP_THRESHOLD:
        status_row["recovery_result"] = "unrecovered_http_block"
    else:
        status_row["recovery_result"] = "recovered_manual_seed_needed"

    if deep_path_hits > 0:
        if recovered_contact_count > 0 and recovered_role_winner_count > 0:
            status_row["recovery_result"] = "recovered_deep_path"
        elif recovered_contact_count > 0 and recovered_role_winner_count == 0:
            status_row["recovery_result"] = "partial_deep_path_recovery"
        elif recovered_contact_count == 0:
            status_row["recovery_result"] = "deep_path_present_no_extract"
    elif (
        recovered_contact_count == 0
        and successful_api_probe_count > 0
        and likely_structured_endpoint_count > 0
    ):
        status_row["recovery_result"] = "api_structured_data_found"
    elif recovered_contact_count == 0 and swagger_json_path and documented_get_count > 0:
        status_row["recovery_result"] = "api_inventory_viable"
    elif api_hit == 1 and recovered_contact_count == 0:
        status_row["recovery_result"] = "api_available_no_scrape"

    if status_row["recovery_result"] not in RECOVERY_RESULT_VALUES:
        status_row["recovery_result"] = "partial_recovery"
    if notes:
        notes_parts.extend(sorted(set(notes)))
    if notes_parts:
        status_row["notes"] = ";".join(notes_parts)
    else:
        status_row["notes"] = str(diagnostics.get("notes") or "")
    return status_row, latest_source_url


def _fetch_latest_crawl_diagnostics(conn, municipality_ids: list[str]) -> dict[str, dict[str, object]]:
    if not municipality_ids or not _table_exists(conn, "signals"):
        return {}
    sql = f"""
        SELECT municipality_id, value
        FROM signals
        WHERE signal_type = 'crawl_diagnostics'
          AND municipality_id IN ({_placeholders(len(municipality_ids))})
        ORDER BY rowid DESC
    """
    rows = conn.execute(sql, tuple(municipality_ids)).fetchall()
    out: dict[str, dict[str, object]] = {}
    for row in rows:
        municipality_id = str(row["municipality_id"] or "")
        if not municipality_id or municipality_id in out:
            continue
        payload = _parse_json_dict(row["value"])
        if payload:
            out[municipality_id] = payload
    return out


def _derive_blocked_reason(diagnostics: dict[str, object]) -> str:
    status_code = _coerce_int(diagnostics.get("http_status"), default=-1)
    if status_code in {401, 403}:
        return "http_forbidden"
    if status_code == 429:
        return "rate_limited"
    if status_code == 503:
        return "service_unavailable_or_protected"
    if _coerce_int(diagnostics.get("detected_block_signal")) > 0:
        return "block_signal_detected"
    return "blocked_or_forbidden"


def is_directory_hit(path: str, status_code: int | None) -> bool:
    return status_code == 200 and "directory" in str(path or "").lower()


def assess_deep_path_recovery_trust(
    deep_path_results: list[dict[str, object]],
    extracted_paths: list[str],
) -> tuple[str, int]:
    hit_rows = [
        row
        for row in deep_path_results
        if _coerce_int(row.get("hit"), default=0) == 1
    ]
    if not hit_rows:
        return "none", 0

    extracted_any = bool([path for path in extracted_paths if str(path or "").strip()])
    aligned_any = any(_is_deep_path_row_aligned(row) for row in hit_rows)
    clean_non_numeric_any = any(_is_clean_non_numeric_path(str(row.get("path") or "")) for row in hit_rows)
    legacy_suspected = any(_is_deep_path_row_legacy_suspected(row) for row in hit_rows)

    trust = "low"
    if extracted_any and aligned_any and clean_non_numeric_any and not legacy_suspected:
        trust = "high"
    elif extracted_any and (aligned_any or clean_non_numeric_any):
        trust = "medium"
    elif extracted_any:
        trust = "medium"

    if legacy_suspected and trust == "high":
        trust = "medium"
    if legacy_suspected and not extracted_any:
        trust = "low"

    return trust, (1 if legacy_suspected else 0)


def _is_deep_path_row_aligned(row: dict[str, object]) -> bool:
    category = str(row.get("category") or "").strip().lower()
    hints = DEEP_PATH_TRUST_HINTS.get(category) or ()
    if not hints:
        return False
    path_text = str(row.get("path") or "").lower()
    body_text = str(row.get("text") or "").lower()[:4000]
    haystack = f"{path_text} {body_text}"
    return any(hint in haystack for hint in hints)


def _is_clean_non_numeric_path(path: str) -> bool:
    canonical = _canonical_probe_path(path)
    if not canonical or canonical == "/":
        return False
    if "?" in canonical or "=" in canonical:
        return False
    if re.search(r"/\d+(?:/|$)", canonical):
        return False
    return True


def _is_deep_path_row_legacy_suspected(row: dict[str, object]) -> bool:
    path_text = str(row.get("path") or "").lower()
    body_text = str(row.get("text") or "").lower()[:4000]
    if re.search(r"/\d{2,}(?:/|$)", path_text):
        return True
    if any(hint in path_text for hint in DEEP_PATH_LEGACY_HINTS):
        return True
    if any(hint in body_text for hint in DEEP_PATH_LEGACY_HINTS):
        return True
    return False


def fetch_with_playwright(url: str, timeout: int = 15000) -> str | None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    )
                )
                page = context.new_page()
                page.goto(url, timeout=timeout)
                return page.content()
            finally:
                browser.close()
    except Exception:
        return None


def run_directory_hit_fallback(
    conn,
    municipality_id: str,
    source_url: str,
    html_text: str,
) -> tuple[int, int, str]:
    """
    Returns:
      recovered_contact_count,
      recovered_role_winner_count,
      fallback_note
    """
    before_contacts = _count_contacts(conn, municipality_id)
    winners_view_exists = _view_exists(conn, "vw_best_role_per_town")
    before_winners = _count_role_winners(conn, municipality_id) if winners_view_exists else 0

    process_text_extractions(
        conn,
        municipality_id,
        source_url,
        html_text,
        page_type="staff_directory",
    )

    after_contacts = _count_contacts(conn, municipality_id)
    recovered_contacts = max(0, after_contacts - before_contacts)
    recovered_winners = 0
    fallback_note = ""
    if winners_view_exists:
        recovered_winners = max(0, _count_role_winners(conn, municipality_id) - before_winners)
    else:
        fallback_note = "directory_fallback_winners_unavailable=1"
    return recovered_contacts, recovered_winners, fallback_note


def _build_known_probe_urls(seed_url: str) -> list[dict[str, str]]:
    site_root = _site_root(seed_url)
    if not site_root:
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for path in KNOWN_PATHS:
        url = normalize_url(path, base_url=site_root)
        if not url or url in seen:
            continue
        seen.add(url)
        out.append({"path": path, "url": url})
    return out


def probe_deep_paths(
    base_url: str,
    categorized_paths: dict[str, list[str]],
    fetch_fn,
    probe_budget: int = 20,
    on_result=None,
) -> list[dict]:
    results: list[dict] = []
    entries = _build_deep_probe_entries(base_url=base_url, categorized_paths=categorized_paths)
    if not entries:
        return results

    block_streak = 0
    attempts_used = 0
    for entry in entries:
        if attempts_used >= max(0, probe_budget):
            break
        if block_streak >= BLOCK_STREAK_STOP_THRESHOLD:
            break

        category = str(entry.get("category") or "")
        path = str(entry.get("path") or "")
        url = str(entry.get("url") or "")
        attempts_used += 1
        resp = fetch_fn(url)
        status_code = resp.status_code if resp.status_code is not None else ""
        status_int = _coerce_int(status_code, default=-1)
        hit = 1 if status_int == 200 else 0
        if status_int in BLOCK_HTTP_STATUS_CODES:
            block_streak += 1
        else:
            block_streak = 0

        text_value = str(resp.text or "")
        results.append(
            {
                "category": category,
                "path": path,
                "url": url,
                "status": status_code,
                "hit": hit,
                "content_type": str(resp.content_type or resp.response_headers.get("content-type") or ""),
                "length": len(text_value),
                "text": text_value if hit else "",
            }
        )
        if on_result and on_result(results[-1]):
            break
    return results


def _build_deep_probe_entries(base_url: str, categorized_paths: dict[str, list[str]]) -> list[dict[str, str]]:
    root = str(base_url or "").rstrip("/")
    if not root:
        return []

    out: list[dict[str, str]] = []
    seen: set[str] = set()
    ordered_categories: list[str] = []
    seen_categories: set[str] = set()
    for category in DEEP_PATH_PRIORITY_ORDER:
        if category in categorized_paths and category not in seen_categories:
            ordered_categories.append(category)
            seen_categories.add(category)
    for category in categorized_paths:
        if category in seen_categories:
            continue
        ordered_categories.append(category)
        seen_categories.add(category)

    for category in ordered_categories:
        paths = categorized_paths.get(category) or []
        for raw_path in paths:
            path = str(raw_path or "").strip()
            if not path:
                continue
            canonical = _canonical_probe_path(path)
            if canonical in seen:
                continue
            url = normalize_url(path, base_url=root) or f"{root}{path}"
            if not url:
                continue
            seen.add(canonical)
            out.append(
                {
                    "category": str(category),
                    "path": path,
                    "url": url,
                }
            )
    return out


def _canonical_probe_path(path: str) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    normalized = text.lower()
    if normalized != "/" and normalized.endswith("/"):
        normalized = normalized.rstrip("/")
    return normalized


def probe_api_endpoints(base_url: str, fetch_fn) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    root = str(base_url or "").rstrip("/")
    if not root:
        return results

    for path in API_PROBE_PATHS:
        url = f"{root}{path}"
        resp = fetch_fn(url)
        text_value = str(resp.text or "")
        results.append(
            {
                "path": path,
                "status": resp.status_code if resp.status_code is not None else "",
                "content_type": str(resp.content_type or resp.response_headers.get("content-type") or ""),
                "length": len(text_value),
                "text": text_value[:500],
            }
        )
    return results


def classify_api_presence(results: list[dict[str, object]]) -> tuple[int, str]:
    api_hit = 0
    api_type = "none"

    for row in results:
        if _coerce_int(row.get("status"), default=-1) != 200:
            continue
        text_sample = str(row.get("text") or "")[:500].lower()
        if "swagger" in text_sample or "openapi" in text_sample:
            return 1, "swagger"
        if "api" in str(row.get("path") or "").lower():
            api_hit = 1
            api_type = "rest_root"
    return api_hit, api_type


def run_api_inventory(base_url: str, fetch_fn) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    root = str(base_url or "").rstrip("/")
    if not root:
        return results

    for path in API_INVENTORY_PATHS:
        url = f"{root}{path}"
        resp = fetch_fn(url)
        content_type = str(resp.content_type or resp.response_headers.get("content-type") or "").lower()
        sample = str(resp.text or "")[:500].lower()
        results.append(
            {
                "path": path,
                "status": resp.status_code if resp.status_code is not None else "",
                "content_type": content_type,
                "is_json": "json" in content_type,
                "has_swagger_markers": ("swagger" in sample or "openapi" in sample),
                "length": len(str(resp.text or "")),
            }
        )
    return results


def classify_api_inventory(results: list[dict[str, object]]) -> tuple[str, int]:
    endpoint_count = sum(1 for row in results if _coerce_int(row.get("status"), default=-1) == 200)

    for row in results:
        if (
            _coerce_int(row.get("status"), default=-1) == 200
            and bool(row.get("is_json"))
            and bool(row.get("has_swagger_markers"))
        ):
            return "swagger_json", endpoint_count

    for row in results:
        if _coerce_int(row.get("status"), default=-1) == 200 and bool(row.get("has_swagger_markers")):
            return "swagger_ui", endpoint_count

    for row in results:
        if _coerce_int(row.get("status"), default=-1) == 200 and bool(row.get("is_json")):
            return "rest_json", endpoint_count

    if endpoint_count > 0:
        return "html_only", endpoint_count
    return "none", 0


def fetch_swagger_json(base_url: str, inventory_paths: list[str], fetch_fn) -> dict | None:
    swagger_doc, _ = _fetch_swagger_json_with_path(base_url, inventory_paths, fetch_fn)
    return swagger_doc


def _fetch_swagger_json_with_path(
    base_url: str,
    inventory_paths: list[str],
    fetch_fn,
) -> tuple[dict | None, str]:
    normalized_inventory_paths = [
        str(path or "").strip()
        for path in inventory_paths
        if str(path or "").strip()
    ]
    candidates: list[str] = []
    for path in normalized_inventory_paths:
        lowered = path.lower()
        if lowered.endswith(".json") and path not in candidates:
            candidates.append(path)
    for suffix in SWAGGER_JSON_PATH_SUFFIXES:
        if suffix not in candidates:
            candidates.append(suffix)

    root = str(base_url or "").rstrip("/")
    if not root:
        return None, ""

    for path in candidates:
        url = normalize_url(path, base_url=root) or f"{root}{path}"
        if not url:
            continue
        resp = fetch_fn(url)
        if _coerce_int(resp.status_code, default=-1) != 200:
            continue
        text_value = str(resp.text or "")
        content_type = str(resp.content_type or resp.response_headers.get("content-type") or "").lower()
        if "json" not in content_type and not text_value.lstrip().startswith("{"):
            continue
        try:
            parsed = json.loads(text_value)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed, path
    return None, ""


def extract_get_endpoints_from_swagger(swagger_doc: dict) -> list[dict]:
    paths_block = swagger_doc.get("paths")
    if not isinstance(paths_block, dict):
        return []

    out: list[dict] = []
    for path, operations in paths_block.items():
        if not isinstance(operations, dict):
            continue
        get_operation = operations.get("get")
        if not isinstance(get_operation, dict):
            continue
        summary = str(get_operation.get("summary") or "").strip()
        description = str(get_operation.get("description") or "").strip()
        if len(description) > 240:
            description = f"{description[:240].rstrip()}..."
        operation_id = str(get_operation.get("operationId") or "").strip()
        raw_tags = get_operation.get("tags")
        tag_names = [str(tag).strip() for tag in raw_tags] if isinstance(raw_tags, list) else []
        tag_names = [tag for tag in tag_names if tag]
        out.append(
            {
                "path": str(path or "").strip(),
                "summary": summary,
                "description": description,
                "operation_id": operation_id,
                "tag_names": tag_names,
            }
        )
    return out


def score_swagger_endpoint(endpoint: dict) -> tuple[int, str]:
    path_value = str(endpoint.get("path") or "").lower()
    summary = str(endpoint.get("summary") or "").lower()
    operation_id = str(endpoint.get("operation_id") or "").lower()
    tags = " ".join(str(tag).lower() for tag in endpoint.get("tag_names") or [])
    haystack = " ".join([path_value, summary, operation_id, tags])

    score = 0
    if any(keyword in haystack for keyword in ("directory", "staff", "contact", "contacts")):
        score += 3
    if any(keyword in haystack for keyword in ("department", "departments")):
        score += 2
    if any(keyword in haystack for keyword in ("tax", "assessor", "clerk", "building", "planning", "finance")):
        score += 2
    if any(keyword in haystack for keyword in ("agenda", "meeting", "minutes", "publicrecords")):
        score += 1
    if any(keyword in haystack for keyword in ("auth", "login", "account", "token", "admin")):
        score -= 5
    if any(keyword in haystack for keyword in ("health", "ping", "telemetry")):
        score -= 3

    endpoint_class = "other"
    if any(keyword in haystack for keyword in ("directory", "staff", "contact", "contacts", "official", "clerk")):
        endpoint_class = "contact_like"
    elif any(keyword in haystack for keyword in ("department", "departments", "tax", "assessor", "building", "planning", "finance")):
        endpoint_class = "department_like"
    elif any(keyword in haystack for keyword in ("agenda", "meeting", "minutes")):
        endpoint_class = "meeting_like"
    elif any(keyword in haystack for keyword in ("publicrecords", "records")):
        endpoint_class = "records_like"
    return score, endpoint_class


def select_api_probe_endpoints(endpoints: list[dict], max_count: int = 3) -> list[dict]:
    class_priority = {
        "contact_like": 3,
        "department_like": 2,
        "meeting_like": 1,
        "records_like": 1,
        "other": 0,
    }
    filtered: list[dict] = []
    for endpoint in endpoints:
        score = _coerce_int(endpoint.get("score"), default=0)
        if score <= 0:
            continue
        path_value = str(endpoint.get("path") or "").strip()
        lowered_path = path_value.lower()
        if not path_value:
            continue
        if "{" in path_value or "}" in path_value:
            continue
        if any(keyword in lowered_path for keyword in API_ENDPOINT_EXCLUDE_KEYWORDS):
            continue
        if lowered_path.endswith("/id") or lowered_path.endswith("/ids"):
            continue
        filtered.append(endpoint)

    filtered.sort(
        key=lambda endpoint: (
            class_priority.get(str(endpoint.get("endpoint_class") or "other"), 0),
            _coerce_int(endpoint.get("score"), default=0),
            -len(str(endpoint.get("path") or "")),
        ),
        reverse=True,
    )
    return filtered[: max(0, max_count)]


def probe_swagger_get_endpoint(base_url: str, endpoint_path: str, fetch_fn) -> dict:
    path_value = str(endpoint_path or "").strip()
    if not path_value:
        return {
            "status": "",
            "content_type": "",
            "json_root_type": "other",
            "item_count_estimate": 0,
            "likely_structured_data": 0,
        }
    if "{" in path_value or "}" in path_value:
        return {
            "status": "",
            "content_type": "",
            "json_root_type": "other",
            "item_count_estimate": 0,
            "likely_structured_data": 0,
        }

    root = str(base_url or "").rstrip("/")
    target_url = normalize_url(path_value, base_url=root) or f"{root}{path_value}"
    resp = fetch_fn(target_url)
    content_type = str(resp.content_type or resp.response_headers.get("content-type") or "").lower()
    status = resp.status_code if resp.status_code is not None else ""
    out = {
        "status": status,
        "content_type": content_type,
        "json_root_type": "other",
        "item_count_estimate": 0,
        "likely_structured_data": 0,
    }
    if _coerce_int(status, default=-1) != 200:
        return out

    text_value = str(resp.text or "")
    if "json" not in content_type and not text_value.lstrip().startswith(("{", "[")):
        return out
    try:
        parsed = json.loads(text_value)
    except json.JSONDecodeError:
        return out

    if isinstance(parsed, list):
        out["json_root_type"] = "list"
        out["item_count_estimate"] = len(parsed)
        if parsed:
            first_item = parsed[0]
            if isinstance(first_item, dict) or isinstance(first_item, (str, int, float, bool)):
                out["likely_structured_data"] = 1
    elif isinstance(parsed, dict):
        out["json_root_type"] = "object"
        out["item_count_estimate"] = len(parsed.keys())
        if len(parsed.keys()) >= 2:
            out["likely_structured_data"] = 1
    return out


def _select_discovered_probe_urls(
    candidates: list[tuple[str, str]],
    municipality_domain: str,
    limit: int,
) -> list[str]:
    scored: list[tuple[float, str]] = []
    seen: set[str] = set()
    for raw_url, source_kind in candidates:
        candidate = normalize_url(raw_url)
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if not _is_internal_link(candidate, municipality_domain):
            continue
        if not _is_high_value_discovered_url(candidate):
            continue
        score = _score_discovered_url(candidate)
        if source_kind == "sitemap":
            score += 0.15
        scored.append((score, candidate))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [url for _, url in scored[: max(0, limit)]]


def _score_discovered_url(url: str) -> float:
    lowered = url.lower()
    score = 0.0
    for hint in DISCOVERED_HIGH_VALUE_HINTS:
        if hint in lowered:
            score += 1.0
    page_type = classify_page_type(url, "")
    if is_contact_oriented_page_type(page_type):
        score += 1.5
    return score


def _is_high_value_discovered_url(url: str) -> bool:
    lowered = url.lower()
    if lowered.endswith((".pdf", ".doc", ".docx", ".xls", ".xlsx", ".jpg", ".jpeg", ".png", ".gif", ".zip")):
        return False
    page_type = classify_page_type(url, "")
    if is_contact_oriented_page_type(page_type):
        return True
    return any(hint in lowered for hint in DISCOVERED_HIGH_VALUE_HINTS)


def _extract_urls_from_robots(robots_text: str, site_root: str) -> list[str]:
    out: list[str] = []
    for line in (robots_text or "").splitlines():
        clean = line.split("#", 1)[0].strip()
        if not clean or ":" not in clean:
            continue
        key, raw_value = clean.split(":", 1)
        directive = key.strip().lower()
        value = raw_value.strip()
        if not value:
            continue
        if directive in {"allow", "disallow"}:
            if not value.startswith("/"):
                continue
            if "*" in value or "$" in value:
                continue
            resolved = normalize_url(value, base_url=site_root)
            if resolved:
                out.append(resolved)
    return out


def _upsert_blocked_recovery_signal(
    conn,
    municipality_id: str,
    status_row: dict[str, object],
    source_url: str,
) -> None:
    payload = {field: status_row.get(field) for field in BLOCKED_RECOVERY_STATUS_FIELDS}
    signal_id = make_id("sig", municipality_id, "blocked_recovery_status")
    db.upsert_signal(
        conn,
        {
            "signal_id": signal_id,
            "municipality_id": municipality_id,
            "signal_type": "blocked_recovery_status",
            "value": json.dumps(payload, sort_keys=True),
            "confidence": 1.0,
            "source_url": source_url,
        },
    )


def _upsert_api_ingestion_inventory_signal(
    conn,
    municipality_id: str,
    source_url: str,
    payload: dict[str, object],
) -> None:
    signal_id = make_id("sig", municipality_id, "api_ingestion_inventory")
    db.upsert_signal(
        conn,
        {
            "signal_id": signal_id,
            "municipality_id": municipality_id,
            "signal_type": "api_ingestion_inventory",
            "value": json.dumps(payload, sort_keys=True),
            "confidence": 1.0,
            "source_url": source_url,
        },
    )


def _count_contacts(conn, municipality_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM contacts WHERE municipality_id = ?",
        (municipality_id,),
    ).fetchone()
    return int(row["cnt"] if row else 0)


def _count_role_winners(conn, municipality_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM vw_best_role_per_town WHERE municipality_id = ?",
        (municipality_id,),
    ).fetchone()
    return int(row["cnt"] if row else 0)


def _view_exists(conn, view_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'view' AND name = ? LIMIT 1",
        (view_name,),
    ).fetchone()
    return row is not None


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def _site_root(url: str) -> str:
    normalized = normalize_url(ensure_url_has_scheme(url or ""))
    if not normalized:
        return ""
    parsed = urlparse(normalized)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def _build_fallback_url(url: str) -> str | None:
    normalized = normalize_url(ensure_url_has_scheme(url or ""))
    if not normalized:
        return None
    parsed = urlparse(normalized)
    if not parsed.netloc:
        return None
    fallback_scheme = "http" if parsed.scheme == "https" else "https"
    fallback = normalize_url(f"{fallback_scheme}://{parsed.netloc}/")
    if not fallback or fallback == normalized:
        return None
    return fallback


def _update_block_tracking(
    result: FetchResult,
    block_streak: int,
    blocked_responses: int,
) -> tuple[int, int]:
    if _is_block_status(result):
        return block_streak + 1, blocked_responses + 1
    return 0, blocked_responses


def _is_block_status(result: FetchResult) -> bool:
    return result.status_code in BLOCK_HTTP_STATUS_CODES


def _status_label(result: FetchResult) -> str:
    if result.status_code is None:
        return str(result.error or "request_error")
    if result.error and result.error != "http_error":
        return f"{result.status_code}:{result.error}"
    return str(result.status_code)


def _status_is_200(value: object) -> bool:
    return _coerce_int(value, default=-1) == 200


def _is_internal_link(url: str, municipality_domain: str) -> bool:
    candidate_domain = (get_domain(url) or "").lower()
    target_domain = (municipality_domain or "").lower()
    if not candidate_domain or not target_domain:
        return False
    return candidate_domain == target_domain or candidate_domain.endswith(f".{target_domain}")


def _coerce_int(value: object, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip()
    if not text:
        return default
    try:
        return int(float(text))
    except ValueError:
        return default


def _parse_json_dict(value: object) -> dict[str, object]:
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _placeholders(size: int) -> str:
    return ",".join("?" for _ in range(size))
