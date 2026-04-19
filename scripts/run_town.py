from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import db
from src.discover import extract_links_from_html, extract_links_from_sitemap_xml, select_high_value_links
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


def extract_title(html: str) -> str | None:
    soup = BeautifulSoup(html or "", "html.parser")
    if soup.title and soup.title.string:
        return normalize_whitespace(soup.title.string)
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


def process_text_extractions(conn, municipality_id: str, source_url: str, text: str) -> tuple[int, int]:
    contact_count = 0
    location_count = 0
    seen_contacts: set[str] = set()
    seen_locations: set[str] = set()

    for contact in extract_contacts(text, source_url):
        email = str(contact.get("email") or "").strip().lower()
        phone = str(contact.get("phone") or "").strip()
        if not email and not phone:
            continue

        if email:
            contact_id = make_id("ctc", municipality_id, email, source_url)
        else:
            contact_id = make_id(
                "ctc",
                municipality_id,
                phone,
                (contact.get("department") or "").strip().lower(),
                contact.get("source_context") or "",
                source_url,
            )
        if contact_id in seen_contacts:
            continue
        seen_contacts.add(contact_id)

        db.upsert_contact(
            conn,
            {
                "contact_id": contact_id,
                "municipality_id": municipality_id,
                "name": contact.get("name"),
                "title": contact.get("title"),
                "department": contact.get("department") or infer_department_from_url(source_url),
                "email": email or None,
                "email_type": contact.get("email_type") or "unknown",
                "phone": phone or None,
                "phone_ext": contact.get("phone_ext"),
                "source_context": contact.get("source_context"),
                "source_url": source_url,
                "confidence": contact.get("confidence") or 0.45,
            },
        )
        contact_count += 1

    for location in extract_locations(text, source_url):
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
    if not website_url:
        print(f"Fallback triggered for {municipality_id}")
        upsert_signal(conn, municipality_id, "crawl_status", "missing_website_url", 1.0, "")
        db.commit(conn)
        return {"municipality_id": municipality_id, "fetched_pages": 0, "service_links": 0, "contacts": 0, "locations": 0}

    stats = {
        "municipality_id": municipality_id,
        "fetched_pages": 0,
        "service_links": 0,
        "contacts": 0,
        "locations": 0,
    }
    vendor_best: dict[str, tuple[float, str]] = {}
    discovered_links: list[dict[str, str]] = []
    seen_fetched_urls: set[str] = set()
    seen_service_ids: set[str] = set()
    processed_seed_urls: set[str] = set()
    external_seed_urls: set[str] = set()
    session = create_session()

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
            upsert_signal(
                conn,
                municipality_id,
                "crawl_error",
                json.dumps(
                    {
                        "url": final_url,
                        "error": result.error,
                        "status": result.status_code,
                        "response_headers": result.response_headers or {},
                    }
                ),
                0.7,
                final_url,
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
    entry_url = normalize_url(home_url or website_url) or (home_url or website_url)
    primary_seed_url = home_url
    if entry_url:
        processed_seed_urls.add(entry_url)

    if not home_ok or not home_url:
        homepage_failed = True
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

                ok, final_seed_url, seed_text, _, _ = fetch_and_record(
                    seed_url,
                    "alternate_seed",
                    f"seed_fallback:{seed_key}",
                    referer=None,
                )
                if not ok or not final_seed_url:
                    continue
                alternate_successes.append(final_seed_url)
                processed_seed_urls.add(normalize_url(final_seed_url) or final_seed_url)
                if seed_text:
                    (raw_dir / f"{municipality_id}_{make_id('raw', final_seed_url, length=10)}.txt").write_text(
                        seed_text,
                        encoding="utf-8",
                        errors="ignore",
                    )
                    c_count, l_count = process_text_extractions(conn, municipality_id, final_seed_url, seed_text)
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
            db.commit(conn)
            return stats
    elif home_text:
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / f"{municipality_id}_homepage.html").write_text(home_text, encoding="utf-8", errors="ignore")
        c_count, l_count = process_text_extractions(conn, municipality_id, home_url, home_text)
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

    high_value_links = select_high_value_links(discovered_links, min_score=2.5, max_links=max_candidate_pages)
    fallback_triggered = False
    fallback_logged = False

    internal_discovered_links = [
        link
        for link in discovered_links
        if is_internal_link(str(link.get("url") or ""), municipality_domain)
    ]
    seed_internal_links = [
        link
        for link in internal_discovered_links
        if str(link.get("source_url") or "") == (primary_seed_url or "")
    ]

    target_min_candidates = min(max_candidate_pages, 10)
    if max_candidate_pages >= 5:
        target_min_candidates = max(5, target_min_candidates)

    if not high_value_links:
        fallback_triggered = True
        seed_links = seed_internal_links or internal_discovered_links
        high_value_links = select_high_value_links(
            seed_links,
            min_score=1.2,
            max_links=max(max_candidate_pages, 20),
            broad_mode=True,
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

    for candidate in high_value_links:
        target_url = str(candidate["url"])
        target_norm = normalize_url(target_url) or target_url
        if target_norm in processed_seed_urls or target_norm in external_seed_urls:
            continue

        source_url = str(candidate.get("source_url") or primary_seed_url or website_url)
        label = str(candidate.get("label") or "")
        link_score = float(candidate.get("score") or 0.0)

        category, class_conf = classify_service_link(target_url, label)
        referer = str(primary_seed_url or source_url) if is_internal_link(target_url, municipality_domain) else None
        ok, final_target_url, page_text, _, _ = fetch_and_record(
            target_url,
            "candidate",
            source_url,
            referer=referer,
        )
        active_url = normalize_url(final_target_url or target_url) or (final_target_url or target_url)

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
            c_count, l_count = process_text_extractions(conn, municipality_id, active_url, page_text)
            stats["contacts"] += c_count
            stats["locations"] += l_count

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
