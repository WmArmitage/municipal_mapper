from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import db
from src.discover import extract_links_from_html, extract_links_from_sitemap_xml, select_high_value_links
from src.http_client import candidate_sitemap_urls, fetch_url
from src.normalize import get_domain, make_id, normalize_url, normalize_whitespace
from src.parsers import (
    classify_service_link,
    extract_contacts,
    extract_locations,
    location_dedupe_key,
)
from src.vendors import detect_vendor


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
    if not website_url:
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
    session = requests.Session()

    def register_vendor_signal(vendor: str, confidence: float, url: str) -> None:
        prior = vendor_best.get(vendor)
        if prior is None or confidence > prior[0]:
            vendor_best[vendor] = (confidence, url)

    def fetch_and_record(url: str, page_type: str, discovered_from: str) -> tuple[bool, str | None, str | None, str | None]:
        result = fetch_url(url, session=session, timeout=timeout)
        final_url = result.final_url or url

        if not result.ok:
            upsert_signal(
                conn,
                municipality_id,
                "crawl_error",
                json.dumps({"url": final_url, "error": result.error, "status": result.status_code}),
                0.7,
                final_url,
            )
            return False, final_url, None, result.content_type

        if final_url in seen_fetched_urls:
            return True, final_url, result.text, result.content_type
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

        return True, final_url, result.text, result.content_type

    home_ok, home_url, home_text, _ = fetch_and_record(website_url, "homepage", "seed")
    if not home_ok or not home_url:
        upsert_signal(conn, municipality_id, "crawl_status", "homepage_fetch_failed", 1.0, website_url)
        db.commit(conn)
        return stats

    if home_text:
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / f"{municipality_id}_homepage.html").write_text(home_text, encoding="utf-8", errors="ignore")
        c_count, l_count = process_text_extractions(conn, municipality_id, home_url, home_text)
        stats["contacts"] += c_count
        stats["locations"] += l_count

    for sitemap_url in candidate_sitemap_urls(home_url):
        ok, final_sitemap_url, sitemap_text, _ = fetch_and_record(sitemap_url, "sitemap", home_url)
        if ok and final_sitemap_url and sitemap_text:
            (raw_dir / f"{municipality_id}_{make_id('raw', final_sitemap_url, length=10)}.txt").write_text(
                sitemap_text, encoding="utf-8", errors="ignore"
            )

    high_value_links = select_high_value_links(discovered_links, min_score=2.5, max_links=max_candidate_pages)

    for candidate in high_value_links:
        target_url = str(candidate["url"])
        source_url = str(candidate.get("source_url") or home_url)
        label = str(candidate.get("label") or "")
        link_score = float(candidate.get("score") or 0.0)

        category, class_conf = classify_service_link(target_url, label)
        ok, final_target_url, page_text, _ = fetch_and_record(target_url, "candidate", source_url)
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

    upsert_signal(conn, municipality_id, "crawl_status", "completed", 1.0, home_url)
    upsert_signal(conn, municipality_id, "fetched_pages_count", str(stats["fetched_pages"]), 1.0, home_url)
    upsert_signal(conn, municipality_id, "high_value_links_count", str(len(high_value_links)), 0.95, home_url)
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


if __name__ == "__main__":
    main()
