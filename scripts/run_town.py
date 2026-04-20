from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
import re
from urllib.parse import urlparse

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover - fallback for minimal environments
    BeautifulSoup = None

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import db
from src.discover import (
    classify_page_type,
    extract_links_from_html,
    extract_links_from_sitemap_xml,
    is_contact_oriented_page_type,
    is_drillable_page_type,
    select_contact_child_links,
    select_high_value_links,
)
from src.http_client import FetchResult, candidate_sitemap_urls, create_session, fetch_url
from src.normalize import get_domain, make_id, normalize_url, normalize_whitespace
from src.parsers import (
    classify_service_link,
    extract_contacts,
    extract_locations,
    location_dedupe_key,
)
from src.vendors import detect_vendor

ALTERNATE_SEED_COLUMNS = ("jobs_url", "directory_url", "assessor_url", "tax_url")
SEED_TYPE_CONFIG: dict[str, dict[str, str]] = {
    "jobs_url": {
        "category": "jobs",
        "label": "Jobs",
    },
    "directory_url": {
        "category": "directory_contact",
        "label": "Directory/Contact",
    },
    "assessor_url": {
        "category": "property_cards",
        "label": "Assessor/Property",
    },
    "tax_url": {
        "category": "tax_payment",
        "label": "Tax Payment",
    },
}
CONTACT_SECOND_HOP_LINKS_PER_PAGE = 10
MAX_CONTACT_SECOND_HOP_PAGES = 40
MAX_CONTACT_DISCOVERY_DEPTH = 2
CRAWL_BLOCK_INDICATORS = (
    "cloudflare",
    "access denied",
    "forbidden",
    "attention required",
    "request blocked",
    "bot verification",
)
JS_SHELL_INDICATORS = (
    "javascript required",
    "enable javascript",
    "please enable javascript",
    "requires javascript",
)
JS_SHELL_TEXT_LENGTH_THRESHOLD = 1200
JS_SHELL_LINK_NEAR_ZERO_THRESHOLD = 1
DIRECTORY_CANDIDATE_PAGE_TYPES = {
    "directory_page",
    "directory_category_page",
    "contact_page",
    "department_page",
    "official_page",
}


def extract_title(html: str) -> str | None:
    match = re.search(r"(?is)<title[^>]*>(.*?)</title>", html or "")
    if BeautifulSoup is None:
        if match:
            return normalize_whitespace(match.group(1))
        return None
    try:
        soup = BeautifulSoup(html or "", "html.parser")
    except Exception:
        if match:
            return normalize_whitespace(match.group(1))
        return None
    if soup.title and soup.title.string:
        return normalize_whitespace(soup.title.string)
    if match:
        return normalize_whitespace(match.group(1))
    return None


def is_xml_content(content_type: str | None, url: str) -> bool:
    ctype = (content_type or "").lower()
    if "xml" in ctype:
        return True
    return url.lower().endswith(".xml")


def upsert_signal(
    conn,
    municipality_id: str,
    signal_type: str,
    value: str,
    confidence: float,
    source_url: str,
) -> None:
    normalized_value = normalize_signal_value(value)
    signal_id = make_id("sig", municipality_id, signal_type, normalized_value)
    db.upsert_signal(
        conn,
        {
            "signal_id": signal_id,
            "municipality_id": municipality_id,
            "signal_type": signal_type,
            "value": normalize_whitespace(value) or value,
            "confidence": round(confidence, 3),
            "source_url": source_url,
        },
    )


def upsert_crawl_error_signal(
    conn,
    municipality_id: str,
    source_url: str,
    result: FetchResult,
    confidence: float = 0.7,
) -> None:
    normalized_source = normalize_url(source_url) or source_url
    error_class = normalize_error_class(result.error)
    status_code = str(result.status_code) if result.status_code is not None else "none"
    signal_id = make_id(
        "sig",
        municipality_id,
        "crawl_error",
        normalized_source,
        error_class,
        status_code,
    )
    db.upsert_signal(
        conn,
        {
            "signal_id": signal_id,
            "municipality_id": municipality_id,
            "signal_type": "crawl_error",
            "value": json.dumps(
                {
                    "url": normalized_source,
                    "error": result.error,
                    "error_class": error_class,
                    "status": result.status_code,
                    "response_headers": result.response_headers or {},
                }
            ),
            "confidence": round(confidence, 3),
            "source_url": normalized_source,
        },
    )


def upsert_crawl_diagnostics_signal(
    conn,
    municipality_id: str,
    diagnostics: dict[str, object],
    confidence: float = 1.0,
) -> None:
    source_url = str(
        diagnostics.get("final_url_fetched")
        or diagnostics.get("seed_url_attempted")
        or ""
    )
    signal_id = make_id("sig", municipality_id, "crawl_diagnostics")
    db.upsert_signal(
        conn,
        {
            "signal_id": signal_id,
            "municipality_id": municipality_id,
            "signal_type": "crawl_diagnostics",
            "value": json.dumps(diagnostics, sort_keys=True),
            "confidence": round(confidence, 3),
            "source_url": source_url,
        },
    )


def detect_block_signal(
    page_title: str | None,
    response_text: str | None,
) -> int:
    blob = f"{page_title or ''}\n{response_text or ''}".lower()
    return 1 if any(token in blob for token in CRAWL_BLOCK_INDICATORS) else 0


def detect_js_shell_signal(
    status_code: int | None,
    page_title: str | None,
    response_text: str | None,
    response_text_length: int,
    extracted_link_count: int,
) -> int:
    if status_code != 200:
        return 0
    blob = f"{page_title or ''}\n{response_text or ''}".lower()
    if any(token in blob for token in JS_SHELL_INDICATORS):
        return 1
    if response_text_length <= JS_SHELL_TEXT_LENGTH_THRESHOLD:
        return 1
    if response_text_length > 0 and extracted_link_count <= JS_SHELL_LINK_NEAR_ZERO_THRESHOLD:
        return 1
    return 0


def count_directory_candidates(candidates: list[dict[str, str | float]]) -> int:
    count = 0
    for candidate in candidates:
        page_type = str(candidate.get("page_type") or "").strip().lower()
        if page_type in DIRECTORY_CANDIDATE_PAGE_TYPES:
            count += 1
    return count


def classify_crawl_diagnostic(
    diagnostics: dict[str, object],
) -> str:
    raw_status_code = diagnostics.get("http_status")
    status_code = None if raw_status_code in (None, "") else _coerce_int(raw_status_code)
    extracted_link_count = _coerce_int(diagnostics.get("extracted_link_count"))
    candidate_service_link_count = _coerce_int(diagnostics.get("candidate_service_link_count"))
    contact_rows_extracted = _coerce_int(diagnostics.get("contact_rows_extracted"))
    response_text_length = _coerce_int(diagnostics.get("response_text_length"))
    detected_block_signal = _coerce_int(diagnostics.get("detected_block_signal"))
    detected_js_shell_signal = _coerce_int(diagnostics.get("detected_js_shell_signal"))

    if status_code in {401, 403, 429, 503} or detected_block_signal == 1:
        return "blocked_or_forbidden"
    if status_code == 200 and (response_text_length <= JS_SHELL_TEXT_LENGTH_THRESHOLD or detected_js_shell_signal == 1):
        return "probable_js_shell"
    if status_code == 200 and extracted_link_count > 0 and candidate_service_link_count == 0 and contact_rows_extracted == 0:
        return "discovery_failure"
    if status_code == 200 and candidate_service_link_count > 0 and contact_rows_extracted == 0:
        return "low_extraction"
    if status_code is None and extracted_link_count == 0 and candidate_service_link_count == 0 and contact_rows_extracted == 0:
        return "discovery_failure"
    return "ok"


def process_text_extractions(
    conn,
    municipality_id: str,
    source_url: str,
    text: str,
    page_type: str | None = None,
) -> tuple[int, int]:
    contact_count = 0
    location_count = 0
    seen_locations: set[str] = set()
    extracted_locations = extract_locations(text, source_url)
    fallback_address = None
    fallback_hours = None
    if extracted_locations:
        fallback_address = extracted_locations[0].get("address")
        fallback_hours = extracted_locations[0].get("hours")

    merged_contacts: dict[tuple[str, ...], dict[str, str | float | None]] = {}
    for contact in extract_contacts(text, source_url, page_type=page_type):
        email = str(contact.get("email") or "").strip().lower()
        phone = str(contact.get("phone") or "").strip()
        if not email and not phone:
            continue

        name = str(contact.get("name") or "").strip()
        title = str(contact.get("title") or "").strip()
        department = str(contact.get("department") or "").strip() or (infer_department_from_url(source_url) or "")
        source_context = str(contact.get("source_context") or "").strip()
        address = contact.get("address") or fallback_address
        hours = contact.get("hours") or fallback_hours
        merged_row = {
            "name": name or None,
            "title": title or None,
            "department": department or None,
            "email": email or None,
            "email_type": contact.get("email_type") or "unknown",
            "phone": phone or None,
            "phone_ext": contact.get("phone_ext"),
            "address": address,
            "hours": hours,
            "source_context": source_context or None,
            "source_url": source_url,
            "confidence": float(contact.get("confidence") or 0.45),
        }
        merge_key = build_contact_merge_key(merged_row, source_url)
        prior = merged_contacts.get(merge_key)
        merged_contacts[merge_key] = merge_contact_rows(prior, merged_row) if prior else merged_row

    for merged in merged_contacts.values():
        effective_confidence = min(
            0.995,
            float(merged.get("confidence") or 0.45) + (contact_row_richness(merged) * 0.01),
        )
        contact_id = build_contact_id(municipality_id, merged, source_url)

        db.upsert_contact(
            conn,
            {
                "contact_id": contact_id,
                "municipality_id": municipality_id,
                "name": merged.get("name"),
                "title": merged.get("title"),
                "department": merged.get("department"),
                "email": merged.get("email"),
                "email_type": merged.get("email_type") or "unknown",
                "phone": merged.get("phone"),
                "phone_ext": merged.get("phone_ext"),
                "address": merged.get("address"),
                "hours": merged.get("hours"),
                "source_context": merged.get("source_context"),
                "source_url": merged.get("source_url") or source_url,
                "confidence": round(effective_confidence, 3),
            },
        )
        contact_count += 1

    for location in extracted_locations:
        dedupe_address, dedupe_hours = location_dedupe_key(location.get("address"), location.get("hours"))
        if dedupe_address:
            location_id = make_id("loc", municipality_id, dedupe_address)
        else:
            location_id = make_id("loc", municipality_id, "", dedupe_hours)
        if location_id in seen_locations:
            continue
        seen_locations.add(location_id)
        db.upsert_location(
            conn,
            {
                "location_id": location_id,
                "municipality_id": municipality_id,
                "address": location.get("address"),
                "hours": location.get("hours"),
                "source_url": source_url,
            },
        )
        location_count += 1

    return contact_count, location_count


def crawl_single_municipality(
    conn,
    municipality: dict,
    raw_dir: Path,
    max_candidate_pages: int = 25,
    timeout: int = 20,
) -> dict[str, int | str]:
    municipality_id = municipality["municipality_id"]
    website_url = municipality.get("website_url")
    municipality_domain = (municipality.get("domain") or get_domain(website_url) or "").lower()
    stats = {
        "municipality_id": municipality_id,
        "fetched_pages": 0,
        "service_links": 0,
        "contacts": 0,
        "locations": 0,
    }
    diagnostics: dict[str, object] = {
        "municipality_id": municipality_id,
        "seed_url_attempted": normalize_url(str(website_url or "")) or str(website_url or ""),
        "final_url_fetched": "",
        "fallback_used": 0,
        "http_status": None,
        "redirect_count": 0,
        "content_type": "",
        "page_title": "",
        "response_text_length": 0,
        "extracted_link_count": 0,
        "candidate_service_link_count": 0,
        "candidate_directory_link_count": 0,
        "contact_rows_extracted": 0,
        "detected_block_signal": 0,
        "detected_js_shell_signal": 0,
        "diagnostic_class": "ok",
    }

    if not website_url:
        print(f"Fallback triggered for {municipality_id}")
        upsert_signal(conn, municipality_id, "crawl_status", "missing_website_url", 1.0, "")
        diagnostics["fallback_used"] = 1
        diagnostics["diagnostic_class"] = "discovery_failure"
        upsert_crawl_diagnostics_signal(conn, municipality_id, diagnostics, confidence=1.0)
        stats["diagnostic_class"] = str(diagnostics["diagnostic_class"])
        db.commit(conn)
        return stats

    vendor_best: dict[str, tuple[float, str]] = {}
    discovered_links: list[dict[str, str]] = []
    seen_fetched_urls: set[str] = set()
    seen_service_ids: set[str] = set()
    processed_seed_urls: set[str] = set()
    external_seed_urls: set[str] = set()
    session = create_session()

    def update_diagnostics_from_seed_fetch(
        requested_url: str,
        final_url: str | None,
        result: FetchResult,
        response_text: str | None,
    ) -> None:
        requested = normalize_url(requested_url) or requested_url
        final = normalize_url(final_url or requested_url) or (final_url or requested_url)
        diagnostics["seed_url_attempted"] = requested
        diagnostics["final_url_fetched"] = final
        diagnostics["http_status"] = result.status_code
        diagnostics["redirect_count"] = int(result.redirect_count or 0)
        diagnostics["content_type"] = result.content_type or ""
        payload = response_text or ""
        diagnostics["response_text_length"] = len(payload)
        if payload and not is_xml_content(result.content_type, final):
            title = extract_title(payload) or ""
            diagnostics["page_title"] = title
            if detect_block_signal(title, payload):
                diagnostics["detected_block_signal"] = 1
            if detect_js_shell_signal(
                status_code=result.status_code,
                page_title=title,
                response_text=payload,
                response_text_length=len(payload),
                extracted_link_count=0,
            ):
                diagnostics["detected_js_shell_signal"] = 1

    def finalize_and_store_diagnostics(source_url: str) -> None:
        diagnostics["contact_rows_extracted"] = int(stats["contacts"])
        diagnostics["extracted_link_count"] = int(diagnostics.get("extracted_link_count") or 0)
        diagnostics["candidate_service_link_count"] = int(diagnostics.get("candidate_service_link_count") or 0)
        diagnostics["candidate_directory_link_count"] = int(diagnostics.get("candidate_directory_link_count") or 0)
        diagnostics["fallback_used"] = 1 if _coerce_int(diagnostics.get("fallback_used")) > 0 else 0
        diagnostics["final_url_fetched"] = str(
            diagnostics.get("final_url_fetched")
            or normalize_url(source_url)
            or source_url
            or ""
        )
        diagnostics["seed_url_attempted"] = str(
            diagnostics.get("seed_url_attempted")
            or normalize_url(str(website_url or ""))
            or str(website_url or "")
        )
        diagnostics["detected_js_shell_signal"] = max(
            _coerce_int(diagnostics.get("detected_js_shell_signal")),
            detect_js_shell_signal(
                status_code=_coerce_int(diagnostics.get("http_status")) or None,
                page_title=str(diagnostics.get("page_title") or ""),
                response_text="",
                response_text_length=_coerce_int(diagnostics.get("response_text_length")),
                extracted_link_count=_coerce_int(diagnostics.get("extracted_link_count")),
            ),
        )
        diagnostics["diagnostic_class"] = classify_crawl_diagnostic(diagnostics)
        upsert_crawl_diagnostics_signal(conn, municipality_id, diagnostics, confidence=1.0)
        stats["diagnostic_class"] = str(diagnostics["diagnostic_class"])

    def register_vendor_signal(vendor: str, confidence: float, url: str) -> None:
        prior = vendor_best.get(vendor)
        if prior is None or confidence > prior[0]:
            vendor_best[vendor] = (confidence, url)

    def fetch_and_record(
        url: str,
        page_type: str,
        discovered_from: str,
        referer: str | None = None,
    ) -> tuple[bool, str | None, str | None, str | None, FetchResult]:
        result = fetch_url(url, session=session, timeout=timeout, referer=referer)
        final_url = result.final_url or url

        if not result.ok:
            upsert_crawl_error_signal(
                conn,
                municipality_id,
                final_url,
                result,
                confidence=0.7,
            )
            return False, final_url, None, result.content_type, result

        if final_url in seen_fetched_urls:
            return True, final_url, result.text, result.content_type, result
        seen_fetched_urls.add(final_url)

        title = extract_title(result.text or "") if not is_xml_content(result.content_type, final_url) else None
        page_id = make_id("page", municipality_id, final_url)
        db.upsert_page(
            conn,
            {
                "page_id": page_id,
                "municipality_id": municipality_id,
                "url": final_url,
                "page_type": page_type,
                "title": title,
                "discovered_from": discovered_from,
            },
        )
        stats["fetched_pages"] += 1

        if result.text:
            if is_xml_content(result.content_type, final_url):
                links = extract_links_from_sitemap_xml(result.text)
            else:
                links = extract_links_from_html(result.text, final_url)
            for link in links:
                link["source_url"] = final_url
                discovered_links.append(link)

            vendor, vendor_conf = detect_vendor(final_url, result.text)
            if vendor:
                register_vendor_signal(vendor, vendor_conf, final_url)

        return True, final_url, result.text, result.content_type, result

    homepage_failed = False
    alternate_seed_attempted = False
    home_ok, home_url, home_text, _, home_result = fetch_and_record(website_url, "homepage", "seed", referer=None)
    update_diagnostics_from_seed_fetch(website_url, home_url, home_result, home_text)
    entry_url = normalize_url(home_url or website_url) or (home_url or website_url)
    primary_seed_url = home_url
    if entry_url:
        processed_seed_urls.add(entry_url)

    if not home_ok or not home_url:
        homepage_failed = True
        diagnostics["fallback_used"] = 1
        upsert_signal(conn, municipality_id, "crawl_status", "homepage_fetch_failed", 1.0, website_url)
        blocked_value = classify_blocked_homepage(home_result)
        if blocked_value:
            upsert_signal(conn, municipality_id, "blocked_homepage", blocked_value, 0.98, website_url)

        alternate_urls = get_alternate_seed_entries(municipality, website_url, municipality_domain)
        alternate_successes: list[str] = []
        external_seed_recovered = False
        if alternate_urls:
            alternate_seed_attempted = True
            upsert_signal(conn, municipality_id, "alternate_seed_attempted", "true", 1.0, website_url)
            print(f"Alternate seed fallback triggered for {municipality_id}")
            raw_dir.mkdir(parents=True, exist_ok=True)
            for seed in alternate_urls:
                seed_url = str(seed["url"])
                seed_key = str(seed["seed_key"])
                seed_kind = str(seed["seed_kind"])
                seed_category = str(seed.get("seed_category") or "")
                seed_label = str(seed.get("seed_label") or seed_key)
                normalized_seed = normalize_url(seed_url) or seed_url
                if normalized_seed in processed_seed_urls:
                    continue

                processed_seed_urls.add(normalized_seed)
                if seed_kind == "external":
                    external_seed_urls.add(normalized_seed)
                upsert_signal(conn, municipality_id, "seed_url_used", seed_key, 1.0, seed_url)
                upsert_signal(conn, municipality_id, "seed_url_mode", seed_kind, 0.95, seed_url)

                if seed_kind == "external":
                    category, class_conf = classify_service_link(seed_url, f"{seed_label} {seed_key.replace('_', ' ')}")
                    if not category:
                        category = seed_category or None
                        class_conf = 0.7 if category else 0.0

                    vendor, vendor_conf = detect_vendor(seed_url, None)
                    if category:
                        service_id = make_id("svc", municipality_id, category.strip().lower(), seed_url)
                        if service_id not in seen_service_ids:
                            seen_service_ids.add(service_id)
                            db.upsert_service_link(
                                conn,
                                {
                                    "service_id": service_id,
                                    "municipality_id": municipality_id,
                                    "category": category,
                                    "label": seed_label,
                                    "url": seed_url,
                                    "domain": get_domain(seed_url),
                                    "vendor": vendor,
                                    "service_page_type": "external_portal",
                                    "confidence": round(min(0.95, max(0.55, class_conf)), 3),
                                    "source_url": seed_url,
                                },
                            )
                            stats["service_links"] += 1
                            external_seed_recovered = True
                    if vendor:
                        register_vendor_signal(vendor, max(vendor_conf, 0.8), seed_url)
                        external_seed_recovered = True
                        if category:
                            upsert_signal(
                                conn,
                                municipality_id,
                                f"{category}_vendor",
                                vendor,
                                max(vendor_conf, 0.8),
                                seed_url,
                            )
                    continue

                internal_seed_page_type = classify_page_type(seed_url, seed_label)
                ok, final_seed_url, seed_text, _, seed_result = fetch_and_record(
                    seed_url,
                    internal_seed_page_type,
                    f"seed_fallback:{seed_key}",
                    referer=None,
                )
                if not ok or not final_seed_url:
                    continue
                update_diagnostics_from_seed_fetch(seed_url, final_seed_url, seed_result, seed_text)
                alternate_successes.append(final_seed_url)
                processed_seed_urls.add(normalize_url(final_seed_url) or final_seed_url)
                if seed_text:
                    (raw_dir / f"{municipality_id}_{make_id('raw', final_seed_url, length=10)}.txt").write_text(
                        seed_text,
                        encoding="utf-8",
                        errors="ignore",
                    )
                    c_count, l_count = process_text_extractions(
                        conn,
                        municipality_id,
                        final_seed_url,
                        seed_text,
                        page_type=internal_seed_page_type,
                    )
                    stats["contacts"] += c_count
                    stats["locations"] += l_count

        if alternate_successes:
            primary_seed_url = alternate_successes[0]
            entry_url = normalize_url(primary_seed_url) or primary_seed_url
            upsert_signal(conn, municipality_id, "alternate_seed_recovered", "true", 0.95, primary_seed_url)
        elif external_seed_recovered:
            upsert_signal(conn, municipality_id, "alternate_seed_recovered", "true", 0.95, website_url)
        else:
            if alternate_seed_attempted:
                upsert_signal(conn, municipality_id, "alternate_seed_recovered", "false", 0.95, website_url)
            print(f"Fallback triggered for {municipality_id}")
            finalize_and_store_diagnostics(entry_url or website_url)
            db.commit(conn)
            return stats
    elif home_text:
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / f"{municipality_id}_homepage.html").write_text(home_text, encoding="utf-8", errors="ignore")
        c_count, l_count = process_text_extractions(
            conn,
            municipality_id,
            home_url,
            home_text,
            page_type="homepage",
        )
        stats["contacts"] += c_count
        stats["locations"] += l_count

    if home_ok and home_url:
        for sitemap_url in candidate_sitemap_urls(home_url):
            ok, final_sitemap_url, sitemap_text, _, _ = fetch_and_record(
                sitemap_url,
                "sitemap",
                home_url,
                referer=home_url,
            )
            if ok and final_sitemap_url and sitemap_text:
                (raw_dir / f"{municipality_id}_{make_id('raw', final_sitemap_url, length=10)}.txt").write_text(
                    sitemap_text, encoding="utf-8", errors="ignore"
                )

    high_value_links: list[dict[str, str | float]] = []
    fallback_triggered = False
    fallback_logged = False

    internal_discovered_links = [
        link
        for link in discovered_links
        if is_internal_link(str(link.get("url") or ""), municipality_domain)
    ]
    external_discovered_links = [
        link
        for link in discovered_links
        if not is_internal_link(str(link.get("url") or ""), municipality_domain)
    ]
    seed_internal_links = [
        link
        for link in internal_discovered_links
        if str(link.get("source_url") or "") == (primary_seed_url or "")
    ]

    target_min_candidates = min(max_candidate_pages, 10)
    if max_candidate_pages >= 5:
        target_min_candidates = max(5, target_min_candidates)

    knowledge_source_links = seed_internal_links or internal_discovered_links
    knowledge_links = select_high_value_links(
        knowledge_source_links,
        min_score=1.35,
        max_links=max(max_candidate_pages * 3, 45),
        broad_mode=True,
    )
    service_links = select_high_value_links(
        [*internal_discovered_links, *external_discovered_links],
        min_score=2.3,
        max_links=max(max_candidate_pages * 2, 30),
        broad_mode=False,
    )
    diagnostics["extracted_link_count"] = len(discovered_links)
    diagnostics["candidate_service_link_count"] = len(service_links)
    diagnostics["candidate_directory_link_count"] = count_directory_candidates(knowledge_links)
    high_value_links = compose_candidate_links(
        knowledge_links=knowledge_links,
        service_links=service_links,
        limit=max_candidate_pages,
    )

    if len(high_value_links) < 5 and max_candidate_pages >= 5:
        fallback_triggered = True
        relaxed_links = select_high_value_links(
            internal_discovered_links,
            min_score=0.8,
            max_links=max(max_candidate_pages, 40),
            broad_mode=True,
        )
        high_value_links = merge_candidate_links(high_value_links, relaxed_links, max_candidate_pages)

    if len(high_value_links) < target_min_candidates:
        fallback_triggered = True
        seed_links = seed_internal_links or internal_discovered_links
        unscored_links = build_unscored_internal_candidates(seed_links, limit=max(max_candidate_pages, 30))
        high_value_links = merge_candidate_links(high_value_links, unscored_links, max_candidate_pages)

    if fallback_triggered:
        print(f"Fallback triggered for {municipality_id}")
        fallback_logged = True

    second_hop_seen_urls: set[str] = set()
    second_hop_fetches = 0
    processed_candidate_urls: set[str] = set()

    def process_discovered_page(
        target_url: str,
        label: str,
        source_url: str,
        page_type: str,
        link_score: float,
        referer: str | None = None,
    ) -> tuple[bool, str, str | None]:
        category, class_conf = classify_service_link(target_url, label)
        ok, final_target_url, page_text, _, _ = fetch_and_record(
            target_url,
            page_type,
            source_url,
            referer=referer,
        )
        active_url = normalize_url(final_target_url or target_url) or (final_target_url or target_url)

        active_category, active_class_conf = classify_service_link(active_url, label)
        if active_category:
            category = active_category
            class_conf = max(class_conf, active_class_conf)

        vendor, vendor_conf = detect_vendor(active_url, page_text if ok else None)
        if category:
            service_conf = min(0.99, 0.35 + (link_score / 10.0) + (class_conf * 0.4))
            service_id = make_id("svc", municipality_id, category.strip().lower(), active_url)
            if service_id not in seen_service_ids:
                seen_service_ids.add(service_id)
                db.upsert_service_link(
                    conn,
                    {
                        "service_id": service_id,
                        "municipality_id": municipality_id,
                        "category": category,
                        "label": label or active_url,
                        "url": active_url,
                        "domain": get_domain(active_url),
                        "vendor": vendor,
                        "service_page_type": classify_service_page_type(active_url, municipality_domain),
                        "confidence": round(service_conf, 3),
                        "source_url": source_url,
                    },
                )
                stats["service_links"] += 1

        if vendor:
            register_vendor_signal(vendor, vendor_conf, active_url)

        if ok and page_text:
            c_count, l_count = process_text_extractions(
                conn,
                municipality_id,
                active_url,
                page_text,
                page_type=page_type,
            )
            stats["contacts"] += c_count
            stats["locations"] += l_count
        return ok, active_url, page_text

    def crawl_contact_children(
        parent_url: str,
        parent_text: str,
        parent_page_type: str,
        depth: int,
    ) -> None:
        nonlocal second_hop_fetches
        if depth > MAX_CONTACT_DISCOVERY_DEPTH:
            return
        if second_hop_fetches >= MAX_CONTACT_SECOND_HOP_PAGES:
            return
        if not is_drillable_page_type(parent_page_type):
            return

        child_links = extract_links_from_html(parent_text, parent_url)
        child_candidates = select_contact_child_links(
            child_links,
            municipality_domain=municipality_domain,
            parent_page_type=parent_page_type,
            max_links=CONTACT_SECOND_HOP_LINKS_PER_PAGE,
        )
        for child in child_candidates:
            if second_hop_fetches >= MAX_CONTACT_SECOND_HOP_PAGES:
                break
            child_url = str(child.get("url") or "")
            if not child_url:
                continue
            child_norm = normalize_url(child_url) or child_url
            if child_norm in second_hop_seen_urls:
                continue
            if child_norm in processed_candidate_urls:
                continue
            if child_norm in processed_seed_urls or child_norm in external_seed_urls:
                continue
            second_hop_seen_urls.add(child_norm)
            processed_candidate_urls.add(child_norm)
            second_hop_fetches += 1

            child_label = str(child.get("label") or "")
            child_page_type = str(
                child.get("page_type")
                or classify_page_type(child_url, child_label, parent_page_type=parent_page_type)
            )
            child_score = float(child.get("score") or 0.0)
            child_ok, child_active_url, child_text = process_discovered_page(
                target_url=child_url,
                label=child_label,
                source_url=parent_url,
                page_type=child_page_type,
                link_score=child_score,
                referer=parent_url,
            )
            if child_ok and child_text and depth < MAX_CONTACT_DISCOVERY_DEPTH:
                crawl_contact_children(
                    parent_url=child_active_url,
                    parent_text=child_text,
                    parent_page_type=child_page_type,
                    depth=depth + 1,
                )

    for candidate in high_value_links:
        target_url = str(candidate["url"])
        target_norm = normalize_url(target_url) or target_url
        if target_norm in processed_seed_urls or target_norm in external_seed_urls:
            continue
        if target_norm in processed_candidate_urls:
            continue
        processed_candidate_urls.add(target_norm)

        source_url = str(candidate.get("source_url") or primary_seed_url or website_url)
        label = str(candidate.get("label") or "")
        link_score = float(candidate.get("score") or 0.0)
        candidate_page_type = str(candidate.get("page_type") or classify_page_type(target_url, label))
        referer = str(primary_seed_url or source_url) if is_internal_link(target_url, municipality_domain) else None
        ok, active_url, page_text = process_discovered_page(
            target_url=target_url,
            label=label,
            source_url=source_url,
            page_type=candidate_page_type,
            link_score=link_score,
            referer=referer,
        )

        if ok and page_text and is_contact_oriented_page_type(candidate_page_type):
            crawl_contact_children(
                parent_url=active_url,
                parent_text=page_text,
                parent_page_type=candidate_page_type,
                depth=1,
            )

    for vendor, (confidence, signal_url) in sorted(vendor_best.items()):
        upsert_signal(conn, municipality_id, "vendor_detected", vendor, confidence, signal_url)

    if homepage_failed and alternate_seed_attempted:
        recovered = any(
            stats[key] > 0 for key in ("fetched_pages", "contacts", "service_links", "locations")
        )
        upsert_signal(conn, municipality_id, "alternate_seed_recovered", str(recovered).lower(), 0.95, entry_url)

    upsert_signal(conn, municipality_id, "crawl_status", "completed", 1.0, entry_url)
    upsert_signal(conn, municipality_id, "fetched_pages_count", str(stats["fetched_pages"]), 1.0, entry_url)
    upsert_signal(conn, municipality_id, "high_value_links_count", str(len(high_value_links)), 0.95, entry_url)
    finalize_and_store_diagnostics(entry_url)
    if (stats["fetched_pages"] == 0 or stats["contacts"] == 0 or stats["service_links"] == 0) and not fallback_logged:
        print(f"Fallback triggered for {municipality_id}")
    db.commit(conn)
    return stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run crawler pipeline for one municipality.")
    parser.add_argument("municipality_id", help="e.g. ct_chester")
    parser.add_argument("--max-candidate-pages", type=int, default=25)
    parser.add_argument("--db", default=str(ROOT / "database" / "master.sqlite"))
    parser.add_argument("--qa", action="store_true", help="Print municipality row counts by table after run.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    conn = db.get_connection(args.db)
    try:
        municipality = db.get_municipality(conn, args.municipality_id)
        if not municipality:
            raise SystemExit(f"Municipality not found: {args.municipality_id}")

        stats = crawl_single_municipality(
            conn=conn,
            municipality=municipality,
            raw_dir=ROOT / "data" / "raw",
            max_candidate_pages=args.max_candidate_pages,
        )
    finally:
        conn.close()

    print(f"Crawl complete for {args.municipality_id}")
    print(json.dumps(stats, indent=2))
    if args.qa:
        conn = db.get_connection(args.db)
        try:
            counts = db.get_municipality_table_counts(conn, args.municipality_id)
        finally:
            conn.close()
        print("QA row counts:")
        print(json.dumps(counts, indent=2))


def get_alternate_seed_entries(
    municipality: dict,
    homepage_url: str | None,
    municipality_domain: str,
) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    home_norm = normalize_url(homepage_url or "") or (homepage_url or "")
    for column in ALTERNATE_SEED_COLUMNS:
        raw = str(municipality.get(column) or "").strip()
        normalized = normalize_url(raw) or raw
        if not normalized:
            continue
        if home_norm and normalized == home_norm:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        seed_kind = "internal" if is_internal_link(normalized, municipality_domain) else "external"
        cfg = SEED_TYPE_CONFIG.get(column, {})
        entries.append(
            {
                "seed_key": column,
                "url": normalized,
                "seed_kind": seed_kind,
                "seed_category": str(cfg.get("category") or ""),
                "seed_label": str(cfg.get("label") or column),
            }
        )
    return entries


def classify_blocked_homepage(result: FetchResult | None) -> str | None:
    if result is None or result.status_code != 403:
        return None
    headers = {str(k).lower(): str(v).lower() for k, v in (result.response_headers or {}).items()}
    server = headers.get("server", "")
    has_cloudflare_header = "cf-ray" in headers or any("cloudflare" in value for value in headers.values())
    if "cloudflare" in server or has_cloudflare_header:
        return "cloudflare_403"

    bot_markers = ("akamai", "incapsula", "imperva", "sucuri", "bot", "challenge")
    combined = " ".join([server, *headers.values()])
    if any(marker in combined for marker in bot_markers):
        return "bot_protection_403"
    return None


def normalize_signal_value(value: str) -> str:
    cleaned = normalize_whitespace(value) or value
    return cleaned.strip().lower()


def normalize_error_class(error: str | None) -> str:
    raw = (error or "unknown_error").strip().lower()
    if not raw:
        return "unknown_error"
    return raw.split(":", 1)[0]


def infer_department_from_url(source_url: str) -> str | None:
    path = urlparse(source_url or "").path.lower()
    hints = (
        ("building-department", "Building Department"),
        ("tax-collector", "Tax Collector"),
        ("assessor", "Assessor"),
        ("town-clerk", "Town Clerk"),
        ("planning", "Planning"),
        ("zoning", "Zoning"),
        ("public-works", "Public Works Department"),
        ("human-resources", "Human Resources"),
    )
    for token, label in hints:
        if token in path:
            return label
    return None


def build_contact_merge_key(
    row: dict[str, str | float | None],
    default_source_url: str,
) -> tuple[str, ...]:
    email = normalize_contact_token(row.get("email"))
    if email:
        return ("email", email)

    name_key = normalize_contact_token(row.get("name"))
    title_key = normalize_contact_token(row.get("title") or row.get("department"))
    source_key = normalize_contact_token(row.get("source_url") or default_source_url)
    return ("row", name_key, title_key, source_key)


def build_contact_id(
    municipality_id: str,
    row: dict[str, str | float | None],
    default_source_url: str,
) -> str:
    email = normalize_contact_token(row.get("email"))
    if email:
        return make_id("ctc", municipality_id, "email", email)

    name_key = normalize_contact_token(row.get("name"))
    title_key = normalize_contact_token(row.get("title") or row.get("department"))
    source_key = normalize_contact_token(row.get("source_url") or default_source_url)
    return make_id("ctc", municipality_id, "row", name_key, title_key, source_key)


def contact_row_richness(row: dict[str, str | float | None]) -> int:
    score = 0
    if normalize_contact_token(row.get("name")):
        score += 4
    if normalize_contact_token(row.get("title")):
        score += 4
    if normalize_contact_token(row.get("department")):
        score += 2
    if normalize_contact_token(row.get("email")):
        score += 5
    if normalize_contact_token(row.get("phone")):
        score += 3
    if normalize_contact_token(row.get("phone_ext")):
        score += 1
    if normalize_contact_token(row.get("address")):
        score += 1
    if normalize_contact_token(row.get("hours")):
        score += 1
    return score


def merge_contact_rows(
    left: dict[str, str | float | None],
    right: dict[str, str | float | None],
) -> dict[str, str | float | None]:
    left_score = contact_row_richness(left)
    right_score = contact_row_richness(right)
    if right_score > left_score:
        primary, secondary = right, left
    elif left_score > right_score:
        primary, secondary = left, right
    else:
        primary, secondary = (
            (right, left)
            if float(right.get("confidence") or 0.0) >= float(left.get("confidence") or 0.0)
            else (left, right)
        )

    merged = dict(primary)
    for field in ("name", "title", "department", "email", "phone", "phone_ext", "address", "hours", "source_context", "source_url"):
        if not normalize_contact_token(merged.get(field)):
            merged[field] = secondary.get(field)
    if (merged.get("email_type") in {None, "", "unknown"}) and secondary.get("email_type"):
        merged["email_type"] = secondary.get("email_type")
    merged["confidence"] = max(float(left.get("confidence") or 0.0), float(right.get("confidence") or 0.0))
    return merged


def normalize_contact_token(value: str | float | None) -> str:
    lowered = str(value or "").strip().lower()
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def _coerce_int(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip()
    if not text:
        return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def classify_service_page_type(url: str, municipality_domain: str) -> str:
    domain = (get_domain(url) or "").lower()
    muni = (municipality_domain or "").lower()
    if domain and muni and (domain == muni or domain.endswith(f".{muni}")):
        return "internal_page"
    return "external_portal"


def is_internal_link(url: str, municipality_domain: str) -> bool:
    link_domain = (get_domain(url) or "").lower()
    muni = (municipality_domain or "").lower()
    if not link_domain or not muni:
        return False
    return link_domain == muni or link_domain.endswith(f".{muni}")


def merge_candidate_links(
    primary: list[dict[str, str | float]],
    secondary: list[dict[str, str | float]],
    limit: int,
) -> list[dict[str, str | float]]:
    merged: list[dict[str, str | float]] = []
    seen_urls: set[str] = set()
    for candidate in [*primary, *secondary]:
        url = str(candidate.get("url") or "")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        merged.append(candidate)
        if len(merged) >= limit:
            break
    return merged


def compose_candidate_links(
    knowledge_links: list[dict[str, str | float]],
    service_links: list[dict[str, str | float]],
    limit: int,
) -> list[dict[str, str | float]]:
    if limit <= 0:
        return []

    service_budget = min(max(3, limit // 4), limit)
    knowledge_budget = max(0, limit - service_budget)

    selected: list[dict[str, str | float]] = []
    selected = merge_candidate_links(selected, knowledge_links[:knowledge_budget], limit)
    selected = merge_candidate_links(selected, service_links[:service_budget], limit)
    selected = merge_candidate_links(selected, knowledge_links[knowledge_budget:], limit)
    selected = merge_candidate_links(selected, service_links[service_budget:], limit)
    return selected


def build_unscored_internal_candidates(
    links: list[dict[str, str]],
    limit: int,
) -> list[dict[str, str | float]]:
    out: list[dict[str, str | float]] = []
    seen_urls: set[str] = set()
    blocked_ext = (".jpg", ".jpeg", ".png", ".gif", ".svg", ".css", ".js", ".ico", ".zip")
    for link in links:
        url = str(link.get("url") or "")
        if not url or url in seen_urls:
            continue
        if url.lower().endswith(blocked_ext):
            continue
        seen_urls.add(url)
        out.append(
            {
                "url": url,
                "label": str(link.get("label") or ""),
                "score": 0.5,
                "reasons": "internal_fallback",
                "source_url": str(link.get("source_url") or ""),
            }
        )
        if len(out) >= limit:
            break
    return out


if __name__ == "__main__":
    main()
