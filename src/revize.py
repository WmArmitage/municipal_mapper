from __future__ import annotations

from collections import Counter, deque
from html import unescape as html_unescape
import re
from typing import Callable, Iterable
from urllib.parse import urlparse

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover - fallback for minimal environments
    BeautifulSoup = None

from src.http_client import FetchResult, create_session
from src.normalize import ensure_url_has_scheme, get_domain, normalize_url, normalize_whitespace
from src.parsers import (
    extract_contacts,
    extract_emails,
    extract_emails_from_href,
    extract_phone_candidates,
    infer_email_type,
)

REVIZE_DIRECT_PATHS = (
    "/government/staff_directory.php",
    "/departments/staff_directory.php",
    "/departments/staff_directory_.php",
    "/staff_directory.php",
    "/staff-directory",
    "/directory",
)
REVIZE_PRIORITY_PATHS = (
    "/staff_directory/index.php",
    "/staff_directory_.php",
    "/government/directory_of_services/index.php",
    "/departments/index.php",
    "/contact_us/index.php",
)
REVIZE_SECTION_ROOTS = (
    "/government/",
    "/departments/",
    "/town_hall/",
    "/city_hall/",
    "/administration/",
)
REVIZE_ROLE_DISCOVERY_TERMS = (
    "assessor",
    "tax collector",
    "collector of revenue",
    "town clerk",
    "city clerk",
    "building",
    "zoning",
    "inspection",
    "planning",
    "land use",
    "finance",
    "accounting",
    "treasurer",
    "town manager",
    "town administrator",
    "first selectman",
    "selectmen",
    "mayor",
)
REVIZE_SECTION_DIRECTORY_SUFFIXES = (
    "staff_directory.php",
    "staff_directory_.php",
    "staff-directory",
    "directory",
    "contact",
    "contacts",
)
REVIZE_HARVEST_TOKENS = (
    "staff",
    "directory",
    "contact",
    "employee",
    "official",
    "department",
    "town hall",
    "city hall",
    "administration",
    "read more",
    "profile",
)
REVIZE_DETECTION_TOKENS = (
    "staff directory",
    "contact",
    "email",
    "phone",
    "read more",
)
REVIZE_TABLE_HEADER_HINTS = {
    "name",
    "title",
    "department",
    "phone",
    "email",
    "location",
    "profession",
    "office",
}
REVIZE_TITLE_HINTS = (
    "director",
    "manager",
    "administrator",
    "clerk",
    "collector",
    "assessor",
    "chief",
    "officer",
    "coordinator",
    "superintendent",
    "mayor",
    "selectman",
    "assistant",
    "treasurer",
    "accounting",
    "enforcement officer",
)
REVIZE_DEPARTMENT_HINTS = (
    "administration",
    "finance",
    "police",
    "fire",
    "public works",
    "town clerk",
    "city clerk",
    "assessor",
    "tax collector",
    "zoning",
    "planning",
    "human resources",
    "department",
    "office",
    "division",
    "town hall",
    "city hall",
)
REVIZE_GENERIC_HEADING_REJECTS = {
    "administration",
    "finance",
    "police",
    "fire",
    "public works",
    "departments",
    "government",
    "town hall",
    "city hall",
}
REVIZE_ACTION_TEXT_REJECTS = {
    "email",
    "phone",
    "contact",
    "read more",
    "staff directory",
    "contact info",
    "office phone",
    "location",
    "learn more",
    "details",
    "view",
    "click here",
}
REVIZE_NAME_LITERAL_REJECTS = {
    "email",
    "phone",
    "contact",
    "staff directory",
    "contact info",
    "services",
    "links",
    "request",
    "information",
}
REVIZE_NAME_PHRASE_REJECTS = {
    "quick links",
    "online services",
    "ada services",
    "absentee ballots",
    "if you require",
    "to request",
    "contact us",
    "town hall",
    "municipal center",
    "main street",
    "community development",
    "groton long point",
    "main level",
    "level ridgefield",
}
REVIZE_NAME_TOKEN_REJECTS = {
    "level",
    "floor",
    "elementary",
    "office",
    "services",
    "payments",
    "phone",
    "fax",
    "hours",
    "staff",
    "links",
    "resource",
}
REVIZE_CLEAR_NAME_DROP_PATTERNS = (
    "email me",
    "click here",
    "request",
    "office hours",
)
REVIZE_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
REVIZE_NAME_PARTICLES = {"de", "del", "de la", "la", "van", "von", "da", "di"}
REVIZE_VACANCY_TOKENS = {"vacant", "vacancy", "position vacant"}
REVIZE_EXCLUDED_SELECTOR_PATTERNS = (
    "footer",
    ".footer-links-box",
    ".rz-btns-container",
    "#hours-wrap",
    ".resource-link",
)
REVIZE_STRUCTURAL_TEXT_REJECTS = (
    "office hours",
    "resources",
    "related links",
)
REVIZE_NAME_REJECT_FRAGMENT_TOKENS = (
    "contact",
    "services",
    "links",
    "request",
    "information",
)
REVIZE_LOCATION_NAME_REJECT_TOKENS = (
    "street",
    "avenue",
    "road",
    "drive",
    "lane",
    "boulevard",
    "highway",
    "main st",
    "town hall",
    "city hall",
    "municipal center",
    "point",
    "ridgefield",
    "elementary",
    "school",
    "community",
    "town of",
    "city of",
)
REVIZE_DEPARTMENT_LITERAL_REJECTS = {
    "education",
    "contact us",
    "office hours",
    "resources",
    "related links",
    "bids and rfp's",
    "bids and rfps",
    "quick links",
    "online services",
    "community development",
}
REVIZE_NON_CONTACT_KEYWORDS = (
    "permit",
    "regulation",
    "code",
    "application",
    "certificate",
    "affidavit",
    "cover page",
)
REVIZE_NON_CONTACT_NAME_LITERALS = {
    "development permit",
    "adopted march",
    "cover page",
    "international code council",
}
REVIZE_OUTCOME_LABELS = (
    "ok_detected",
    "not_detected",
    "not_found",
    "empty_response",
    "other_http_error",
)
REVIZE_MAX_DISCOVERED_PROFILE_PAGES = 35
REVIZE_MAX_TOTAL_CANDIDATES = 90
REVIZE_MAX_GENERATED_CANDIDATES = 120
REVIZE_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}
REVIZE_PAGE_CLASS_ROUTE_ORDER = {
    "staff_directory": 0,
    "department_page": 1,
    "contact_hub": 2,
    "generic": 3,
}
REVIZE_PAGE_CLASS_PRIORITY_SCORE = {
    "staff_directory": 120,
    "department_page": 85,
    "contact_hub": 45,
    "generic": 15,
}
REVIZE_CANDIDATE_ORIGIN_BOOST = {
    "priority_path": 25,
    "harvested_link": 15,
    "department_index_discovery": 12,
    "direct_path": 10,
    "section_enumeration": 8,
    "section_root": 6,
    "discovered_link": 5,
}
REVIZE_RECONSTRUCTION_SOURCE_TYPE = "reconstructed_contact_block"
REVIZE_RECONSTRUCTION_SAMPLE_LIMIT = 5

FetchFn = Callable[[str, str | None, dict[str, str] | None], FetchResult]


def build_revize_candidate_urls(
    municipality_homepage: str,
    harvested_links: Iterable[dict[str, str] | str] | None = None,
    max_candidates: int = REVIZE_MAX_GENERATED_CANDIDATES,
) -> list[dict[str, object]]:
    base = normalize_url(ensure_url_has_scheme(municipality_homepage))
    if not base:
        return []
    roots = _candidate_base_roots(base)
    out: list[dict[str, object]] = []
    seen: set[str] = set()
    base_domain = (get_domain(base) or "").lower()

    def add(url: str, source_kind: str, candidate_origin: str) -> None:
        if len(out) >= max_candidates:
            return
        normalized = normalize_url(url)
        if not normalized:
            return
        if normalized in seen:
            return
        if base_domain and not _is_internal_url(normalized, base_domain):
            return
        candidate_page_class = classify_revize_page_class_for_url(
            normalized,
            source_kind=source_kind,
            label="",
            html_text="",
        )
        route_order = _revize_page_class_route_order(candidate_page_class)
        priority_score = _revize_candidate_priority_score(
            candidate_page_class,
            source_kind=source_kind,
            candidate_origin=candidate_origin,
            url=normalized,
            label="",
        )
        seen.add(normalized)
        out.append(
            {
                "url": normalized,
                "source_kind": source_kind,
                "candidate_origin": candidate_origin,
                "candidate_page_class": candidate_page_class,
                "candidate_route_order": route_order,
                "candidate_priority_score": priority_score,
                "priority_candidate": 1 if candidate_origin == "priority_path" else 0,
            }
        )

    for root in roots:
        for path in REVIZE_PRIORITY_PATHS:
            add(
                _join_candidate_url(root, path),
                _source_kind_from_path(path),
                "priority_path",
            )
            if len(out) >= max_candidates:
                break
        if len(out) >= max_candidates:
            break
        for path in REVIZE_DIRECT_PATHS:
            add(
                _join_candidate_url(root, path),
                _source_kind_from_path(path),
                "direct_path",
            )
            if len(out) >= max_candidates:
                break
        if len(out) >= max_candidates:
            break
        for section_root in REVIZE_SECTION_ROOTS:
            add(_join_candidate_url(root, section_root), "department_index_page", "section_root")
            for suffix in REVIZE_SECTION_DIRECTORY_SUFFIXES:
                add(
                    _join_candidate_url(root, f"{section_root.rstrip('/')}/{suffix}"),
                    _source_kind_from_path(suffix),
                    "section_enumeration",
                )
                if len(out) >= max_candidates:
                    break
            if len(out) >= max_candidates:
                break
        if len(out) >= max_candidates:
            break

    if harvested_links:
        for link in harvested_links:
            if len(out) >= max_candidates:
                break
            if isinstance(link, str):
                raw_url = link
                label = ""
            else:
                raw_url = str(link.get("url") or "")
                label = str(link.get("label") or "")
            normalized = normalize_url(raw_url, base_url=base)
            if not normalized:
                continue
            if base_domain and not _is_internal_url(normalized, base_domain):
                continue
            if not _looks_revize_contact_like(normalized, label):
                continue
            add(normalized, _source_kind_from_link(normalized, label), "harvested_link")

    return order_revize_candidates(out, max_candidates=max_candidates)


def order_revize_candidates(
    candidates: Iterable[dict[str, object]],
    max_candidates: int = REVIZE_MAX_GENERATED_CANDIDATES,
) -> list[dict[str, object]]:
    indexed: list[tuple[int, dict[str, object]]] = [(idx, dict(candidate)) for idx, candidate in enumerate(candidates)]
    indexed.sort(key=lambda item: _revize_candidate_sort_key(item[1], item[0]))
    out: list[dict[str, object]] = []
    seen: set[str] = set()
    for _, candidate in indexed:
        normalized = normalize_url(str(candidate.get("url") or "")) or str(candidate.get("url") or "")
        if not normalized or normalized in seen:
            continue
        candidate["url"] = normalized
        if not candidate.get("candidate_page_class"):
            candidate["candidate_page_class"] = classify_revize_page_class_for_url(
                normalized,
                source_kind=str(candidate.get("source_kind") or ""),
                label=str(candidate.get("label") or ""),
                html_text="",
            )
        if "candidate_route_order" not in candidate:
            candidate["candidate_route_order"] = _revize_page_class_route_order(
                str(candidate.get("candidate_page_class") or "generic")
            )
        if "candidate_priority_score" not in candidate:
            candidate["candidate_priority_score"] = _revize_candidate_priority_score(
                str(candidate.get("candidate_page_class") or "generic"),
                source_kind=str(candidate.get("source_kind") or ""),
                candidate_origin=str(candidate.get("candidate_origin") or ""),
                url=normalized,
                label=str(candidate.get("label") or ""),
            )
        seen.add(normalized)
        out.append(candidate)
        if len(out) >= max_candidates:
            break
    return out


def classify_revize_page_class_for_url(
    url: str,
    source_kind: str = "",
    label: str = "",
    html_text: str = "",
) -> str:
    lowered_url = (url or "").lower()
    lowered_blob = (normalize_whitespace(_extract_text_blob(html_text)) or "").lower()
    lowered_label = (normalize_whitespace(label) or "").lower()
    lowered_source_kind = (source_kind or "").lower()
    combined = " ".join(part for part in (lowered_url, lowered_blob, lowered_label, lowered_source_kind) if part)

    if any(token in combined for token in ("staff_directory", "staff-directory", "staff contacts", "staff directory")):
        return "staff_directory"
    if (
        "/departments/" in lowered_url
        or "/departments/index" in lowered_url
        or "department_page" in lowered_source_kind
        or "breadcrumb" in lowered_blob and "departments" in lowered_blob
    ):
        return "department_page"
    if any(token in combined for token in ("contact_us", "directory_of_services", "/contact", "contact us", "services")):
        return "contact_hub"
    return "generic"


def score_revize_page_class(page_class: str) -> float:
    normalized = (page_class or "").strip().lower()
    if normalized == "staff_directory":
        return 1.0
    if normalized == "department_page":
        return 0.75
    if normalized == "contact_hub":
        return 0.4
    return 0.2


def _revize_page_class_route_order(page_class: str) -> int:
    normalized = (page_class or "").strip().lower()
    return int(REVIZE_PAGE_CLASS_ROUTE_ORDER.get(normalized, REVIZE_PAGE_CLASS_ROUTE_ORDER["generic"]))


def _revize_candidate_priority_score(
    page_class: str,
    source_kind: str,
    candidate_origin: str,
    url: str,
    label: str,
) -> int:
    normalized_page_class = (page_class or "").strip().lower() or "generic"
    base_score = int(REVIZE_PAGE_CLASS_PRIORITY_SCORE.get(normalized_page_class, REVIZE_PAGE_CLASS_PRIORITY_SCORE["generic"]))
    origin_boost = int(REVIZE_CANDIDATE_ORIGIN_BOOST.get((candidate_origin or "").strip().lower(), 0))
    source_kind_lower = (source_kind or "").lower()
    label_blob = f"{(url or '').lower()} {(label or '').lower()} {source_kind_lower}"
    keyword_boost = 0
    if normalized_page_class == "staff_directory":
        keyword_boost += 6
    if any(token in label_blob for token in REVIZE_ROLE_DISCOVERY_TERMS):
        keyword_boost += 10
    return base_score + origin_boost + keyword_boost


def _revize_candidate_sort_key(candidate: dict[str, object], index: int) -> tuple[int, int, int]:
    page_class = str(candidate.get("candidate_page_class") or "")
    route_order = _revize_page_class_route_order(page_class)
    priority_score = _coerce_int(candidate.get("candidate_priority_score"))
    return (route_order, -priority_score, index)


def _enqueue_revize_candidate(
    queue: deque[dict[str, object]],
    candidate: dict[str, object],
) -> None:
    candidate_row = dict(candidate)
    candidate_row["candidate_page_class"] = candidate_row.get("candidate_page_class") or classify_revize_page_class_for_url(
        str(candidate_row.get("url") or ""),
        source_kind=str(candidate_row.get("source_kind") or ""),
        label=str(candidate_row.get("label") or ""),
        html_text="",
    )
    candidate_row["candidate_route_order"] = _revize_page_class_route_order(str(candidate_row.get("candidate_page_class") or "generic"))
    candidate_row["candidate_priority_score"] = _coerce_int(candidate_row.get("candidate_priority_score")) or _revize_candidate_priority_score(
        str(candidate_row.get("candidate_page_class") or "generic"),
        source_kind=str(candidate_row.get("source_kind") or ""),
        candidate_origin=str(candidate_row.get("candidate_origin") or ""),
        url=str(candidate_row.get("url") or ""),
        label=str(candidate_row.get("label") or ""),
    )
    candidate_key = _revize_candidate_sort_key(candidate_row, -1)
    inserted = False
    for idx, existing in enumerate(queue):
        existing_key = _revize_candidate_sort_key(existing, idx)
        if candidate_key < existing_key:
            queue.insert(idx, candidate_row)
            inserted = True
            break
    if not inserted:
        queue.append(candidate_row)


def classify_revize_page(
    html_text: str,
    url: str,
    status_code: int | None = None,
) -> dict[str, object]:
    blob = _extract_text_blob(html_text)
    lowered_blob = blob.lower()
    lowered_url = (url or "").lower()
    signals: list[str] = []

    if "staff_directory.php" in lowered_url:
        signals.append("url:staff_directory.php")
    if any(token in lowered_url for token in ("/staff-directory", "/directory", "/staff/")):
        signals.append("url:directory_path")
    if any(token in lowered_blob for token in REVIZE_DETECTION_TOKENS):
        signals.append("text:staff_contact_terms")

    header_hits = _count_table_header_hits(html_text)
    if header_hits >= 2:
        signals.append("table:contact_headers")

    sidebar_staff_hits = _count_sidebar_staff_blocks(html_text)
    if sidebar_staff_hits >= 1:
        signals.append("block:sidebar_staff")

    contact_card_hits = _count_contact_card_hits(html_text)
    if contact_card_hits >= 1:
        signals.append("block:contact_card")

    inline_staff_hits = _count_inline_staff_hits(html_text)
    if inline_staff_hits >= 1:
        signals.append("block:inline_staff")

    labeled_staff_hits = _count_labeled_staff_hits(html_text)
    if labeled_staff_hits >= 1:
        signals.append("block:labeled_staff")

    profile_block_hits = _count_profile_block_hits(html_text)
    if profile_block_hits >= 2:
        signals.append("block:profile_repeat")

    department_section_hits = _count_department_sections(html_text)
    if department_section_hits >= 1:
        signals.append("section:department_heading")

    key_value_hits = _count_key_value_hits(lowered_blob)
    if key_value_hits >= 3:
        signals.append("text:key_value_fields")

    read_more_hits = lowered_blob.count("read more")
    if read_more_hits:
        signals.append("text:read_more")
    breadcrumb_department_hits = 1 if "departments" in lowered_blob and (" > " in lowered_blob or "breadcrumb" in lowered_blob) else 0
    if breadcrumb_department_hits:
        signals.append("context:department_breadcrumb")
    contact_hub_hits = 0
    if any(token in lowered_url for token in ("contact_us", "directory_of_services")):
        contact_hub_hits += 2
        signals.append("url:contact_hub")
    if "contact us" in lowered_blob:
        contact_hub_hits += 1
        signals.append("text:contact_us")
    if "directory of services" in lowered_blob:
        contact_hub_hits += 1
        signals.append("text:directory_of_services")

    source_type = "unknown"
    if sidebar_staff_hits >= 1:
        source_type = "sidebar_staff"
    elif contact_card_hits >= 1:
        source_type = "contact_card"
    elif inline_staff_hits >= 1:
        source_type = "inline_staff_list"
    elif labeled_staff_hits >= 1:
        source_type = "labeled_staff"
    elif key_value_hits >= 3 and profile_block_hits <= 1 and header_hits == 0:
        source_type = "single_profile_page"
    elif header_hits >= 2:
        source_type = "table_directory"
    elif profile_block_hits >= 2:
        source_type = "profile_block"
    elif department_section_hits >= 1:
        source_type = "department_section"

    matched = False
    if status_code is None or 200 <= status_code < 400:
        matched = (
            sidebar_staff_hits >= 1
            or contact_card_hits >= 1
            or inline_staff_hits >= 1
            or labeled_staff_hits >= 1
            or header_hits >= 2
            or profile_block_hits >= 2
            or key_value_hits >= 3
            or (
                ("staff directory" in lowered_blob or "directory" in lowered_url)
                and ("email" in lowered_blob and "phone" in lowered_blob)
            )
            or (
                ("staff_directory.php" in lowered_url or "/staff-directory" in lowered_url)
                and ("email" in lowered_blob or "phone" in lowered_blob)
            )
        )

    page_class = classify_revize_page_class_for_url(
        url=url,
        source_kind=source_type,
        label="",
        html_text=html_text,
    )
    if header_hits >= 2 or sidebar_staff_hits >= 1:
        page_class = "staff_directory"
    elif page_class != "staff_directory" and (
        "/departments/" in lowered_url
        or breadcrumb_department_hits >= 1
        or (department_section_hits >= 1 and (contact_card_hits >= 1 or inline_staff_hits >= 1 or labeled_staff_hits >= 1))
    ):
        page_class = "department_page"
    elif page_class not in {"staff_directory", "department_page"} and (
        contact_hub_hits >= 2
        or ("contact" in lowered_url and profile_block_hits <= 1 and header_hits <= 1 and sidebar_staff_hits == 0)
    ):
        page_class = "contact_hub"
    elif not matched:
        page_class = "generic"
    page_priority_score = score_revize_page_class(page_class)

    return {
        "page_kind": "staff_directory_or_profile" if matched else "unknown",
        "signals": sorted(set(signals)),
        "source_type": source_type,
        "page_class": page_class,
        "page_priority_score": page_priority_score,
        "sidebar_staff_hits": sidebar_staff_hits,
        "contact_card_hits": contact_card_hits,
        "inline_staff_hits": inline_staff_hits,
        "labeled_staff_hits": labeled_staff_hits,
        "header_hits": header_hits,
        "profile_block_hits": profile_block_hits,
        "department_section_hits": department_section_hits,
        "breadcrumb_department_hits": breadcrumb_department_hits,
        "contact_hub_hits": contact_hub_hits,
        "key_value_hits": key_value_hits,
        "read_more_hits": read_more_hits,
    }


def is_revize_staff_page(html_text: str, url: str) -> tuple[bool, list[str]]:
    classified = classify_revize_page(html_text=html_text, url=url)
    return str(classified.get("page_kind") or "") == "staff_directory_or_profile", list(
        classified.get("signals") or []
    )


def extract_revize_contacts(
    html_text: str,
    source_url: str,
    source_kind: str = "unknown",
) -> list[dict[str, str | float | None]]:
    contacts, _, _, _ = _extract_revize_contacts_with_diagnostics(
        html_text=html_text,
        source_url=source_url,
        source_kind=source_kind,
    )
    return contacts


def extract_revize_table_directory(
    html_text: str,
    source_url: str,
) -> list[dict[str, str | float | None]]:
    if BeautifulSoup is None:
        return _extract_table_contacts_regex(html_text, source_url)
    try:
        soup = BeautifulSoup(html_text or "", "html.parser")
    except Exception:
        return []

    out: list[dict[str, str | float | None]] = []
    for table in soup.find_all("table"):
        headers = _extract_table_headers(table)
        if not headers:
            continue
        mapping = _map_revize_headers(headers)
        if len(mapping) < 2 and _table_header_hits(headers) < 2:
            continue

        department_hint = _nearest_department_heading(table)
        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            if row.find_all("th") and not row.find_all("td"):
                continue

            values = [normalize_whitespace(cell.get_text(" ", strip=True)) or "" for cell in cells]
            values = [value for value in values if value]
            if not values:
                continue
            row_blob = " | ".join(values)
            emails = set(extract_emails(row_blob))
            for anchor in row.find_all("a", href=True):
                emails.update(extract_emails_from_href(str(anchor.get("href") or "")))
            phones = extract_phone_candidates(row_blob)
            if not emails and not phones:
                continue

            name = _safe_cell_value(values, mapping.get("name"))
            title = _safe_cell_value(values, mapping.get("title"))
            department = _safe_cell_value(values, mapping.get("department")) or department_hint

            if not name:
                name = _guess_name_from_values(values)
            if not title:
                title = _guess_title_from_values(values, name=name)
            if not department:
                department = _guess_department_from_values(values)

            phone = str(phones[0].get("phone") or "") if phones else ""
            phone_ext = str(phones[0].get("phone_ext") or "") if phones else ""
            email = sorted(emails)[0].lower() if emails else ""
            out.append(
                {
                    "name": name,
                    "title": title,
                    "department": department,
                    "email": email or None,
                    "email_type": infer_email_type(email),
                    "phone": phone or None,
                    "phone_ext": phone_ext or None,
                    "address": None,
                    "hours": None,
                    "source_context": row_blob[:240],
                    "source_url": source_url,
                    "confidence": 0.83,
                    "revize_source_type": "table_directory",
                }
            )
    return out


def extract_revize_profile_blocks(
    html_text: str,
    source_url: str,
) -> list[dict[str, str | float | None]]:
    if BeautifulSoup is None:
        return _extract_profile_blocks_regex(html_text, source_url)
    try:
        soup = BeautifulSoup(html_text or "", "html.parser")
    except Exception:
        return []

    candidate_blocks = _discover_profile_blocks(soup)
    out: list[dict[str, str | float | None]] = []
    for block in candidate_blocks:
        snippet = normalize_whitespace(block.get_text("\n", strip=True)) or ""
        lines = _clean_lines(snippet.splitlines())
        if not lines:
            continue
        emails = set(extract_emails(snippet))
        for anchor in block.find_all("a", href=True):
            emails.update(extract_emails_from_href(str(anchor.get("href") or "")))
        phones = extract_phone_candidates(snippet)
        if not emails and not phones:
            continue

        heading_name = _find_heading_name(block)
        name = heading_name or _guess_name_from_values(lines)
        title = _guess_title_from_values(lines, name=name)
        department = _guess_department_from_values(lines) or _nearest_department_heading(block)
        phone = str(phones[0].get("phone") or "") if phones else ""
        phone_ext = str(phones[0].get("phone_ext") or "") if phones else ""
        email = sorted(emails)[0].lower() if emails else ""
        out.append(
            {
                "name": name,
                "title": title,
                "department": department,
                "email": email or None,
                "email_type": infer_email_type(email),
                "phone": phone or None,
                "phone_ext": phone_ext or None,
                "address": None,
                "hours": None,
                "source_context": " | ".join(lines[:6])[:240],
                "source_url": source_url,
                "confidence": 0.79,
                "revize_source_type": "profile_block",
            }
        )
    return out


def extract_revize_department_sections(
    html_text: str,
    source_url: str,
) -> list[dict[str, str | float | None]]:
    if BeautifulSoup is None:
        return _extract_department_sections_line_fallback(html_text, source_url)
    try:
        soup = BeautifulSoup(html_text or "", "html.parser")
    except Exception:
        return []

    out: list[dict[str, str | float | None]] = []
    for heading, section_text in _iter_department_sections(soup):
        lines = _clean_lines(section_text.splitlines())
        if not lines:
            continue
        chunk = []
        for line in lines:
            chunk.append(line)
            lowered = line.lower()
            has_contact = bool(extract_emails(line) or extract_phone_candidates(line))
            if has_contact or any(token in lowered for token in ("read more", "office phone", "email")):
                chunk_blob = " | ".join(chunk[-5:])
                emails = set(extract_emails(chunk_blob))
                phones = extract_phone_candidates(chunk_blob)
                if not emails and not phones:
                    continue
                name = _guess_name_from_values(chunk[-4:])
                title = _guess_title_from_values(chunk[-4:], name=name)
                department = heading or _guess_department_from_values(chunk[-4:])
                phone = str(phones[0].get("phone") or "") if phones else ""
                phone_ext = str(phones[0].get("phone_ext") or "") if phones else ""
                email = sorted(emails)[0].lower() if emails else ""
                out.append(
                    {
                        "name": name,
                        "title": title,
                        "department": department,
                        "email": email or None,
                        "email_type": infer_email_type(email),
                        "phone": phone or None,
                        "phone_ext": phone_ext or None,
                        "address": None,
                        "hours": None,
                        "source_context": chunk_blob[:240],
                        "source_url": source_url,
                        "confidence": 0.74,
                        "revize_source_type": "department_section",
                    }
                )
                chunk = []
    return out


def extract_revize_single_profile_page(
    html_text: str,
    source_url: str,
) -> list[dict[str, str | float | None]]:
    lines = _extract_lines(html_text)
    if not lines:
        return []

    key_values: dict[str, str] = {}
    for line in lines:
        match = re.match(
            r"(?i)^\s*(name|title|position|profession|department|office|phone|office phone|email|location)\s*[:\-]\s*(.+)$",
            line,
        )
        if not match:
            continue
        key = match.group(1).strip().lower()
        value = normalize_whitespace(match.group(2)) or ""
        if value and key not in key_values:
            key_values[key] = value

    if not key_values:
        return []

    name = key_values.get("name")
    title = key_values.get("title") or key_values.get("position") or key_values.get("profession")
    department = key_values.get("department") or key_values.get("office") or key_values.get("location")
    email_candidates = extract_emails(key_values.get("email", ""))
    phone_candidates = extract_phone_candidates(
        f"{key_values.get('phone', '')} {key_values.get('office phone', '')}"
    )
    if not email_candidates and not phone_candidates:
        return []

    if not name:
        title_guess = _extract_title_heading(html_text)
        if title_guess and _looks_like_person_name(title_guess):
            name = title_guess

    email = email_candidates[0].lower() if email_candidates else ""
    phone = str(phone_candidates[0].get("phone") or "") if phone_candidates else ""
    phone_ext = str(phone_candidates[0].get("phone_ext") or "") if phone_candidates else ""
    return [
        {
            "name": name,
            "title": title,
            "department": department,
            "email": email or None,
            "email_type": infer_email_type(email),
            "phone": phone or None,
            "phone_ext": phone_ext or None,
            "address": None,
            "hours": None,
            "source_context": "; ".join(f"{k}:{v}" for k, v in key_values.items())[:240],
            "source_url": source_url,
            "confidence": 0.78,
            "revize_source_type": "single_profile_page",
        }
    ]


def extract_revize_sidebar_staff(
    soup,
    page_url: str,
    page_context: dict[str, object] | None = None,
) -> list[dict[str, str | float | None]]:
    context = dict(page_context or {})
    inferred_department = _infer_department_from_page_context(context, page_url)
    if soup is None:
        return _extract_revize_sidebar_staff_regex(
            html_text=str(context.get("html_text") or ""),
            page_url=page_url,
            department_hint=inferred_department,
        )

    sidebar = soup.select_one("aside#staff-dr")
    if sidebar is None:
        sidebar = soup.find("aside", id=re.compile(r"(?i)staff-?dr"))
    if sidebar is None:
        return []

    out: list[dict[str, str | float | None]] = []
    for staff in sidebar.select("div.staff"):
        heading = staff.find("h4")
        name, title = _extract_name_title_from_heading(heading)
        phone, phone_ext = _extract_phone_from_tel_links(staff)
        email = _extract_email_from_mailto_links(staff)
        department = inferred_department or _nearest_department_heading(staff)
        source_context = normalize_whitespace(staff.get_text(" ", strip=True)) or ""
        source_context, _ = normalize_revize_fragmented_text(source_context)

        if not name and (email or phone):
            out.append(
                {
                    "name": None,
                    "title": "Department Contact",
                    "department": department,
                    "email": email or None,
                    "email_type": infer_email_type(email),
                    "phone": phone or None,
                    "phone_ext": phone_ext or None,
                    "address": None,
                    "hours": None,
                    "source_context": source_context,
                    "source_url": page_url,
                    "confidence": 0.7,
                    "revize_source_type": "department_contact_block",
                }
            )
            continue

        out.append(
            {
                "name": name,
                "title": title,
                "department": department,
                "email": email or None,
                "email_type": infer_email_type(email),
                "phone": phone or None,
                "phone_ext": phone_ext or None,
                "address": None,
                "hours": None,
                "source_context": source_context,
                "source_url": page_url,
                "confidence": 0.9 if (email or phone) else 0.75,
                "revize_source_type": "sidebar_staff",
            }
        )
    return out


def extract_revize_department_contact_info(
    soup,
    page_url: str,
    page_context: dict[str, object] | None = None,
) -> list[dict[str, str | float | None]]:
    context = dict(page_context or {})
    inferred_department = _infer_department_from_page_context(context, page_url)
    if soup is None:
        return _extract_revize_department_contact_info_regex(
            html_text=str(context.get("html_text") or ""),
            page_url=page_url,
            department_hint=inferred_department,
        )

    blocks: list = []
    for heading in soup.find_all(["h2", "h3", "h4", "strong"]):
        heading_text = normalize_whitespace(heading.get_text(" ", strip=True)) or ""
        if not heading_text:
            continue
        if "contact info" not in heading_text.lower() and "contact" not in heading_text.lower():
            continue
        chunks = []
        for sibling in heading.find_next_siblings():
            if sibling.name in {"h1", "h2", "h3", "h4", "strong"}:
                break
            chunks.append(sibling)
            if len(chunks) >= 6:
                break
        if chunks:
            blocks.append((heading, chunks))

    if not blocks:
        main_like = soup.find(["main", "article", "section"], id=re.compile(r"(?i)content|main|article"))
        if main_like is not None:
            blocks.append((main_like, [main_like]))

    out: list[dict[str, str | float | None]] = []
    for _, chunk_nodes in blocks:
        chunk_text = normalize_whitespace(
            " ".join(normalize_whitespace(node.get_text(" ", strip=True)) or "" for node in chunk_nodes)
        ) or ""
        chunk_text, _ = normalize_revize_fragmented_text(chunk_text)
        if not chunk_text:
            continue
        emails = set(extract_emails(chunk_text))
        phones = extract_phone_candidates(chunk_text)
        for node in chunk_nodes:
            for anchor in node.find_all("a", href=True):
                href = str(anchor.get("href") or "")
                emails.update(extract_emails_from_href(href))
            tel_phone, tel_ext = _extract_phone_from_tel_links(node)
            if tel_phone:
                phones.insert(
                    0,
                    {
                        "phone": tel_phone,
                        "phone_ext": tel_ext or None,
                        "source_context": chunk_text,
                    },
                )
        if not emails and not phones:
            continue
        lines = _clean_lines(chunk_text.splitlines() if "\n" in chunk_text else [chunk_text])
        address = _extract_address_like_line(lines)
        hours = _extract_hours_like_line(lines)
        phone = str(phones[0].get("phone") or "") if phones else ""
        phone_ext = str(phones[0].get("phone_ext") or "") if phones else ""
        email = sorted(emails)[0].lower() if emails else ""
        out.append(
            {
                "name": None,
                "title": "Department Contact",
                "department": inferred_department,
                "email": email or None,
                "email_type": infer_email_type(email),
                "phone": phone or None,
                "phone_ext": phone_ext or None,
                "address": address,
                "hours": hours,
                "source_context": chunk_text[:240],
                "source_url": page_url,
                "confidence": 0.72,
                "revize_source_type": "department_contact_block",
            }
        )
    return out


def extract_revize_contact_cards(
    soup,
    page_url: str,
    page_context: dict[str, object] | None = None,
) -> list[dict[str, str | float | None]]:
    context = dict(page_context or {})
    inferred_department = _infer_department_from_page_context(context, page_url)
    if soup is None:
        return _extract_revize_contact_cards_regex(
            html_text=str(context.get("html_text") or ""),
            page_url=page_url,
            department_hint=inferred_department,
        )
    page_level_contact_node = soup.select_one("#contact-info")
    out: list[dict[str, str | float | None]] = []

    for name_node in soup.select(".contact-name"):
        name = normalize_whitespace(name_node.get_text(" ", strip=True)) or None
        if not name:
            continue
        card_root = name_node.find_parent(["div", "li", "article", "section"]) or name_node
        title_node = card_root.select_one(".contact-position") or card_root.find(
            ["p", "div", "span"],
            attrs={"class": re.compile(r"(?i)position|title")},
        )
        title = normalize_whitespace(title_node.get_text(" ", strip=True)) if title_node is not None else None

        phone, phone_ext = _extract_phone_from_tel_links(card_root)
        email = _extract_email_from_mailto_links(card_root)
        source_context = normalize_whitespace(card_root.get_text(" ", strip=True)) or ""
        source_context, _ = normalize_revize_fragmented_text(source_context)
        if (not phone and not email) and page_level_contact_node is not None:
            phone, phone_ext, email = _extract_contact_info_from_node(
                page_level_contact_node,
                fallback_phone=phone,
                fallback_phone_ext=phone_ext,
                fallback_email=email,
            )
            source_context = (
                normalize_whitespace(page_level_contact_node.get_text(" ", strip=True))
                or source_context
            )
            source_context, _ = normalize_revize_fragmented_text(source_context)

        out.append(
            {
                "name": name,
                "title": title,
                "department": inferred_department,
                "email": email or None,
                "email_type": infer_email_type(email),
                "phone": phone or None,
                "phone_ext": phone_ext or None,
                "address": None,
                "hours": None,
                "source_context": source_context[:240],
                "source_url": page_url,
                "confidence": 0.84,
                "revize_source_type": "contact_card",
            }
        )
    return out


def extract_revize_inline_staff_lists(
    soup,
    page_url: str,
    page_context: dict[str, object] | None = None,
) -> list[dict[str, str | float | None]]:
    context = dict(page_context or {})
    inferred_department = _infer_department_from_page_context(context, page_url)
    if soup is None:
        return _extract_revize_inline_staff_lists_regex(
            html_text=str(context.get("html_text") or ""),
            page_url=page_url,
            department_hint=inferred_department,
        )
    out: list[dict[str, str | float | None]] = []

    for node in soup.find_all(["p", "li", "div", "td"]):
        if _is_structural_or_excluded_node(node):
            continue
        mailto_anchors = [
            anchor
            for anchor in node.find_all("a", href=True)
            if str(anchor.get("href") or "").lower().startswith("mailto:")
        ]
        if not mailto_anchors:
            continue
        text = normalize_whitespace(node.get_text(" ", strip=True)) or ""
        text, _ = normalize_revize_fragmented_text(text)
        if not text or "," not in text:
            continue

        for anchor in mailto_anchors:
            email = _extract_email_from_mailto_links(anchor) or _extract_email_from_mailto_links(node)
            if not email:
                continue
            phone, phone_ext = _extract_phone_from_tel_links(node)
            main_part = re.split(r"\s+[–\-]\s+", text, maxsplit=1)[0]
            match = re.match(
                r"^\s*([A-Z][a-zA-Z'`.-]+(?:\s+[A-Z][a-zA-Z'`.-]+){1,2})\s*,\s*(.+?)\s*$",
                main_part,
            )
            if not match:
                continue
            name = normalize_whitespace(match.group(1)) or None
            title = normalize_whitespace(match.group(2)) or None
            out.append(
                {
                    "name": name,
                    "title": title,
                    "department": inferred_department,
                    "email": email or None,
                    "email_type": infer_email_type(email),
                    "phone": phone or None,
                    "phone_ext": phone_ext or None,
                    "address": None,
                    "hours": None,
                    "source_context": text[:240],
                    "source_url": page_url,
                    "confidence": 0.82,
                    "revize_source_type": "inline_staff_list",
                }
            )
    return out


def extract_revize_labeled_staff_blocks(
    soup,
    page_url: str,
    page_context: dict[str, object] | None = None,
) -> list[dict[str, str | float | None]]:
    context = dict(page_context or {})
    inferred_department = _infer_department_from_page_context(context, page_url)
    if soup is None:
        return _extract_revize_labeled_staff_blocks_regex(
            html_text=str(context.get("html_text") or ""),
            page_url=page_url,
            department_hint=inferred_department,
        )
    out: list[dict[str, str | float | None]] = []

    for node in soup.find_all(["p", "li", "div", "td"]):
        if _is_structural_or_excluded_node(node):
            continue
        text = normalize_whitespace(node.get_text(" ", strip=True)) or ""
        text, _ = normalize_revize_fragmented_text(text)
        if ":" not in text:
            continue
        label_match = re.match(r"^\s*([^:]{2,80}):\s*", text)
        if not label_match:
            continue
        label = normalize_whitespace(label_match.group(1)) or None
        if not label or _is_action_text(label):
            continue

        candidate_name = None
        for anchor in node.find_all("a", href=True):
            href = str(anchor.get("href") or "")
            if href.lower().startswith("mailto:"):
                continue
            anchor_text = normalize_whitespace(anchor.get_text(" ", strip=True)) or ""
            if _looks_like_person_name(anchor_text):
                candidate_name = anchor_text
                break
        if not candidate_name:
            candidate_name = _extract_person_name_from_text(text)
        if not candidate_name:
            continue

        phone, phone_ext = _extract_phone_from_tel_links(node)
        email = _extract_email_from_mailto_links(node)
        out.append(
            {
                "name": candidate_name,
                "title": label,
                "department": inferred_department,
                "email": email or None,
                "email_type": infer_email_type(email),
                "phone": phone or None,
                "phone_ext": phone_ext or None,
                "address": None,
                "hours": None,
                "source_context": text[:240],
                "source_url": page_url,
                "confidence": 0.81,
                "revize_source_type": "labeled_staff",
            }
        )
    return out


def group_revize_contact_blocks(
    soup,
    html_text: str,
    source_url: str,
    page_context: dict[str, object] | None = None,
    max_blocks: int = 40,
) -> list[dict[str, object]]:
    if soup is None and BeautifulSoup is not None:
        try:
            soup = BeautifulSoup(html_text or "", "html.parser")
        except Exception:
            soup = None
    if soup is None:
        return _group_revize_contact_blocks_regex(
            html_text=html_text,
            source_url=source_url,
            page_context=page_context,
            max_blocks=max_blocks,
        )

    context = dict(page_context or {})
    department_hint = _infer_department_from_page_context(context, source_url)
    blocks: list[dict[str, object]] = []
    seen_nodes: set[int] = set()

    def add_node(node) -> None:
        if node is None:
            return
        node_id = id(node)
        if node_id in seen_nodes:
            return
        if len(blocks) >= max_blocks:
            return
        if _is_structural_or_excluded_node(node):
            return
        lines: list[str] = []
        for raw_line in node.stripped_strings:
            cleaned = normalize_whitespace(str(raw_line) or "") or ""
            if not cleaned:
                continue
            lines.append(cleaned)
        if not lines:
            return
        text_blob = normalize_whitespace(" | ".join(lines)) or ""
        if not text_blob:
            return
        has_mailto = any(
            str(anchor.get("href") or "").lower().startswith("mailto:")
            for anchor in node.find_all("a", href=True)
        )
        has_tel = any(
            str(anchor.get("href") or "").lower().startswith("tel:")
            for anchor in node.find_all("a", href=True)
        )
        has_email = bool(extract_emails(text_blob))
        has_phone = bool(extract_phone_candidates(text_blob))
        has_digit_fragment = bool(re.search(r"\d", text_blob))
        heading = node.find(["h4", "h3", "h2"])
        if not heading and not (has_mailto or has_tel or has_email or has_phone):
            return
        if heading and not (has_mailto or has_tel or has_email or has_phone or has_digit_fragment):
            return

        blocks.append(
            {
                "node": node,
                "original_lines": lines,
                "source_url": source_url,
                "department_hint": department_hint,
                "has_mailto": has_mailto,
                "has_tel": has_tel,
            }
        )
        seen_nodes.add(node_id)

    for node in soup.select("aside#staff-dr .staff"):
        add_node(node)
        if len(blocks) >= max_blocks:
            return blocks
    for node in soup.find_all(
        ["div", "li", "article", "section"],
        attrs={"class": re.compile(r"(?i)(staff|contact|profile|employee)")},
    ):
        add_node(node)
        if len(blocks) >= max_blocks:
            break
    return blocks


def _extract_text_nodes_from_html_fragment(fragment: str) -> list[str]:
    out: list[str] = []
    for match in re.finditer(r"(?is)>([^<]+)<", fragment or ""):
        cleaned = normalize_whitespace(html_unescape(str(match.group(1) or ""))) or ""
        if cleaned:
            out.append(cleaned)
    return out


def _group_revize_contact_blocks_regex(
    html_text: str,
    source_url: str,
    page_context: dict[str, object] | None = None,
    max_blocks: int = 40,
) -> list[dict[str, object]]:
    context = dict(page_context or {})
    department_hint = _infer_department_from_page_context(context, source_url)
    blocks: list[dict[str, object]] = []
    if not html_text:
        return blocks

    aside_match = re.search(
        r'(?is)<aside[^>]*id\s*=\s*(?:"staff-dr"|\'staff-dr\'|staff-dr)[^>]*>(.*?)</aside>',
        html_text,
    )
    if not aside_match:
        return blocks

    aside_html = aside_match.group(1) or ""
    heading_matches = list(re.finditer(r"(?is)<h4[^>]*>(.*?)</h4>", aside_html))
    for idx, heading_match in enumerate(heading_matches):
        if len(blocks) >= max_blocks:
            break
        start = heading_match.start()
        end = heading_matches[idx + 1].start() if idx + 1 < len(heading_matches) else len(aside_html)
        block_html = aside_html[start:end]
        if not block_html:
            continue
        original_lines = _extract_text_nodes_from_html_fragment(block_html)[:16]
        if not original_lines:
            continue
        has_mailto = bool(re.search(r"(?is)\bhref\s*=\s*(?:\"mailto:[^\"]+\"|'mailto:[^']+')", block_html))
        has_tel = bool(re.search(r"(?is)\bhref\s*=\s*(?:\"tel:[^\"]+\"|'tel:[^']+')", block_html))
        text_blob = normalize_whitespace(" | ".join(original_lines)) or ""
        has_digit_fragment = bool(re.search(r"\d", text_blob))
        if not (has_mailto or has_tel or extract_emails(text_blob) or extract_phone_candidates(text_blob) or has_digit_fragment):
            continue
        blocks.append(
            {
                "node": None,
                "block_html": block_html,
                "original_lines": original_lines,
                "source_url": source_url,
                "department_hint": department_hint,
                "has_mailto": has_mailto,
                "has_tel": has_tel,
            }
        )
    return blocks


def extract_reconstructed_revize_candidates(
    blocks: list[dict[str, object]],
    source_url: str,
    page_context: dict[str, object] | None = None,
    page_class: str = "generic",
    page_priority_score: float = 0.0,
) -> tuple[list[dict[str, str | float | None]], dict[str, object]]:
    context = dict(page_context or {})
    department_hint = _infer_department_from_page_context(context, source_url)
    emitted: list[dict[str, str | float | None]] = []
    skipped_reasons: Counter[str] = Counter()
    sample_rows: list[dict[str, object]] = []
    split_merge_count = 0

    for block in blocks:
        node = block.get("node")
        block_html = str(block.get("block_html") or "")
        has_mailto = bool(block.get("has_mailto"))
        has_tel = bool(block.get("has_tel"))
        original_lines = [normalize_whitespace(str(item) or "") or "" for item in (block.get("original_lines") or [])]
        original_lines = [item for item in original_lines if item][:16]
        if not original_lines:
            skipped_reasons["missing_original_lines"] += 1
            continue

        merged_text, merge_count = normalize_revize_fragmented_text(" ".join(original_lines))
        split_merge_count += int(merge_count)
        merged_text = normalize_whitespace(merged_text) or ""

        if merge_count == 0 and has_tel and has_mailto:
            skipped_reasons["already_structured_sidebar"] += 1
            continue

        name = None
        title = None
        email = None
        phone = None
        phone_ext = None

        if node is not None:
            heading = node.find(["h4", "h3", "h2"])
            if heading is not None:
                name, title = _extract_name_title_from_heading(heading)
            email = _extract_email_from_mailto_links(node)
            phone, phone_ext = _extract_phone_from_tel_links(node)
        elif block_html:
            heading_match = re.search(r"(?is)<h4[^>]*>(.*?)</h4>", block_html)
            if heading_match:
                raw_heading = str(heading_match.group(1) or "")
                title_match = re.search(r"(?is)<span[^>]*>(.*?)</span>", raw_heading)
                if title_match:
                    title = normalize_whitespace(_strip_tags(title_match.group(1) or "")) or None
                name = normalize_whitespace(
                    _strip_tags(re.sub(r"(?is)<span[^>]*>.*?</span>", " ", raw_heading))
                ) or None
            mailto_match = re.search(
                r"""(?is)\bhref\s*=\s*(?:"(mailto:[^"]+)"|'(mailto:[^']+)')""",
                block_html,
            )
            if mailto_match:
                mailto_href = mailto_match.group(1) or mailto_match.group(2) or ""
                emails = extract_emails_from_href(mailto_href)
                if emails:
                    email = emails[0].lower()
            tel_match = re.search(
                r"""(?is)\bhref\s*=\s*(?:"(tel:[^"]+)"|'(tel:[^']+)')""",
                block_html,
            )
            if tel_match:
                tel_href = tel_match.group(1) or tel_match.group(2) or ""
                phone_candidates = extract_phone_candidates(tel_href.split(":", 1)[1] if ":" in tel_href else tel_href)
                if phone_candidates:
                    phone = str(phone_candidates[0].get("phone") or "") or None
                    phone_ext = str(phone_candidates[0].get("phone_ext") or "") or None

        if not name:
            name = _extract_person_name_from_text(merged_text)
        if not title:
            title = _guess_title_from_values(original_lines, name=name)
        if not email:
            email_candidates = extract_emails(merged_text)
            if email_candidates:
                email = email_candidates[0].lower()
        parsed_phone, parsed_phone_ext, _, _ = parse_revize_phone_and_ext(
            phone_value=phone,
            fallback_text=merged_text,
            phone_ext_value=phone_ext,
        )
        if parsed_phone:
            phone = parsed_phone
        if parsed_phone_ext:
            phone_ext = parsed_phone_ext

        department = (
            normalize_whitespace(str(block.get("department_hint") or "")) or department_hint
            or _infer_department_from_source_url(source_url)
        )

        rejection_reason = ""
        accepted = True
        if name and _is_vacancy_name(name):
            accepted = False
            rejection_reason = "vacancy_name"
        elif not name:
            accepted = False
            rejection_reason = "missing_name"
        elif not _accept_revize_person_name(name, title):
            accepted = False
            rejection_reason = "invalid_person_name"
        elif not (title or email or phone):
            accepted = False
            rejection_reason = "missing_supporting_signal"

        trace_sample = {
            "source_url": source_url,
            "original_lines": original_lines,
            "reconstructed_name": name or "",
            "reconstructed_title": title or "",
            "reconstructed_email": email or "",
            "reconstructed_phone": phone or "",
            "phone_ext": phone_ext or "",
            "accepted": 1 if accepted else 0,
            "rejection_reason": rejection_reason or "",
        }
        if len(sample_rows) < REVIZE_RECONSTRUCTION_SAMPLE_LIMIT:
            sample_rows.append(trace_sample)

        if not accepted:
            skipped_reasons[rejection_reason or "reconstruction_rejected"] += 1
            continue

        emitted.append(
            {
                "name": name,
                "title": title,
                "department": department,
                "email": email or None,
                "email_type": infer_email_type(email),
                "phone": phone or None,
                "phone_ext": phone_ext or None,
                "address": None,
                "hours": None,
                "source_context": merged_text[:240],
                "source_url": source_url,
                "confidence": 0.86,
                "revize_source_type": REVIZE_RECONSTRUCTION_SOURCE_TYPE,
                "revize_page_class": page_class,
                "revize_page_priority_score": page_priority_score,
                "original_lines": original_lines,
                "reconstructed_name": name or "",
                "reconstructed_title": title or "",
                "reconstructed_email": email or "",
                "reconstructed_phone": phone or "",
                "reconstructed_phone_ext": phone_ext or "",
                "reconstruction_accepted": 1,
                "reconstruction_rejection_reason": "",
            }
        )

    diagnostics = {
        "revize_reconstruction_blocks_seen": len(blocks),
        "revize_reconstruction_candidates_emitted": len(emitted),
        "revize_reconstruction_skipped_reason": dict(sorted(skipped_reasons.items())),
        "revize_split_text_merged": split_merge_count,
        "reconstructed_rows_sample": sample_rows,
    }
    return emitted, diagnostics


def run_revize_strategy_for_municipality(
    municipality_homepage: str,
    harvested_links: Iterable[dict[str, str] | str] | None = None,
    timeout: int = 20,
    session=None,
    request_headers: dict[str, str] | None = None,
    fetch_fn: FetchFn | None = None,
    max_total_candidates: int = REVIZE_MAX_TOTAL_CANDIDATES,
    max_generated_candidates: int = REVIZE_MAX_GENERATED_CANDIDATES,
) -> dict[str, object]:
    initial_candidates = build_revize_candidate_urls(
        municipality_homepage=municipality_homepage,
        harvested_links=harvested_links,
        max_candidates=max_generated_candidates,
    )
    queue: deque[dict[str, object]] = deque(initial_candidates)
    seen_urls: set[str] = set()
    attempted_rows: list[dict[str, object]] = []
    matched_urls: list[str] = []
    contacts_by_url: list[dict[str, object]] = []
    all_contacts: list[dict[str, str | float | None]] = []
    suppression_reasons: Counter[str] = Counter()
    outcome_counts = {label: 0 for label in REVIZE_OUTCOME_LABELS}
    counters = {
        "candidate_urls_generated_count": len(initial_candidates),
        "candidate_urls_attempted_count": 0,
        "revize_priority_candidates_generated": sum(
            1 for candidate in initial_candidates if _coerce_int(candidate.get("priority_candidate")) == 1
        ),
        "revize_priority_candidates_fetched": 0,
        "http_responses_received_count": 0,
        "pages_fetched_with_body_count": 0,
        "pages_classified_detected_count": 0,
        "revize_staff_directory_pages_found": 0,
        "revize_department_pages_found": 0,
        "revize_contact_hub_pages_found": 0,
        "revize_generic_pages_used": 0,
        "revize_blocks_seen": 0,
        "revize_blocks_filtered_non_contact": 0,
        "revize_blocks_emitted_as_candidates": 0,
        "rows_extracted_total": 0,
        "rows_normalized_seen": 0,
        "rows_normalized_kept": 0,
        "rows_normalized_rejected": 0,
        "rows_kept_vs_dropped_ratio": 0.0,
        "rows_flagged_as_noise": 0,
        "rows_soft_kept": 0,
        "revize_over_filtering_detected": 0,
        "revize_rows_from_staff_directory": 0,
        "revize_rows_from_department_pages": 0,
        "revize_rows_from_contact_hubs": 0,
        "sidebar_staff_blocks_found": 0,
        "sidebar_staff_contacts_extracted": 0,
        "department_contact_blocks_found": 0,
        "department_contact_rows_extracted": 0,
        "revize_footer_blocks_ignored": 0,
        "revize_hours_blocks_ignored": 0,
        "revize_office_contact_blocks": 0,
        "revize_person_blocks": 0,
        "revize_office_contact_rows_classified": 0,
        "revize_person_rows_classified": 0,
        "revize_role_only_rows_demoted": 0,
        "revize_structural_blocks_dropped": 0,
        "revize_invalid_name_rejections": 0,
        "revize_department_contamination_rejections": 0,
        "revize_split_text_merged": 0,
        "revize_phone_extensions_parsed": 0,
        "revize_phone_string_preserved": 0,
        "revize_reconstruction_pages_seen": 0,
        "revize_reconstruction_blocks_seen": 0,
        "revize_reconstruction_candidates_emitted": 0,
        "revize_reconstruction_candidates_persisted": 0,
    }
    extracted_rows_sample: list[dict[str, object]] = []
    normalized_rows_sample: list[dict[str, object]] = []
    rejected_rows_sample: list[dict[str, object]] = []
    reconstructed_rows_sample: list[dict[str, object]] = []
    reconstruction_skipped_reasons: Counter[str] = Counter()

    while queue and len(attempted_rows) < max_total_candidates:
        candidate = queue.popleft()
        request_url = normalize_url(str(candidate.get("url") or "")) or str(candidate.get("url") or "")
        if not request_url or request_url in seen_urls:
            continue
        seen_urls.add(request_url)

        fetch_row = _fetch_revize_candidate(
            municipality_homepage=municipality_homepage,
            candidate=candidate,
            timeout=timeout,
            session=session,
            request_headers=request_headers,
            fetch_fn=fetch_fn,
        )
        if not fetch_row:
            continue
        counters["candidate_urls_attempted_count"] += 1
        if _coerce_int(candidate.get("priority_candidate")) == 1:
            counters["revize_priority_candidates_fetched"] += 1
        text = str(fetch_row.get("text") or "")
        final_url = str(fetch_row.get("final_url") or request_url)
        status_code = _coerce_int(fetch_row.get("status_code")) or None
        has_body = bool(fetch_row.get("has_body"))
        if bool(fetch_row.get("http_response_received")):
            counters["http_responses_received_count"] += 1
        if has_body:
            counters["pages_fetched_with_body_count"] += 1

        page_classification = classify_revize_page(
            html_text=text,
            url=final_url,
            status_code=status_code,
        )
        page_kind = str(page_classification.get("page_kind") or "unknown")
        page_class = str(page_classification.get("page_class") or "generic")
        page_priority_score = float(page_classification.get("page_priority_score") or 0.0)
        if page_class == "staff_directory":
            counters["revize_staff_directory_pages_found"] += 1
        elif page_class == "department_page":
            counters["revize_department_pages_found"] += 1
        elif page_class == "contact_hub":
            counters["revize_contact_hub_pages_found"] += 1
        else:
            counters["revize_generic_pages_used"] += 1
        detected = page_kind == "staff_directory_or_profile"
        if detected:
            counters["pages_classified_detected_count"] += 1
            matched_urls.append(final_url)

        extracted_rows: list[dict[str, str | float | None]] = []
        source_type_counts: dict[str, int] = {}
        per_page_metrics: dict[str, int] = {}
        if detected and text:
            extracted_rows, local_suppression, source_type_counts, per_page_metrics = _extract_revize_contacts_with_diagnostics(
                html_text=text,
                source_url=final_url,
                source_kind=str(fetch_row.get("source_kind") or "unknown"),
                page_class=page_class,
                page_priority_score=page_priority_score,
            )
            suppression_reasons.update(local_suppression)
            counters["sidebar_staff_blocks_found"] += _coerce_int(per_page_metrics.get("sidebar_staff_blocks_found"))
            counters["sidebar_staff_contacts_extracted"] += _coerce_int(
                per_page_metrics.get("sidebar_staff_contacts_extracted")
            )
            counters["department_contact_blocks_found"] += _coerce_int(
                per_page_metrics.get("department_contact_blocks_found")
            )
            counters["department_contact_rows_extracted"] += _coerce_int(
                per_page_metrics.get("department_contact_rows_extracted")
            )
            counters["revize_footer_blocks_ignored"] += _coerce_int(
                per_page_metrics.get("revize_footer_blocks_ignored")
            )
            counters["revize_hours_blocks_ignored"] += _coerce_int(
                per_page_metrics.get("revize_hours_blocks_ignored")
            )
            counters["revize_office_contact_blocks"] += _coerce_int(
                per_page_metrics.get("revize_office_contact_blocks")
            )
            counters["revize_person_blocks"] += _coerce_int(
                per_page_metrics.get("revize_person_blocks")
            )
            counters["revize_office_contact_rows_classified"] += _coerce_int(
                per_page_metrics.get("revize_office_contact_rows_classified")
            )
            counters["revize_person_rows_classified"] += _coerce_int(
                per_page_metrics.get("revize_person_rows_classified")
            )
            counters["revize_role_only_rows_demoted"] += _coerce_int(
                per_page_metrics.get("revize_role_only_rows_demoted")
            )
            counters["revize_structural_blocks_dropped"] += _coerce_int(
                per_page_metrics.get("revize_structural_blocks_dropped")
            )
            counters["revize_invalid_name_rejections"] += _coerce_int(
                per_page_metrics.get("revize_invalid_name_rejections")
            )
            counters["revize_department_contamination_rejections"] += _coerce_int(
                per_page_metrics.get("revize_department_contamination_rejections")
            )
            counters["revize_split_text_merged"] += _coerce_int(
                per_page_metrics.get("revize_split_text_merged")
            )
            counters["revize_phone_extensions_parsed"] += _coerce_int(
                per_page_metrics.get("revize_phone_extensions_parsed")
            )
            counters["revize_phone_string_preserved"] += _coerce_int(
                per_page_metrics.get("revize_phone_string_preserved")
            )
            counters["revize_reconstruction_pages_seen"] += _coerce_int(
                per_page_metrics.get("revize_reconstruction_pages_seen")
            )
            counters["revize_reconstruction_blocks_seen"] += _coerce_int(
                per_page_metrics.get("revize_reconstruction_blocks_seen")
            )
            counters["revize_reconstruction_candidates_emitted"] += _coerce_int(
                per_page_metrics.get("revize_reconstruction_candidates_emitted")
            )
            counters["revize_blocks_seen"] += _coerce_int(per_page_metrics.get("revize_blocks_seen"))
            counters["revize_blocks_filtered_non_contact"] += _coerce_int(
                per_page_metrics.get("revize_blocks_filtered_non_contact")
            )
            counters["revize_blocks_emitted_as_candidates"] += _coerce_int(
                per_page_metrics.get("revize_blocks_emitted_as_candidates")
            )
            counters["rows_extracted_total"] += _coerce_int(per_page_metrics.get("rows_extracted_total"))
            counters["rows_normalized_seen"] += _coerce_int(per_page_metrics.get("rows_normalized_seen"))
            counters["rows_normalized_kept"] += _coerce_int(per_page_metrics.get("rows_normalized_kept"))
            counters["rows_normalized_rejected"] += _coerce_int(per_page_metrics.get("rows_normalized_rejected"))
            counters["rows_flagged_as_noise"] += _coerce_int(per_page_metrics.get("rows_flagged_as_noise"))
            counters["rows_soft_kept"] += _coerce_int(per_page_metrics.get("rows_soft_kept"))
            counters["revize_over_filtering_detected"] += _coerce_int(
                per_page_metrics.get("revize_over_filtering_detected")
            )
            counters["revize_rows_from_staff_directory"] += _coerce_int(
                per_page_metrics.get("revize_rows_from_staff_directory")
            )
            counters["revize_rows_from_department_pages"] += _coerce_int(
                per_page_metrics.get("revize_rows_from_department_pages")
            )
            counters["revize_rows_from_contact_hubs"] += _coerce_int(
                per_page_metrics.get("revize_rows_from_contact_hubs")
            )
            for row in list(per_page_metrics.get("extracted_rows_sample") or []):
                if len(extracted_rows_sample) >= 25:
                    break
                extracted_rows_sample.append(dict(row))
            for row in list(per_page_metrics.get("normalized_rows_sample") or []):
                if len(normalized_rows_sample) >= 25:
                    break
                normalized_rows_sample.append(dict(row))
            for row in list(per_page_metrics.get("rejected_rows_sample") or []):
                if len(rejected_rows_sample) >= 25:
                    break
                rejected_rows_sample.append(dict(row))
            for row in list(per_page_metrics.get("reconstructed_rows_sample") or []):
                if len(reconstructed_rows_sample) >= 25:
                    break
                reconstructed_rows_sample.append(dict(row))
            reconstruction_skipped_reasons.update(
                {
                    str(reason): _coerce_int(count)
                    for reason, count in dict(per_page_metrics.get("revize_reconstruction_skipped_reason") or {}).items()
                }
            )
            if extracted_rows:
                contacts_by_url.append(
                    {
                        "url": final_url,
                        "source_kind": str(fetch_row.get("source_kind") or "unknown"),
                        "extraction_source_type": str(page_classification.get("source_type") or "unknown"),
                        "page_class": page_class,
                        "page_priority_score": page_priority_score,
                        "contacts_extracted": len(extracted_rows),
                        "source_type_counts": source_type_counts,
                        "metrics": per_page_metrics,
                    }
                )
                all_contacts.extend(extracted_rows)

            for discovered in discover_revize_profile_candidates(
                html_text=text,
                base_url=final_url,
                max_candidates=REVIZE_MAX_DISCOVERED_PROFILE_PAGES,
            ):
                discovered_url = normalize_url(str(discovered.get("url") or "")) or str(discovered.get("url") or "")
                if not discovered_url or discovered_url in seen_urls:
                    continue
                _enqueue_revize_candidate(queue, discovered)

            if page_class == "department_page" and _is_revize_department_index_url(final_url):
                for discovered in discover_revize_department_candidates(
                    html_text=text,
                    base_url=final_url,
                    max_candidates=REVIZE_MAX_DISCOVERED_PROFILE_PAGES,
                ):
                    discovered_url = normalize_url(str(discovered.get("url") or "")) or str(discovered.get("url") or "")
                    if not discovered_url or discovered_url in seen_urls:
                        continue
                    _enqueue_revize_candidate(queue, discovered)

        fetch_outcome = _classify_attempt_outcome(
            status_code=status_code,
            has_body=has_body,
            detected=detected,
            http_response_received=bool(fetch_row.get("http_response_received")),
        )
        outcome_counts[fetch_outcome] = outcome_counts.get(fetch_outcome, 0) + 1
        attempted_rows.append(
            {
                "attempt_order": len(attempted_rows) + 1,
                "candidate_url_generated": str(fetch_row.get("request_url") or request_url),
                "request_url": str(fetch_row.get("request_url") or request_url),
                "final_url": final_url,
                "status_code": status_code,
                "fetch_outcome": fetch_outcome,
                "http_response_received": bool(fetch_row.get("http_response_received")),
                "source_kind": str(fetch_row.get("source_kind") or "unknown"),
                "candidate_origin": str(fetch_row.get("candidate_origin") or ""),
                "candidate_page_class": str(candidate.get("candidate_page_class") or ""),
                "candidate_priority_score": _coerce_int(candidate.get("candidate_priority_score")),
                "priority_candidate": _coerce_int(candidate.get("priority_candidate")),
                "directory_match": detected,
                "page_kind": page_kind,
                "page_class": page_class,
                "page_priority_score": page_priority_score,
                "detection_signals": list(page_classification.get("signals") or []),
                "extraction_source_type": str(page_classification.get("source_type") or "unknown"),
                "response_body_length": len(text),
                "contacts_extracted": len(extracted_rows),
                "revize_blocks_seen": _coerce_int(per_page_metrics.get("revize_blocks_seen")),
                "revize_blocks_filtered_non_contact": _coerce_int(
                    per_page_metrics.get("revize_blocks_filtered_non_contact")
                ),
                "revize_blocks_emitted_as_candidates": _coerce_int(
                    per_page_metrics.get("revize_blocks_emitted_as_candidates")
                ),
                "sidebar_staff_blocks_found": _coerce_int(per_page_metrics.get("sidebar_staff_blocks_found")),
                "sidebar_staff_contacts_extracted": _coerce_int(
                    per_page_metrics.get("sidebar_staff_contacts_extracted")
                ),
                "department_contact_blocks_found": _coerce_int(
                    per_page_metrics.get("department_contact_blocks_found")
                ),
                "department_contact_rows_extracted": _coerce_int(
                    per_page_metrics.get("department_contact_rows_extracted")
                ),
                "revize_footer_blocks_ignored": _coerce_int(per_page_metrics.get("revize_footer_blocks_ignored")),
                "revize_hours_blocks_ignored": _coerce_int(per_page_metrics.get("revize_hours_blocks_ignored")),
                "revize_office_contact_blocks": _coerce_int(per_page_metrics.get("revize_office_contact_blocks")),
                "revize_person_blocks": _coerce_int(per_page_metrics.get("revize_person_blocks")),
                "revize_office_contact_rows_classified": _coerce_int(
                    per_page_metrics.get("revize_office_contact_rows_classified")
                ),
                "revize_person_rows_classified": _coerce_int(
                    per_page_metrics.get("revize_person_rows_classified")
                ),
                "revize_role_only_rows_demoted": _coerce_int(
                    per_page_metrics.get("revize_role_only_rows_demoted")
                ),
                "revize_structural_blocks_dropped": _coerce_int(
                    per_page_metrics.get("revize_structural_blocks_dropped")
                ),
                "revize_invalid_name_rejections": _coerce_int(
                    per_page_metrics.get("revize_invalid_name_rejections")
                ),
                "revize_department_contamination_rejections": _coerce_int(
                    per_page_metrics.get("revize_department_contamination_rejections")
                ),
                "revize_split_text_merged": _coerce_int(per_page_metrics.get("revize_split_text_merged")),
                "revize_phone_extensions_parsed": _coerce_int(
                    per_page_metrics.get("revize_phone_extensions_parsed")
                ),
                "revize_phone_string_preserved": _coerce_int(
                    per_page_metrics.get("revize_phone_string_preserved")
                ),
                "revize_reconstruction_pages_seen": _coerce_int(
                    per_page_metrics.get("revize_reconstruction_pages_seen")
                ),
                "revize_reconstruction_blocks_seen": _coerce_int(
                    per_page_metrics.get("revize_reconstruction_blocks_seen")
                ),
                "revize_reconstruction_candidates_emitted": _coerce_int(
                    per_page_metrics.get("revize_reconstruction_candidates_emitted")
                ),
                "revize_reconstruction_skipped_reason": dict(
                    per_page_metrics.get("revize_reconstruction_skipped_reason") or {}
                ),
                "rows_kept_vs_dropped_ratio": float(per_page_metrics.get("rows_kept_vs_dropped_ratio") or 0.0),
                "rows_flagged_as_noise": _coerce_int(per_page_metrics.get("rows_flagged_as_noise")),
                "rows_soft_kept": _coerce_int(per_page_metrics.get("rows_soft_kept")),
                "revize_over_filtering_detected": _coerce_int(
                    per_page_metrics.get("revize_over_filtering_detected")
                ),
                "page_title": str(fetch_row.get("page_title") or ""),
            }
        )

    deduped_contacts = _dedupe_contact_list(all_contacts)
    extraction_source_counts = _count_extraction_sources(deduped_contacts)
    page_class_source_counts = _count_page_class_sources(deduped_contacts)
    attempted_urls = [
        str(row.get("request_url") or "")
        for row in attempted_rows
        if str(row.get("request_url") or "")
    ]
    matched_urls_unique = sorted({url for url in matched_urls if url})
    if counters["rows_normalized_seen"] > 0:
        counters["rows_kept_vs_dropped_ratio"] = round(
            float(counters["rows_normalized_kept"]) / float(counters["rows_normalized_seen"]),
            4,
        )
    if counters["rows_normalized_seen"] >= 8 and float(counters["rows_kept_vs_dropped_ratio"]) < 0.25:
        counters["revize_over_filtering_detected"] = max(1, _coerce_int(counters["revize_over_filtering_detected"]))
        print("[Revize][Warn] revize_over_filtering_detected")

    return {
        "candidate_urls_generated": [str(item.get("url") or "") for item in initial_candidates],
        "candidate_rows_generated": initial_candidates,
        **counters,
        "attempted_count": len(attempted_rows),
        "candidate_urls_attempted_count": counters["candidate_urls_attempted_count"],
        "candidate_urls_attempted": attempted_urls,
        "matched_urls": matched_urls_unique,
        "outcome_counts": outcome_counts,
        "attempted_rows": attempted_rows,
        "contacts_by_url": contacts_by_url,
        "contacts": deduped_contacts,
        "contacts_total": len(deduped_contacts),
        "extraction_source_counts": extraction_source_counts,
        "page_class_source_counts": page_class_source_counts,
        "source_type_counts": extraction_source_counts,
        "suspicious_reduction_counts": dict(sorted(suppression_reasons.items())),
        "suppressed_vacancy_rows": _coerce_int(suppression_reasons.get("suppressed_vacancy_rows")),
        "extracted_rows_sample": extracted_rows_sample,
        "normalized_rows_sample": normalized_rows_sample,
        "rejected_rows_sample": rejected_rows_sample,
        "reconstructed_rows_sample": reconstructed_rows_sample,
        "revize_reconstruction_skipped_reason": dict(sorted(reconstruction_skipped_reasons.items())),
        "revize_pass_produced_contacts": len(deduped_contacts) > 0,
    }


def discover_revize_profile_candidates(
    html_text: str,
    base_url: str,
    max_candidates: int = REVIZE_MAX_DISCOVERED_PROFILE_PAGES,
) -> list[dict[str, object]]:
    if BeautifulSoup is None:
        return []
    try:
        soup = BeautifulSoup(html_text or "", "html.parser")
    except Exception:
        return []

    out: list[dict[str, object]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = normalize_url(str(anchor.get("href") or ""), base_url=base_url)
        if not href or href in seen:
            continue
        label = normalize_whitespace(anchor.get_text(" ", strip=True)) or ""
        if not _looks_revize_contact_like(href, label):
            continue
        seen.add(href)
        source_kind = _source_kind_from_link(href, label)
        page_class = classify_revize_page_class_for_url(
            url=href,
            source_kind=source_kind,
            label=label,
            html_text="",
        )
        out.append(
            {
                "url": href,
                "source_kind": source_kind,
                "candidate_origin": "discovered_link",
                "candidate_page_class": page_class,
                "candidate_route_order": _revize_page_class_route_order(page_class),
                "candidate_priority_score": _revize_candidate_priority_score(
                    page_class=page_class,
                    source_kind=source_kind,
                    candidate_origin="discovered_link",
                    url=href,
                    label=label,
                ),
                "priority_candidate": 0,
            }
        )
        if len(out) >= max_candidates:
            break
    return order_revize_candidates(out, max_candidates=max_candidates)


def discover_revize_department_candidates(
    html_text: str,
    base_url: str,
    max_candidates: int = REVIZE_MAX_DISCOVERED_PROFILE_PAGES,
) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    seen: set[str] = set()

    def add_candidate(href: str, label: str) -> None:
        nonlocal out
        if len(out) >= max_candidates:
            return
        normalized_href = normalize_url(href, base_url=base_url)
        if not normalized_href or normalized_href in seen:
            return
        lowered_blob = f"{normalized_href.lower()} {label.lower()}"
        role_keyword_hit = any(term in lowered_blob for term in REVIZE_ROLE_DISCOVERY_TERMS)
        department_like_url = "/departments/" in normalized_href.lower() or "/government/" in normalized_href.lower()
        if not role_keyword_hit and not department_like_url:
            return
        source_kind = _source_kind_from_link(normalized_href, label) or "department_page"
        page_class = classify_revize_page_class_for_url(
            url=normalized_href,
            source_kind=source_kind,
            label=label,
            html_text="",
        )
        if page_class == "generic":
            page_class = "department_page"
        seen.add(normalized_href)
        score = _revize_candidate_priority_score(
            page_class=page_class,
            source_kind=source_kind,
            candidate_origin="department_index_discovery",
            url=normalized_href,
            label=label,
        )
        if role_keyword_hit:
            score += 8
        out.append(
            {
                "url": normalized_href,
                "source_kind": source_kind,
                "candidate_origin": "department_index_discovery",
                "candidate_page_class": page_class,
                "candidate_route_order": _revize_page_class_route_order(page_class),
                "candidate_priority_score": score,
                "priority_candidate": 0,
            }
        )

    if BeautifulSoup is None:
        for anchor_match in re.finditer(
            r"(?is)<a[^>]*href\s*=\s*(?:\"([^\"]+)\"|'([^']+)'|([^\s>]+))[^>]*>(.*?)</a>",
            html_text or "",
        ):
            href_value = anchor_match.group(1) or anchor_match.group(2) or anchor_match.group(3) or ""
            label_value = normalize_whitespace(_strip_tags(anchor_match.group(4) or "")) or ""
            add_candidate(href_value, label_value)
            if len(out) >= max_candidates:
                break
        return order_revize_candidates(out, max_candidates=max_candidates)

    try:
        soup = BeautifulSoup(html_text or "", "html.parser")
    except Exception:
        return []
    for anchor in soup.find_all("a", href=True):
        add_candidate(str(anchor.get("href") or ""), normalize_whitespace(anchor.get_text(" ", strip=True)) or "")
        if len(out) >= max_candidates:
            break
    return order_revize_candidates(out, max_candidates=max_candidates)


def _extract_revize_contacts_with_diagnostics(
    html_text: str,
    source_url: str,
    source_kind: str,
    page_class: str = "generic",
    page_priority_score: float = 0.0,
) -> tuple[list[dict[str, str | float | None]], Counter[str], dict[str, int], dict[str, object]]:
    sanitized_html_text, soup, structural_counts = _sanitize_revize_html(html_text)
    page_context = _build_page_context(html_text=sanitized_html_text, source_url=source_url, soup=soup)
    department_like = _is_department_like_page(source_url, page_context)

    reconstruction_blocks = group_revize_contact_blocks(
        soup=soup,
        html_text=sanitized_html_text,
        source_url=source_url,
        page_context=page_context,
    )
    reconstructed_rows, reconstruction_metrics = extract_reconstructed_revize_candidates(
        blocks=reconstruction_blocks,
        source_url=source_url,
        page_context=page_context,
        page_class=page_class,
        page_priority_score=page_priority_score,
    )
    sidebar_rows = extract_revize_sidebar_staff(soup, source_url, page_context)
    table_rows = extract_revize_table_directory(sanitized_html_text, source_url)
    contact_card_rows = extract_revize_contact_cards(soup, source_url, page_context)
    inline_staff_rows = extract_revize_inline_staff_lists(soup, source_url, page_context)
    labeled_staff_rows = extract_revize_labeled_staff_blocks(soup, source_url, page_context)
    profile_rows = extract_revize_profile_blocks(sanitized_html_text, source_url)
    department_section_rows = extract_revize_department_sections(sanitized_html_text, source_url)
    single_profile_rows = extract_revize_single_profile_page(sanitized_html_text, source_url)
    department_contact_rows = extract_revize_department_contact_info(soup, source_url, page_context)

    extracted: list[dict[str, str | float | None]] = []
    if department_like:
        extracted.extend(reconstructed_rows)
        extracted.extend(sidebar_rows)
        extracted.extend(table_rows)
        extracted.extend(contact_card_rows)
        extracted.extend(inline_staff_rows)
        extracted.extend(labeled_staff_rows)
        extracted.extend(profile_rows)
        extracted.extend(department_section_rows)
        extracted.extend(single_profile_rows)
        extracted.extend(department_contact_rows)
    else:
        extracted.extend(reconstructed_rows)
        extracted.extend(table_rows)
        extracted.extend(contact_card_rows)
        extracted.extend(profile_rows)
        extracted.extend(inline_staff_rows)
        extracted.extend(labeled_staff_rows)
        extracted.extend(department_section_rows)
        extracted.extend(single_profile_rows)
        extracted.extend(sidebar_rows)
        extracted.extend(department_contact_rows)

    inferred_department = normalize_whitespace(str(page_context.get("department_inferred") or "")) or None
    if inferred_department:
        for row in extracted:
            if not normalize_whitespace(str(row.get("department") or "")):
                row["department"] = inferred_department
            row["revize_page_class"] = page_class
            row["revize_page_priority_score"] = page_priority_score

    if not extracted:
        classified = classify_revize_page(html_text=sanitized_html_text, url=source_url)
        fallback_source_type = str(classified.get("source_type") or "unknown")
        for row in extract_contacts(sanitized_html_text, source_url, page_type="directory_page"):
            candidate = dict(row)
            candidate["revize_source_type"] = fallback_source_type
            candidate["revize_page_class"] = page_class
            candidate["revize_page_priority_score"] = page_priority_score
            extracted.append(candidate)

    for row in extracted:
        if not row.get("revize_page_class"):
            row["revize_page_class"] = page_class
        if row.get("revize_page_priority_score") in (None, ""):
            row["revize_page_priority_score"] = page_priority_score

    reduction_counts: Counter[str] = Counter()
    rejected_rows_sample: list[dict[str, object]] = []
    extracted_before_filter = list(extracted)
    filtered_extracted: list[dict[str, str | float | None]] = []
    non_contact_blocks_filtered = 0
    for row in extracted_before_filter:
        block_type, is_non_contact = _classify_revize_non_contact_block(row)
        row["revize_block_type"] = block_type
        if not is_non_contact:
            filtered_extracted.append(row)
            continue
        non_contact_blocks_filtered += 1
        reduction_counts["revize_non_contact_blocks_filtered"] += 1
        if len(rejected_rows_sample) < 12:
            rejected_rows_sample.append(
                {
                    "row": _trace_row_payload(row),
                    "drop_reason": "non_contact_content",
                }
            )
    extracted = filtered_extracted

    block_classification_counts = {
        "person_block": 0,
        "office_contact_block": 0,
        "structural_block": 0,
    }
    for row in extracted:
        block_class = _classify_revize_row_block(row)
        block_classification_counts[block_class] = block_classification_counts.get(block_class, 0) + 1
        row["revize_block_class"] = block_class

    split_merge_from_reconstruction = _coerce_int(reconstruction_metrics.get("revize_split_text_merged"))
    if split_merge_from_reconstruction > 0:
        reduction_counts["revize_split_text_merged"] += split_merge_from_reconstruction
    deduped: dict[tuple[str, ...], dict[str, str | float | None]] = {}
    normalized_kept_count = 0
    extracted_rows_sample: list[dict[str, object]] = []
    normalized_rows_sample: list[dict[str, object]] = []
    for row in extracted:
        if len(extracted_rows_sample) < 12:
            extracted_rows_sample.append(_trace_row_payload(row))
        before_reduction = Counter(reduction_counts)
        normalized = _normalize_revize_contact_row(
            row=row,
            source_url=source_url,
            source_kind=source_kind,
            reduction_counts=reduction_counts,
        )
        if normalized is None:
            if len(rejected_rows_sample) < 12:
                rejected_rows_sample.append(
                    {
                        "row": _trace_row_payload(row),
                        "drop_reason": _pick_row_drop_reason(before_reduction, reduction_counts),
                    }
                )
            continue
        normalized_kept_count += 1
        if len(normalized_rows_sample) < 12:
            normalized_rows_sample.append(_trace_row_payload(normalized))
        key = _contact_dedupe_key(normalized)
        prior = deduped.get(key)
        deduped[key] = _merge_contacts(prior, normalized) if prior else normalized

    contacts = sorted(
        deduped.values(),
        key=lambda item: (
            -float(item.get("confidence") or 0.0),
            str(item.get("email") or ""),
            str(item.get("name") or ""),
        ),
    )
    source_counts = _count_extraction_sources(contacts)
    invalid_name_rejections = (
        _coerce_int(reduction_counts.get("soft_name_clear_reject"))
        + _coerce_int(reduction_counts.get("soft_name_generic_heading"))
        + _coerce_int(reduction_counts.get("soft_name_invalid_pattern"))
        + _coerce_int(reduction_counts.get("soft_name_address_like"))
        + _coerce_int(reduction_counts.get("soft_name_non_person_label"))
    )
    rows_kept_vs_dropped_ratio = round(
        float(normalized_kept_count) / float(len(extracted)) if extracted else 0.0,
        4,
    )
    over_filtering_detected = 1 if extracted and rows_kept_vs_dropped_ratio < 0.25 else 0
    if over_filtering_detected:
        reduction_counts["revize_over_filtering_detected"] += 1
    metrics = {
        "revize_blocks_seen": len(extracted_before_filter),
        "revize_blocks_filtered_non_contact": non_contact_blocks_filtered,
        "revize_blocks_emitted_as_candidates": len(extracted),
        "rows_extracted_total": len(extracted),
        "rows_normalized_seen": len(extracted),
        "rows_normalized_kept": normalized_kept_count,
        "rows_normalized_rejected": max(0, len(extracted) - normalized_kept_count),
        "rows_kept_vs_dropped_ratio": rows_kept_vs_dropped_ratio,
        "rows_flagged_as_noise": _coerce_int(reduction_counts.get("rows_flagged_as_noise")),
        "rows_soft_kept": _coerce_int(reduction_counts.get("rows_soft_kept")),
        "revize_over_filtering_detected": over_filtering_detected,
        "sidebar_staff_blocks_found": _count_sidebar_staff_blocks(sanitized_html_text),
        "sidebar_staff_contacts_extracted": _coerce_int(source_counts.get("sidebar_staff")),
        "department_contact_blocks_found": _count_department_contact_blocks(sanitized_html_text),
        "department_contact_rows_extracted": _coerce_int(source_counts.get("department_contact_block")),
        "revize_footer_blocks_ignored": _coerce_int(structural_counts.get("revize_footer_blocks_ignored")),
        "revize_hours_blocks_ignored": _coerce_int(structural_counts.get("revize_hours_blocks_ignored")),
        "revize_structural_blocks_dropped": _coerce_int(structural_counts.get("revize_structural_blocks_dropped")),
        "revize_person_blocks": _coerce_int(block_classification_counts.get("person_block")),
        "revize_office_contact_blocks": _coerce_int(block_classification_counts.get("office_contact_block")),
        "revize_person_rows_classified": _coerce_int(reduction_counts.get("revize_person_rows_classified")),
        "revize_office_contact_rows_classified": _coerce_int(
            reduction_counts.get("revize_office_contact_rows_classified")
        ),
        "revize_role_only_rows_demoted": _coerce_int(reduction_counts.get("revize_role_only_rows_demoted")),
        "revize_invalid_name_rejections": invalid_name_rejections,
        "revize_department_contamination_rejections": _coerce_int(
            reduction_counts.get("revize_department_contamination_rejections")
        ),
        "revize_split_text_merged": _coerce_int(reduction_counts.get("revize_split_text_merged")),
        "revize_phone_extensions_parsed": _coerce_int(reduction_counts.get("revize_phone_extensions_parsed")),
        "revize_phone_string_preserved": _coerce_int(reduction_counts.get("revize_phone_string_preserved")),
        "revize_reconstruction_pages_seen": 1,
        "revize_reconstruction_blocks_seen": _coerce_int(
            reconstruction_metrics.get("revize_reconstruction_blocks_seen")
        ),
        "revize_reconstruction_candidates_emitted": _coerce_int(
            reconstruction_metrics.get("revize_reconstruction_candidates_emitted")
        ),
        "revize_reconstruction_skipped_reason": dict(
            reconstruction_metrics.get("revize_reconstruction_skipped_reason") or {}
        ),
        "revize_rows_from_staff_directory": normalized_kept_count if page_class == "staff_directory" else 0,
        "revize_rows_from_department_pages": normalized_kept_count if page_class == "department_page" else 0,
        "revize_rows_from_contact_hubs": normalized_kept_count if page_class == "contact_hub" else 0,
        "revize_page_class": page_class,
        "revize_page_priority_score": page_priority_score,
        "extracted_rows_sample": extracted_rows_sample,
        "normalized_rows_sample": normalized_rows_sample,
        "rejected_rows_sample": rejected_rows_sample,
        "reconstructed_rows_sample": list(reconstruction_metrics.get("reconstructed_rows_sample") or []),
    }
    return contacts, reduction_counts, source_counts, metrics


def _fetch_revize_candidate(
    municipality_homepage: str,
    candidate: dict[str, object],
    timeout: int,
    session,
    request_headers: dict[str, str] | None,
    fetch_fn: FetchFn | None,
) -> dict[str, object] | None:
    request_url = str(candidate.get("url") or "")
    if not request_url:
        return None
    referer = normalize_url(ensure_url_has_scheme(municipality_homepage)) or municipality_homepage
    headers = {**REVIZE_REQUEST_HEADERS, **(request_headers or {})}

    if fetch_fn:
        result = fetch_fn(request_url, referer, headers)
        fetch_row = _coerce_fetch_result(result, request_url)
    else:
        fetch_row = _fetch_revize_http(
            url=request_url,
            timeout=timeout,
            session=session or create_session(),
            referer=referer,
            request_headers=headers,
        )

    text = str(fetch_row.get("text") or "")
    return {
        "request_url": request_url,
        "final_url": normalize_url(str(fetch_row.get("final_url") or request_url))
        or (str(fetch_row.get("final_url") or request_url)),
        "status_code": _coerce_int(fetch_row.get("status_code")) or None,
        "content_type": str(fetch_row.get("content_type") or ""),
        "response_headers": dict(fetch_row.get("response_headers") or {}),
        "error": str(fetch_row.get("error") or ""),
        "http_response_received": bool(fetch_row.get("http_response_received")),
        "has_body": bool(text.strip()),
        "source_kind": str(candidate.get("source_kind") or "unknown"),
        "candidate_origin": str(candidate.get("candidate_origin") or ""),
        "candidate_page_class": str(candidate.get("candidate_page_class") or ""),
        "candidate_priority_score": _coerce_int(candidate.get("candidate_priority_score")),
        "priority_candidate": _coerce_int(candidate.get("priority_candidate")),
        "text": text,
        "page_title": _extract_html_title(text),
    }


def _candidate_base_roots(base_url: str) -> list[str]:
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    out: list[str] = [origin]
    path = (parsed.path or "").strip()
    if path and path != "/":
        parent = path.rsplit("/", 1)[0] if "." in path.rsplit("/", 1)[-1] else path
        parent = parent.strip("/")
        if parent:
            out.append(f"{origin}/{parent}")
    return list(dict.fromkeys(out))


def _join_candidate_url(base_root: str, path: str) -> str:
    root = (base_root or "").rstrip("/")
    cleaned = path if path.startswith("/") else f"/{path}"
    return normalize_url(f"{root}{cleaned}") or f"{root}{cleaned}"


def _source_kind_from_path(path: str) -> str:
    lowered = (path or "").lower()
    if "directory_of_services" in lowered:
        return "directory_of_services"
    if "departments/index" in lowered:
        return "department_index_page"
    if "staff_directory.php" in lowered:
        return "staff_directory_php"
    if "staff_directory_.php" in lowered:
        return "staff_directory_underscore_php"
    if "staff-directory" in lowered:
        return "staff_directory_path"
    if "directory" in lowered:
        return "directory_path"
    if "contact_us" in lowered:
        return "contact_hub_path"
    if "contact" in lowered:
        return "contact_path"
    return "unknown"


def _source_kind_from_link(url: str, label: str) -> str:
    lowered = f"{url} {label}".lower()
    if "read more" in lowered or "profile" in lowered:
        return "single_profile_page"
    return _source_kind_from_path(lowered)


def _is_revize_department_index_url(url: str) -> bool:
    lowered = (url or "").lower()
    if not lowered:
        return False
    return (
        "/departments/index" in lowered
        or lowered.rstrip("/").endswith("/departments")
        or "/departments/" in lowered and lowered.endswith("/index.php")
    )


def _looks_revize_contact_like(url: str, label: str) -> bool:
    blob = f"{url} {label}".lower()
    return any(token in blob for token in REVIZE_HARVEST_TOKENS)


def _is_internal_url(url: str, base_domain: str) -> bool:
    domain = (get_domain(url) or "").lower()
    if not domain or not base_domain:
        return False
    return domain == base_domain or domain.endswith(f".{base_domain}")


def _extract_html_title(html_text: str) -> str:
    match = re.search(r"(?is)<title[^>]*>(.*?)</title>", html_text or "")
    return normalize_whitespace(match.group(1)) or "" if match else ""


def _extract_title_heading(html_text: str) -> str | None:
    if BeautifulSoup is None:
        return None
    try:
        soup = BeautifulSoup(html_text or "", "html.parser")
    except Exception:
        return None
    heading = soup.find(["h1", "h2"])
    if heading is None:
        return None
    return normalize_whitespace(heading.get_text(" ", strip=True))


def _extract_text_blob(html_text: str) -> str:
    raw = html_text or ""
    if not raw:
        return ""
    if BeautifulSoup is None:
        return normalize_whitespace(re.sub(r"(?s)<[^>]+>", " ", raw)) or ""
    try:
        soup = BeautifulSoup(raw, "html.parser")
    except Exception:
        return normalize_whitespace(re.sub(r"(?s)<[^>]+>", " ", raw)) or ""
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return normalize_whitespace(soup.get_text(" ", strip=True)) or ""


def normalize_revize_fragmented_text(value: str) -> tuple[str, int]:
    text = normalize_whitespace(value) or ""
    if not text:
        return "", 0
    merges = 0

    def apply(pattern: str, repl: str, flags: int = 0) -> None:
        nonlocal text, merges
        updated, count = re.subn(pattern, repl, text, flags=flags)
        if count > 0:
            merges += count
            text = normalize_whitespace(updated) or ""

    # Rejoin common split-digit fragments from adjacent DOM text nodes.
    apply(
        r"\b(\d{2})\s+(\d[\s.\-]?\d{3}[\s.\-]?\d{4}(?:\s*(?:x|ext\.?|extension)\s*\d{1,6})?)\b",
        r"\1\2",
        flags=re.IGNORECASE,
    )
    apply(r"(?<=\d)\s+(?=(?:x|ext\.?|extension)\s*\d{1,6}\b)", "", flags=re.IGNORECASE)
    apply(r"\b(\d{3})\s+(\d{3})\s+(\d{4})(\b|$)", r"\1-\2-\3")
    apply(r"\b(\d{3}[-.\s]?\d{3}[-.\s]?\d{4})(?:\s+\1\b)+", r"\1")
    return text, merges


def parse_revize_phone_and_ext(
    phone_value: object,
    fallback_text: str = "",
    phone_ext_value: object | None = None,
) -> tuple[str, str, int, int]:
    parts = [str(phone_value or "").strip()]
    fallback_clean = normalize_whitespace(str(fallback_text or "")) or ""
    if fallback_clean:
        parts.append(fallback_clean)
    ext_hint = str(phone_ext_value or "").strip()
    if ext_hint:
        parts.append(f"x{ext_hint}")
    merged_blob, merge_count = normalize_revize_fragmented_text(" | ".join(part for part in parts if part))
    if not merged_blob:
        return "", "", merge_count, 0

    phone_re = re.compile(
        r"""
        (?P<full>
            (?:\+?1[\s.\-]?)?
            \(?(?P<area>[2-9][0-9]{2})\)?[\s.\-]?
            (?P<prefix>[0-9]{3})[\s.\-]?
            (?P<line>[0-9]{4})
            (?:
                \s*(?:,|;)?\s*
                (?:ext\.?|extension|x)
                \s*(?P<ext>[0-9]{1,6})
            )?
        )
        """,
        re.IGNORECASE | re.VERBOSE,
    )
    for match in phone_re.finditer(merged_blob):
        prefix_context = merged_blob[max(0, match.start() - 12):match.start()].lower()
        if "fax" in prefix_context:
            continue
        phone_digits = f"{match.group('area')}{match.group('prefix')}{match.group('line')}"
        ext_digits = _normalize_phone_ext_string(match.group("ext") or ext_hint)
        ext_count = 1 if ext_digits else 0
        return phone_digits, ext_digits, merge_count, ext_count

    phone_candidates = extract_phone_candidates(merged_blob)
    for candidate in phone_candidates:
        source_context = normalize_whitespace(str(candidate.get("source_context") or "")) or ""
        if source_context.lower().startswith("fax"):
            continue
        phone_digits = str(candidate.get("phone") or "").strip()
        ext_digits = _normalize_phone_ext_string(candidate.get("phone_ext") or ext_hint)
        ext_count = 1 if ext_digits else 0
        return phone_digits, ext_digits, merge_count, ext_count
    return "", _normalize_phone_ext_string(ext_hint), merge_count, 1 if ext_hint else 0


def _sanitize_revize_html(html_text: str) -> tuple[str, object | None, dict[str, int]]:
    structural_counts = {
        "revize_footer_blocks_ignored": 0,
        "revize_hours_blocks_ignored": 0,
        "revize_structural_blocks_dropped": 0,
    }
    if BeautifulSoup is None:
        sanitized, regex_counts = _sanitize_revize_html_regex(html_text or "")
        structural_counts.update(regex_counts)
        return sanitized, None, structural_counts
    try:
        soup = BeautifulSoup(html_text or "", "html.parser")
    except Exception:
        sanitized, regex_counts = _sanitize_revize_html_regex(html_text or "")
        structural_counts.update(regex_counts)
        return sanitized, None, structural_counts

    removed_nodes: set[int] = set()

    def drop(node, category: str) -> None:
        if node is None:
            return
        node_id = id(node)
        if node_id in removed_nodes:
            return
        removed_nodes.add(node_id)
        if category == "footer":
            structural_counts["revize_footer_blocks_ignored"] += 1
        elif category == "hours":
            structural_counts["revize_hours_blocks_ignored"] += 1
        structural_counts["revize_structural_blocks_dropped"] += 1
        node.decompose()

    for footer in list(soup.find_all("footer")):
        drop(footer, "footer")

    for selector in REVIZE_EXCLUDED_SELECTOR_PATTERNS:
        for node in list(soup.select(selector)):
            if selector == "#hours-wrap":
                drop(node, "hours")
            elif selector == "footer":
                drop(node, "footer")
            else:
                drop(node, "structural")

    for script in list(soup.find_all("script")):
        script_text = normalize_whitespace(script.get_text(" ", strip=True)) or ""
        if "rz.module" in script_text.lower():
            drop(script, "structural")

    for node in list(soup.find_all(["section", "div", "aside", "nav", "ul", "ol", "article"])):
        text = normalize_whitespace(node.get_text(" ", strip=True)) or ""
        lowered = text.lower()
        if not lowered:
            continue
        if re.match(r"^(?:office hours)\b", lowered):
            drop(node, "hours")
            continue
        if re.match(r"^(?:resources|related links)\b", lowered):
            drop(node, "structural")

    return str(soup), soup, structural_counts


def _sanitize_revize_html_regex(html_text: str) -> tuple[str, dict[str, int]]:
    structural_counts = {
        "revize_footer_blocks_ignored": 0,
        "revize_hours_blocks_ignored": 0,
        "revize_structural_blocks_dropped": 0,
    }
    sanitized = html_text or ""
    if not sanitized:
        return sanitized, structural_counts

    def drop_pattern(pattern: str, category: str = "structural") -> None:
        nonlocal sanitized
        matches = list(re.finditer(pattern, sanitized))
        if not matches:
            return
        count = len(matches)
        if category == "footer":
            structural_counts["revize_footer_blocks_ignored"] += count
        elif category == "hours":
            structural_counts["revize_hours_blocks_ignored"] += count
        structural_counts["revize_structural_blocks_dropped"] += count
        sanitized = re.sub(pattern, " ", sanitized)

    drop_pattern(r"(?is)<footer\b[^>]*>.*?</footer>", category="footer")
    drop_pattern(
        r"(?is)<(?P<tag>[a-z0-9]+)\b(?=[^>]*\bid\s*=\s*(?:\"hours-wrap\"|'hours-wrap'))[^>]*>.*?</(?P=tag)>",
        category="hours",
    )
    for class_token in ("footer-links-box", "rz-btns-container", "resource-link"):
        drop_pattern(
            rf"(?is)<(?P<tag>[a-z0-9]+)\b(?=[^>]*\bclass\s*=\s*(?:\"[^\"]*\b{re.escape(class_token)}\b[^\"]*\"|'[^']*\b{re.escape(class_token)}\b[^']*'))[^>]*>.*?</(?P=tag)>",
            category="structural",
        )
    for token in REVIZE_STRUCTURAL_TEXT_REJECTS:
        if token == "office hours":
            category = "hours"
        else:
            category = "structural"
        drop_pattern(
            rf"(?is)<(?P<tag>section|div|aside|nav|ul|ol|article)\b[^>]*>.*?\b{re.escape(token)}\b.*?</(?P=tag)>",
            category=category,
        )
    drop_pattern(r"(?is)<script[^>]*>[^<]*RZ\.module.*?</script>", category="structural")
    return sanitized, structural_counts


def _classify_revize_row_block(row: dict[str, str | float | None]) -> str:
    source_type = str(row.get("revize_source_type") or "").strip().lower()
    name = normalize_whitespace(str(row.get("name") or "")) or ""
    title = normalize_whitespace(str(row.get("title") or "")) or ""
    email = str(row.get("email") or "").strip()
    phone = str(row.get("phone") or "").strip()

    if source_type == "department_contact_block":
        return "office_contact_block"
    if name and _looks_like_person_name(name) and (title or email or phone):
        return "person_block"
    if email or phone:
        return "office_contact_block"
    return "structural_block"


def _is_structural_or_excluded_node(node) -> bool:
    if node is None:
        return True
    if node.find_parent("footer") is not None:
        return True
    class_blob = " ".join(str(token) for token in (node.get("class") or [])).lower()
    node_id = str(node.get("id") or "").lower()
    if any(token in class_blob for token in ("footer-links-box", "rz-btns-container", "resource-link")):
        return True
    if node_id == "hours-wrap":
        return True
    text = normalize_whitespace(node.get_text(" ", strip=True)) or ""
    lowered = text.lower()
    if re.match(r"^(?:office hours|resources|related links)\b", lowered):
        return True
    return False


def _build_page_context(
    html_text: str,
    source_url: str,
    soup=None,
) -> dict[str, object]:
    context: dict[str, object] = {"html_text": html_text or "", "source_url": source_url}
    context["page_title"] = _extract_html_title(html_text or "")
    context["url_department"] = _infer_department_from_source_url(source_url)
    context["breadcrumbs"] = []
    context["left_nav_labels"] = []
    context["section_headings"] = []
    context["h1"] = None

    if soup is not None:
        h1 = soup.find("h1")
        if h1 is not None:
            context["h1"] = normalize_whitespace(h1.get_text(" ", strip=True))

        breadcrumb_tokens: list[str] = []
        for node in soup.find_all(
            ["nav", "ul", "ol", "div"],
            attrs={"class": re.compile(r"(?i)breadcrumb"), "id": re.compile(r"(?i)breadcrumb")},
        ):
            text = normalize_whitespace(node.get_text(" ", strip=True)) or ""
            if not text:
                continue
            parts = [normalize_whitespace(item) or "" for item in re.split(r"\s*[>/|»]+\s*", text)]
            for part in parts:
                if part and part not in breadcrumb_tokens:
                    breadcrumb_tokens.append(part)
        context["breadcrumbs"] = breadcrumb_tokens

        left_nav_labels: list[str] = []
        for node in soup.find_all(["aside", "nav", "div", "ul"], attrs={"id": re.compile(r"(?i)left|sidebar")}):
            for anchor in node.find_all("a"):
                label = normalize_whitespace(anchor.get_text(" ", strip=True)) or ""
                if not label:
                    continue
                if label.lower() in {"home", "government", "departments"}:
                    continue
                if label not in left_nav_labels:
                    left_nav_labels.append(label)
                if len(left_nav_labels) >= 12:
                    break
        context["left_nav_labels"] = left_nav_labels

        section_headings: list[str] = []
        for heading in soup.find_all(["h2", "h3", "h4"]):
            label = normalize_whitespace(heading.get_text(" ", strip=True)) or ""
            if not label:
                continue
            lowered = label.lower()
            if any(token in lowered for token in REVIZE_STRUCTURAL_TEXT_REJECTS):
                continue
            if lowered in REVIZE_ACTION_TEXT_REJECTS:
                continue
            if not _looks_like_department(label):
                continue
            if label not in section_headings:
                section_headings.append(label)
            if len(section_headings) >= 12:
                break
        context["section_headings"] = section_headings
    else:
        h1_match = re.search(r"(?is)<h1[^>]*>(.*?)</h1>", html_text or "")
        if h1_match:
            heading_text = normalize_whitespace(html_unescape(re.sub(r"(?is)<[^>]+>", " ", h1_match.group(1)))) or ""
            if heading_text:
                context["h1"] = heading_text

        breadcrumb_tokens: list[str] = []
        for crumb_match in re.finditer(
            r"(?is)<(?:nav|ul|ol|div)[^>]*(?:class|id)\s*=\s*(?:\"[^\"]*breadcrumb[^\"]*\"|'[^']*breadcrumb[^']*')[^>]*>(.*?)</(?:nav|ul|ol|div)>",
            html_text or "",
        ):
            crumb_text = normalize_whitespace(re.sub(r"(?is)<[^>]+>", " ", str(crumb_match.group(1) or ""))) or ""
            crumb_text = normalize_whitespace(html_unescape(crumb_text)) or ""
            if not crumb_text:
                continue
            parts = [normalize_whitespace(item) or "" for item in re.split(r"\s*[>/|Â»]+\s*", crumb_text)]
            for part in parts:
                if part and part not in breadcrumb_tokens:
                    breadcrumb_tokens.append(part)
        context["breadcrumbs"] = breadcrumb_tokens

        section_headings: list[str] = []
        for heading_match in re.finditer(r"(?is)<h[2-4][^>]*>(.*?)</h[2-4]>", html_text or ""):
            label = normalize_whitespace(re.sub(r"(?is)<[^>]+>", " ", str(heading_match.group(1) or ""))) or ""
            label = normalize_whitespace(html_unescape(label)) or ""
            if not label:
                continue
            lowered = label.lower()
            if any(token in lowered for token in REVIZE_STRUCTURAL_TEXT_REJECTS):
                continue
            if lowered in REVIZE_ACTION_TEXT_REJECTS:
                continue
            if not _looks_like_department(label):
                continue
            if label not in section_headings:
                section_headings.append(label)
            if len(section_headings) >= 12:
                break
        context["section_headings"] = section_headings

    context["department_inferred"] = _infer_department_from_page_context(context, source_url)
    return context


def _infer_department_from_page_context(
    page_context: dict[str, object] | None,
    source_url: str,
) -> str | None:
    context = dict(page_context or {})
    candidates: list[str] = []
    # Prefer breadcrumb context first for Revize department pages.
    for value in _department_candidates_from_breadcrumbs(context.get("breadcrumbs") or []):
        cleaned = normalize_whitespace(str(value) or "") or ""
        if cleaned:
            candidates.append(cleaned)
    for key in ("h1", "page_title"):
        value = normalize_whitespace(str(context.get(key) or "")) or ""
        if value:
            candidates.append(value)
    for value in context.get("section_headings") or []:
        cleaned = normalize_whitespace(str(value) or "") or ""
        if cleaned:
            candidates.append(cleaned)
    for key in ("url_department",):
        value = normalize_whitespace(str(context.get(key) or "")) or ""
        if value:
            candidates.append(value)
    for value in context.get("left_nav_labels") or []:
        cleaned = normalize_whitespace(str(value) or "") or ""
        if cleaned:
            candidates.append(cleaned)
    candidates.append(_infer_department_from_source_url(source_url) or "")

    for candidate in candidates:
        department = _to_department_label(candidate)
        if department:
            return department
    return None


def _department_candidates_from_breadcrumbs(breadcrumbs: Iterable[object]) -> list[str]:
    tokens = [normalize_whitespace(str(token) or "") or "" for token in breadcrumbs]
    tokens = [token for token in tokens if token]
    if not tokens:
        return []
    lowered = [token.lower() for token in tokens]
    out: list[str] = []
    if "departments" in lowered:
        idx = lowered.index("departments")
        for token in tokens[idx + 1:]:
            lowered_token = token.lower()
            if lowered_token in {"home", "government", "departments"}:
                continue
            out.append(token)
    tail = tokens[-1]
    if tail.lower() not in {"home", "government", "departments"}:
        out.append(tail)
    deduped: list[str] = []
    seen: set[str] = set()
    for token in out:
        key = _normalize_token(token)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(token)
    return deduped


def _infer_department_from_source_url(source_url: str) -> str | None:
    parsed = urlparse(source_url or "")
    segments = [segment for segment in (parsed.path or "").split("/") if segment]
    if not segments:
        return None
    lowered_segments = [segment.lower() for segment in segments]
    if "departments" in lowered_segments:
        idx = lowered_segments.index("departments")
        if idx + 1 < len(segments):
            candidate = segments[idx + 1]
            if candidate.lower() not in {"staff_directory.php", "staff_directory_.php", "staff-directory", "directory", "index.php"}:
                return _to_department_label(candidate)
    for segment in reversed(segments):
        lowered = segment.lower()
        if lowered in {"index.php", "staff_directory.php", "staff_directory_.php", "staff-directory", "directory"}:
            continue
        if "department" in lowered or any(token in lowered for token in ("building", "assessor", "clerk", "finance", "planning", "zoning", "police", "fire")):
            return _to_department_label(segment)
    return None


def _to_department_label(value: str) -> str | None:
    candidate = normalize_whitespace(value) or ""
    if not candidate:
        return None
    if _looks_like_address_or_location(candidate):
        return None
    lowered = candidate.lower()
    lowered = re.sub(r"(?i)^town of\s+[^|:\-]+[|:\-]\s*", "", lowered).strip(" -|")
    lowered = re.sub(r"(?i)^city of\s+[^|:\-]+[|:\-]\s*", "", lowered).strip(" -|")
    lowered = lowered.replace("_", " ").replace("-", " ")
    lowered = re.sub(r"\s+", " ", lowered).strip()
    if not lowered:
        return None
    lowered = re.sub(r"(?i)\bdepartment(s)?\b", "", lowered).strip()
    lowered = re.sub(r"(?i)\boffice\b", "", lowered).strip()
    lowered = re.sub(r"(?i)\bcontact info\b", "", lowered).strip()
    lowered = re.sub(r"(?i)\bbreadcrumbs?\b", "", lowered).strip()
    if not lowered:
        return None
    if lowered in REVIZE_GENERIC_HEADING_REJECTS:
        return None
    if lowered in {"government", "departments", "home", "staff", "directory"}:
        return None
    if lowered in REVIZE_DEPARTMENT_LITERAL_REJECTS:
        return None
    if any(phrase in lowered for phrase in ("bids", "rfp", "request for proposal")):
        return None
    if any(token in lowered for token in ("main street", ", ct", " connecticut", " jewett city", "town hall")):
        return None
    return " ".join(part.capitalize() for part in lowered.split())


def _is_department_like_page(source_url: str, page_context: dict[str, object]) -> bool:
    lowered_url = (source_url or "").lower()
    if "/departments/" in lowered_url:
        return True
    if "department_inferred" in page_context and page_context.get("department_inferred"):
        return True
    breadcrumbs = " ".join(str(item) for item in (page_context.get("breadcrumbs") or [])).lower()
    if "departments" in breadcrumbs:
        return True
    return False


def _count_sidebar_staff_blocks(html_text: str) -> int:
    if BeautifulSoup is None:
        return _count_sidebar_staff_blocks_regex(html_text)
    try:
        soup = BeautifulSoup(html_text or "", "html.parser")
    except Exception:
        return 0
    sidebar = soup.select_one("aside#staff-dr") or soup.find("aside", id=re.compile(r"(?i)staff-?dr"))
    if sidebar is None:
        return 0
    return len(sidebar.select("div.staff"))


def _count_department_contact_blocks(html_text: str) -> int:
    if BeautifulSoup is None:
        return _count_department_contact_blocks_regex(html_text)
    try:
        soup = BeautifulSoup(html_text or "", "html.parser")
    except Exception:
        return 0
    hits = 0
    for heading in soup.find_all(["h2", "h3", "h4", "strong"]):
        heading_text = normalize_whitespace(heading.get_text(" ", strip=True)) or ""
        if heading_text and "contact" in heading_text.lower():
            hits += 1
    return hits


def _count_contact_card_hits(html_text: str) -> int:
    if BeautifulSoup is None:
        return len(re.findall(r"(?is)class\s*=\s*(?:\"[^\"]*contact-name[^\"]*\"|'[^']*contact-name[^']*')", html_text or ""))
    try:
        soup = BeautifulSoup(html_text or "", "html.parser")
    except Exception:
        return 0
    return len(soup.select(".contact-name"))


def _count_inline_staff_hits(html_text: str) -> int:
    blob = normalize_whitespace(_extract_text_blob(html_text)) or ""
    if not blob:
        return 0
    has_mailto = bool(re.search(r"(?i)mailto:", html_text or ""))
    if not has_mailto:
        return 0
    return 1 if re.search(r"\b[A-Z][a-zA-Z'`.-]+(?:\s+[A-Z][a-zA-Z'`.-]+){1,2}\s*,\s*[A-Za-z]", blob) else 0


def _count_labeled_staff_hits(html_text: str) -> int:
    blob = normalize_whitespace(_extract_text_blob(html_text)) or ""
    if not blob:
        return 0
    return 1 if re.search(r"(?i)\b(?:manager|clerk|director|administrator|assessor|collector|chief)\s*:\s*[A-Z]", blob) else 0


def _count_table_header_hits(html_text: str) -> int:
    if BeautifulSoup is None:
        return _count_table_header_hits_regex(html_text)
    try:
        soup = BeautifulSoup(html_text or "", "html.parser")
    except Exception:
        return 0
    hits = 0
    for table in soup.find_all("table"):
        headers = _extract_table_headers(table)
        hits = max(hits, _table_header_hits(headers))
    return hits


def _table_header_hits(headers: list[str]) -> int:
    lowered = {_normalize_token(header) for header in headers}
    hits = 0
    for token in REVIZE_TABLE_HEADER_HINTS:
        if any(token in header for header in lowered):
            hits += 1
    return hits


def _count_profile_block_hits(html_text: str) -> int:
    if BeautifulSoup is None:
        return _count_profile_block_hits_regex(html_text)
    try:
        soup = BeautifulSoup(html_text or "", "html.parser")
    except Exception:
        return 0
    blocks = _discover_profile_blocks(soup)
    return len(blocks)


def _count_department_sections(html_text: str) -> int:
    if BeautifulSoup is None:
        return _count_department_sections_regex(html_text)
    try:
        soup = BeautifulSoup(html_text or "", "html.parser")
    except Exception:
        return 0
    hits = 0
    for heading in soup.find_all(["h1", "h2", "h3", "h4", "strong"]):
        text = normalize_whitespace(heading.get_text(" ", strip=True)) or ""
        if _looks_like_department(text):
            hits += 1
    return hits


def _count_key_value_hits(lowered_blob: str) -> int:
    if not lowered_blob:
        return 0
    hits = 0
    for key in ("name", "title", "department", "phone", "email", "location", "profession"):
        if re.search(rf"\b{re.escape(key)}\s*[:\-]", lowered_blob):
            hits += 1
    return hits


def _count_table_header_hits_regex(html_text: str) -> int:
    hits = 0
    for table_html in re.findall(r"(?is)<table[^>]*>(.*?)</table>", html_text or ""):
        headers = _extract_table_headers_regex(table_html)
        hits = max(hits, _table_header_hits(headers))
    return hits


def _count_profile_block_hits_regex(html_text: str) -> int:
    raw_html = html_text or ""
    blob = _strip_tags(raw_html)
    if not blob:
        return 0
    href_emails = [
        match.group(1).strip().lower()
        for match in re.finditer(
            r"(?i)mailto\s*:\s*([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})",
            raw_html,
        )
    ]
    email_count = len({*extract_emails(blob), *href_emails})
    phone_count = len(extract_phone_candidates(blob))
    read_more_count = blob.lower().count("read more")
    return max(0, min(email_count, phone_count, max(read_more_count, 1)))


def _count_department_sections_regex(html_text: str) -> int:
    hits = 0
    for match in re.findall(r"(?is)<(?:h1|h2|h3|h4|strong)[^>]*>(.*?)</(?:h1|h2|h3|h4|strong)>", html_text or ""):
        heading = normalize_whitespace(_strip_tags(match)) or ""
        if _looks_like_department(heading):
            hits += 1
    return hits


def _count_sidebar_staff_blocks_regex(html_text: str) -> int:
    aside_match = re.search(
        r'(?is)<aside[^>]*id\s*=\s*(?:"staff-dr"|\'staff-dr\'|staff-dr)[^>]*>(.*?)</aside>',
        html_text or "",
    )
    if not aside_match:
        return 0
    aside_html = aside_match.group(1) or ""
    return len(re.findall(r'(?is)<div[^>]*class\s*=\s*(?:"[^"]*\bstaff\b[^"]*"|\'[^\']*\bstaff\b[^\']*\')[^>]*>', aside_html))


def _count_department_contact_blocks_regex(html_text: str) -> int:
    hits = 0
    for heading in re.findall(r"(?is)<(?:h2|h3|h4|strong)[^>]*>(.*?)</(?:h2|h3|h4|strong)>", html_text or ""):
        text = normalize_whitespace(_strip_tags(heading)) or ""
        if text and "contact" in text.lower():
            hits += 1
    return hits


def _extract_name_title_from_heading(heading) -> tuple[str | None, str | None]:
    if heading is None:
        return None, None
    span = heading.find("span")
    title = normalize_whitespace(span.get_text(" ", strip=True)) if span is not None else None
    if title:
        title, _ = normalize_revize_fragmented_text(title)
    name_parts: list[str] = []
    for child in heading.contents:
        if getattr(child, "name", None) == "span":
            continue
        if hasattr(child, "get_text"):
            text = normalize_whitespace(child.get_text(" ", strip=True))
        else:
            text = normalize_whitespace(str(child))
        if text:
            name_parts.append(text)
    name = normalize_whitespace(" ".join(name_parts))
    if name:
        name, _ = normalize_revize_fragmented_text(name)
    return name or None, title or None


def _extract_phone_from_tel_links(node) -> tuple[str | None, str | None]:
    for anchor in node.find_all("a", href=True):
        href = str(anchor.get("href") or "")
        if not href.lower().startswith("tel:"):
            continue
        phone_text = href.split(":", 1)[1]
        phone_candidates = extract_phone_candidates(phone_text)
        if phone_candidates:
            return (
                str(phone_candidates[0].get("phone") or "") or None,
                str(phone_candidates[0].get("phone_ext") or "") or None,
            )
        digits = re.sub(r"[^0-9]", "", phone_text)
        if len(digits) >= 10:
            return digits, None
    return None, None


def _extract_email_from_mailto_links(node) -> str | None:
    for anchor in node.find_all("a", href=True):
        href = str(anchor.get("href") or "")
        if not href.lower().startswith("mailto:"):
            continue
        emails = extract_emails_from_href(href)
        if emails:
            return emails[0].lower()
    return None


def _extract_contact_info_from_node(
    node,
    fallback_phone: str | None = None,
    fallback_phone_ext: str | None = None,
    fallback_email: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    phone, phone_ext = _extract_phone_from_tel_links(node)
    email = _extract_email_from_mailto_links(node)
    blob = normalize_whitespace(node.get_text(" ", strip=True)) or ""
    blob, _ = normalize_revize_fragmented_text(blob)
    if not email:
        emails = extract_emails(blob)
        if emails:
            email = emails[0].lower()
    parsed_phone, parsed_ext, _, _ = parse_revize_phone_and_ext(
        phone_value=phone or "",
        fallback_text=blob,
        phone_ext_value=phone_ext or fallback_phone_ext,
    )
    if parsed_phone:
        phone = parsed_phone
    if parsed_ext:
        phone_ext = parsed_ext
    return (
        phone or fallback_phone,
        phone_ext or fallback_phone_ext,
        email or fallback_email,
    )


def _extract_revize_sidebar_staff_regex(
    html_text: str,
    page_url: str,
    department_hint: str | None,
) -> list[dict[str, str | float | None]]:
    aside_match = re.search(
        r'(?is)<aside[^>]*id\s*=\s*(?:"staff-dr"|\'staff-dr\'|staff-dr)[^>]*>(.*?)</aside>',
        html_text or "",
    )
    if not aside_match:
        return []
    aside_html = aside_match.group(1) or ""
    out: list[dict[str, str | float | None]] = []
    heading_matches = list(re.finditer(r"(?is)<h4[^>]*>(.*?)</h4>", aside_html))
    for idx, heading_match in enumerate(heading_matches):
        raw_heading = heading_match.group(1) if heading_match else ""
        title_match = re.search(r"(?is)<span[^>]*>(.*?)</span>", raw_heading)
        title = normalize_whitespace(_strip_tags(title_match.group(1))) if title_match else None
        name = normalize_whitespace(_strip_tags(re.sub(r"(?is)<span[^>]*>.*?</span>", " ", raw_heading)))
        start = heading_match.start()
        end = heading_matches[idx + 1].start() if idx + 1 < len(heading_matches) else len(aside_html)
        block_html = aside_html[start:end]

        tel_match = re.search(
            r"""(?is)\bhref\s*=\s*(?:"(tel:[^"]+)"|'(tel:[^']+)')""",
            block_html,
        )
        mailto_match = re.search(
            r"""(?is)\bhref\s*=\s*(?:"(mailto:[^"]+)"|'(mailto:[^']+)')""",
            block_html,
        )
        phone = None
        phone_ext = None
        if tel_match:
            tel_href = tel_match.group(1) or tel_match.group(2) or ""
            phone_candidates = extract_phone_candidates(tel_href.split(":", 1)[1] if ":" in tel_href else tel_href)
            if phone_candidates:
                phone = str(phone_candidates[0].get("phone") or "") or None
                phone_ext = str(phone_candidates[0].get("phone_ext") or "") or None
        email = None
        if mailto_match:
            mailto_href = mailto_match.group(1) or mailto_match.group(2) or ""
            emails = extract_emails_from_href(mailto_href)
            if emails:
                email = emails[0].lower()

        if not name and (phone or email):
            source_context = normalize_whitespace(_strip_tags(block_html)) or ""
            source_context, _ = normalize_revize_fragmented_text(source_context)
            out.append(
                {
                    "name": None,
                    "title": "Department Contact",
                    "department": department_hint,
                    "email": email,
                    "email_type": infer_email_type(email),
                    "phone": phone,
                    "phone_ext": phone_ext,
                    "address": None,
                    "hours": None,
                    "source_context": source_context,
                    "source_url": page_url,
                    "confidence": 0.7,
                    "revize_source_type": "department_contact_block",
                }
            )
            continue

        out.append(
            {
                "name": name or None,
                "title": title or None,
                "department": department_hint,
                "email": email,
                "email_type": infer_email_type(email),
                "phone": phone,
                "phone_ext": phone_ext,
                "address": None,
                "hours": None,
                "source_context": (normalize_revize_fragmented_text(normalize_whitespace(_strip_tags(block_html)) or "")[0]),
                "source_url": page_url,
                "confidence": 0.9 if (email or phone) else 0.75,
                "revize_source_type": "sidebar_staff",
            }
        )
    return out


def _extract_revize_department_contact_info_regex(
    html_text: str,
    page_url: str,
    department_hint: str | None,
) -> list[dict[str, str | float | None]]:
    out: list[dict[str, str | float | None]] = []
    blocks: list[str] = []
    for block in re.findall(r"(?is)<(div|section|article)[^>]*>(.*?)</\1>", html_text or ""):
        block_html = block[1] or ""
        block_text = normalize_whitespace(_strip_tags(block_html)) or ""
        if not block_text:
            continue
        if "contact info" in block_text.lower() or ("contact" in block_text.lower() and ("phone" in block_text.lower() or "email" in block_text.lower())):
            blocks.append(block_html)

    for block_html in blocks:
        block_text = normalize_whitespace(_strip_tags(block_html)) or ""
        block_text, _ = normalize_revize_fragmented_text(block_text)
        emails = set(extract_emails(block_text))
        phones = extract_phone_candidates(block_text)
        for href in re.findall(
            r"""(?is)\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""",
            block_html,
        ):
            href_value = href[0] or href[1] or href[2]
            emails.update(extract_emails_from_href(href_value))
            if href_value.lower().startswith("tel:"):
                phone_candidates = extract_phone_candidates(href_value.split(":", 1)[1])
                phones = [*phone_candidates, *phones]
        if not emails and not phones:
            continue
        lines = _clean_lines(block_text.splitlines() if "\n" in block_text else [block_text])
        address = _extract_address_like_line(lines)
        hours = _extract_hours_like_line(lines)
        phone = str(phones[0].get("phone") or "") if phones else ""
        phone_ext = str(phones[0].get("phone_ext") or "") if phones else ""
        email = sorted(emails)[0].lower() if emails else ""
        out.append(
            {
                "name": None,
                "title": "Department Contact",
                "department": department_hint,
                "email": email or None,
                "email_type": infer_email_type(email),
                "phone": phone or None,
                "phone_ext": phone_ext or None,
                "address": address,
                "hours": hours,
                "source_context": block_text[:240],
                "source_url": page_url,
                "confidence": 0.72,
                "revize_source_type": "department_contact_block",
            }
        )
    return out


def _extract_href_values_from_html(fragment: str) -> list[str]:
    out: list[str] = []
    for href in re.findall(
        r"""(?is)\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""",
        fragment or "",
    ):
        value = href[0] or href[1] or href[2]
        value = normalize_whitespace(value) or ""
        if value:
            out.append(value)
    return out


def _extract_contact_channels_from_html_fragment(
    fragment: str,
    fallback_phone: str | None = None,
    fallback_phone_ext: str | None = None,
    fallback_email: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    text = normalize_whitespace(_strip_tags(fragment)) or ""
    text, _ = normalize_revize_fragmented_text(text)
    emails = set(extract_emails(text))
    phones = extract_phone_candidates(text)
    for href in _extract_href_values_from_html(fragment):
        emails.update(extract_emails_from_href(href))
        if href.lower().startswith("tel:"):
            tel_value = href.split(":", 1)[1] if ":" in href else href
            phone_candidates = extract_phone_candidates(tel_value)
            if phone_candidates:
                phones = [*phone_candidates, *phones]
    phone = str(phones[0].get("phone") or "") if phones else ""
    phone_ext = str(phones[0].get("phone_ext") or "") if phones else ""
    email = sorted(emails)[0].lower() if emails else ""
    return (
        phone or fallback_phone,
        phone_ext or fallback_phone_ext,
        email or fallback_email,
    )


def _extract_revize_contact_cards_regex(
    html_text: str,
    page_url: str,
    department_hint: str | None,
) -> list[dict[str, str | float | None]]:
    out: list[dict[str, str | float | None]] = []
    if not html_text:
        return out

    fallback_contact_html = ""
    fallback_match = re.search(
        r"(?is)<(?P<tag>[a-z0-9]+)\b(?=[^>]*\bid\s*=\s*(?:\"contact-info\"|'contact-info'))[^>]*>(?P<body>.*?)</(?P=tag)>",
        html_text,
    )
    if fallback_match:
        fallback_contact_html = str(fallback_match.group("body") or "")
    fallback_phone, fallback_phone_ext, fallback_email = _extract_contact_channels_from_html_fragment(
        fallback_contact_html
    )

    name_pattern = re.compile(
        r"(?is)<(?P<tag>[a-z0-9]+)\b(?=[^>]*\bclass\s*=\s*(?:\"[^\"]*\bcontact-name\b[^\"]*\"|'[^']*\bcontact-name\b[^']*'))[^>]*>(?P<name>.*?)</(?P=tag)>"
    )
    for match in name_pattern.finditer(html_text):
        name = normalize_whitespace(_strip_tags(str(match.group("name") or ""))) or None
        if not name:
            continue
        start = max(0, match.start() - 500)
        end = min(len(html_text), match.end() + 900)
        segment = html_text[start:end]
        title = None
        title_match = re.search(
            r"(?is)<(?P<tag>[a-z0-9]+)\b(?=[^>]*\bclass\s*=\s*(?:\"[^\"]*\bcontact-position\b[^\"]*\"|'[^']*\bcontact-position\b[^']*'))[^>]*>(?P<title>.*?)</(?P=tag)>",
            segment,
        )
        if title_match:
            title = normalize_whitespace(_strip_tags(str(title_match.group("title") or ""))) or None
        phone, phone_ext, email = _extract_contact_channels_from_html_fragment(
            segment,
            fallback_phone=fallback_phone,
            fallback_phone_ext=fallback_phone_ext,
            fallback_email=fallback_email,
        )
        source_context = normalize_whitespace(_strip_tags(segment)) or ""
        source_context, _ = normalize_revize_fragmented_text(source_context)
        out.append(
            {
                "name": name,
                "title": title,
                "department": department_hint,
                "email": email or None,
                "email_type": infer_email_type(email),
                "phone": phone or None,
                "phone_ext": phone_ext or None,
                "address": None,
                "hours": None,
                "source_context": source_context[:240],
                "source_url": page_url,
                "confidence": 0.84,
                "revize_source_type": "contact_card",
            }
        )
    return out


def _extract_revize_inline_staff_lists_regex(
    html_text: str,
    page_url: str,
    department_hint: str | None,
) -> list[dict[str, str | float | None]]:
    out: list[dict[str, str | float | None]] = []
    if not html_text:
        return out
    for match in re.finditer(r"(?is)<(p|li|div|td)[^>]*>(.*?)</\1>", html_text):
        body = str(match.group(2) or "")
        href_values = _extract_href_values_from_html(body)
        if not any(value.lower().startswith("mailto:") for value in href_values):
            continue
        text = normalize_whitespace(_strip_tags(body)) or ""
        text, _ = normalize_revize_fragmented_text(text)
        if not text or "," not in text:
            continue
        main_part = re.split(r"\s*(?:-|–|—|â€“)\s*", text, maxsplit=1)[0]
        parsed = re.match(
            r"^\s*([A-Z][a-zA-Z'`.-]+(?:\s+[A-Z][a-zA-Z'`.-]+){1,2})\s*,\s*(.+?)\s*$",
            main_part,
        )
        if not parsed:
            continue
        name = normalize_whitespace(parsed.group(1)) or None
        title = normalize_whitespace(parsed.group(2)) or None
        phone, phone_ext, email = _extract_contact_channels_from_html_fragment(body)
        out.append(
            {
                "name": name,
                "title": title,
                "department": department_hint,
                "email": email or None,
                "email_type": infer_email_type(email),
                "phone": phone or None,
                "phone_ext": phone_ext or None,
                "address": None,
                "hours": None,
                "source_context": text[:240],
                "source_url": page_url,
                "confidence": 0.82,
                "revize_source_type": "inline_staff_list",
            }
        )
    return out


def _extract_revize_labeled_staff_blocks_regex(
    html_text: str,
    page_url: str,
    department_hint: str | None,
) -> list[dict[str, str | float | None]]:
    out: list[dict[str, str | float | None]] = []
    if not html_text:
        return out
    for match in re.finditer(r"(?is)<(p|li|div|td)[^>]*>(.*?)</\1>", html_text):
        body = str(match.group(2) or "")
        text = normalize_whitespace(_strip_tags(body)) or ""
        text, _ = normalize_revize_fragmented_text(text)
        if ":" not in text:
            continue
        label_match = re.match(r"^\s*([^:]{2,80}):\s*", text)
        if not label_match:
            continue
        label = normalize_whitespace(label_match.group(1)) or None
        if not label or _is_action_text(label):
            continue

        candidate_name = None
        for anchor_match in re.finditer(r"(?is)<a[^>]*href\s*=\s*(?:\"([^\"]+)\"|'([^']+)')[^>]*>(.*?)</a>", body):
            href_value = anchor_match.group(1) or anchor_match.group(2) or ""
            if href_value.lower().startswith("mailto:"):
                continue
            anchor_text = normalize_whitespace(_strip_tags(anchor_match.group(3) or "")) or ""
            if _looks_like_person_name(anchor_text):
                candidate_name = anchor_text
                break
        if not candidate_name:
            candidate_name = _extract_person_name_from_text(text)
        if not candidate_name:
            continue
        phone, phone_ext, email = _extract_contact_channels_from_html_fragment(body)
        out.append(
            {
                "name": candidate_name,
                "title": label,
                "department": department_hint,
                "email": email or None,
                "email_type": infer_email_type(email),
                "phone": phone or None,
                "phone_ext": phone_ext or None,
                "address": None,
                "hours": None,
                "source_context": text[:240],
                "source_url": page_url,
                "confidence": 0.81,
                "revize_source_type": "labeled_staff",
            }
        )
    return out


def _extract_address_like_line(lines: list[str]) -> str | None:
    for line in lines:
        candidate = normalize_whitespace(line) or ""
        if not candidate:
            continue
        if re.search(r"\b\d{1,6}\s+[A-Za-z0-9].*(street|st|road|rd|avenue|ave|lane|ln|drive|dr|boulevard|blvd|court|ct|way)\b", candidate, flags=re.IGNORECASE):
            return candidate
    return None


def _extract_hours_like_line(lines: list[str]) -> str | None:
    for line in lines:
        candidate = normalize_whitespace(line) or ""
        lowered = candidate.lower()
        if "hours" in lowered:
            return candidate
        if re.search(r"\b(mon|tue|wed|thu|fri|sat|sun)\b", lowered) and re.search(r"\b(am|pm)\b", lowered):
            return candidate
    return None


def _extract_table_headers_regex(table_html: str) -> list[str]:
    headers = [
        normalize_whitespace(_strip_tags(item)) or ""
        for item in re.findall(r"(?is)<th[^>]*>(.*?)</th>", table_html or "")
    ]
    headers = [item for item in headers if item]
    if headers:
        return headers

    first_row = re.search(r"(?is)<tr[^>]*>(.*?)</tr>", table_html or "")
    if not first_row:
        return []
    out = [
        normalize_whitespace(_strip_tags(item)) or ""
        for item in re.findall(r"(?is)<t[dh][^>]*>(.*?)</t[dh]>", first_row.group(1))
    ]
    return [item for item in out if item]


def _extract_table_contacts_regex(
    html_text: str,
    source_url: str,
) -> list[dict[str, str | float | None]]:
    out: list[dict[str, str | float | None]] = []
    for table_html in re.findall(r"(?is)<table[^>]*>(.*?)</table>", html_text or ""):
        headers = _extract_table_headers_regex(table_html)
        mapping = _map_revize_headers(headers)
        if len(mapping) < 2 and _table_header_hits(headers) < 2:
            continue
        rows = re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", table_html)
        for row_html in rows:
            cells = [
                normalize_whitespace(_strip_tags(item)) or ""
                for item in re.findall(r"(?is)<t[dh][^>]*>(.*?)</t[dh]>", row_html)
            ]
            values = [value for value in cells if value]
            if len(values) < 2:
                continue
            row_blob = " | ".join(values)
            emails = set(extract_emails(row_blob))
            for href in re.findall(
                r"""(?is)\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""",
                row_html,
            ):
                href_value = href[0] or href[1] or href[2]
                emails.update(extract_emails_from_href(href_value))
            phones = extract_phone_candidates(row_blob)
            if not emails and not phones:
                continue
            name = _safe_cell_value(values, mapping.get("name")) or _guess_name_from_values(values)
            title = _safe_cell_value(values, mapping.get("title")) or _guess_title_from_values(values, name=name)
            department = _safe_cell_value(values, mapping.get("department")) or _guess_department_from_values(values)
            phone = str(phones[0].get("phone") or "") if phones else ""
            phone_ext = str(phones[0].get("phone_ext") or "") if phones else ""
            email = sorted(emails)[0].lower() if emails else ""
            out.append(
                {
                    "name": name,
                    "title": title,
                    "department": department,
                    "email": email or None,
                    "email_type": infer_email_type(email),
                    "phone": phone or None,
                    "phone_ext": phone_ext or None,
                    "address": None,
                    "hours": None,
                    "source_context": row_blob[:240],
                    "source_url": source_url,
                    "confidence": 0.77,
                    "revize_source_type": "table_directory",
                }
            )
    return out


def _extract_profile_blocks_regex(
    html_text: str,
    source_url: str,
) -> list[dict[str, str | float | None]]:
    out: list[dict[str, str | float | None]] = []
    for match in re.finditer(r"(?is)<(div|section|article|li)([^>]*)>(.*?)</\1>", html_text or ""):
        attrs = str(match.group(2) or "").lower()
        body = str(match.group(3) or "")
        marker = any(token in attrs for token in ("staff", "profile", "employee", "directory", "card"))
        text = normalize_whitespace(_strip_tags(body)) or ""
        if not text:
            continue
        if not marker and "read more" not in text.lower():
            continue
        emails = set(extract_emails(text))
        for href in re.findall(
            r"""(?is)\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""",
            body,
        ):
            href_value = href[0] or href[1] or href[2]
            emails.update(extract_emails_from_href(href_value))
        phones = extract_phone_candidates(text)
        if not emails and not phones:
            continue
        lines = _clean_lines(text.splitlines())
        name = _guess_name_from_values(lines)
        title = _guess_title_from_values(lines, name=name)
        department = _guess_department_from_values(lines)
        phone = str(phones[0].get("phone") or "") if phones else ""
        phone_ext = str(phones[0].get("phone_ext") or "") if phones else ""
        email = sorted(emails)[0].lower() if emails else ""
        out.append(
            {
                "name": name,
                "title": title,
                "department": department,
                "email": email or None,
                "email_type": infer_email_type(email),
                "phone": phone or None,
                "phone_ext": phone_ext or None,
                "address": None,
                "hours": None,
                "source_context": " | ".join(lines[:6])[:240],
                "source_url": source_url,
                "confidence": 0.72,
                "revize_source_type": "profile_block",
            }
        )
    return out


def _extract_department_sections_line_fallback(
    html_text: str,
    source_url: str,
) -> list[dict[str, str | float | None]]:
    lines = _extract_lines(html_text)
    if not lines:
        return []
    out: list[dict[str, str | float | None]] = []
    current_department: str | None = None
    for idx, line in enumerate(lines):
        if _looks_like_department(line):
            current_department = line
            continue
        nearby = " | ".join(lines[max(0, idx - 2): min(len(lines), idx + 3)])
        emails = extract_emails(nearby)
        phones = extract_phone_candidates(nearby)
        if not emails and not phones:
            continue
        name = _guess_name_from_values([line])
        title = _guess_title_from_values(lines[max(0, idx - 1): min(len(lines), idx + 2)], name=name)
        department = current_department or _guess_department_from_values([line])
        phone = str(phones[0].get("phone") or "") if phones else ""
        phone_ext = str(phones[0].get("phone_ext") or "") if phones else ""
        email = emails[0].lower() if emails else ""
        out.append(
            {
                "name": name,
                "title": title,
                "department": department,
                "email": email or None,
                "email_type": infer_email_type(email),
                "phone": phone or None,
                "phone_ext": phone_ext or None,
                "address": None,
                "hours": None,
                "source_context": nearby[:240],
                "source_url": source_url,
                "confidence": 0.69,
                "revize_source_type": "department_section",
            }
        )
    return out


def _strip_tags(value: str) -> str:
    text = re.sub(r"(?is)<\s*br\s*/?\s*>", "\n", value or "")
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    return normalize_whitespace(html_unescape(text)) or ""


def _extract_table_headers(table) -> list[str]:
    for row in table.find_all("tr"):
        headers = row.find_all("th")
        if not headers:
            continue
        out = [normalize_whitespace(cell.get_text(" ", strip=True)) or "" for cell in headers]
        out = [item for item in out if item]
        if out:
            return out
    return []


def _map_revize_headers(headers: list[str]) -> dict[str, int]:
    mapped: dict[str, int] = {}
    normalized = [_normalize_token(header) for header in headers]
    for idx, header in enumerate(normalized):
        if any(token in header for token in ("name", "employee", "staff")):
            mapped.setdefault("name", idx)
        elif any(token in header for token in ("title", "position", "profession", "role")):
            mapped.setdefault("title", idx)
        elif any(token in header for token in ("department", "office", "division", "location")):
            mapped.setdefault("department", idx)
        elif "email" in header:
            mapped.setdefault("email", idx)
        elif any(token in header for token in ("phone", "telephone", "tel")):
            mapped.setdefault("phone", idx)
    return mapped


def _safe_cell_value(values: list[str], index: int | None) -> str | None:
    if index is None:
        return None
    if index < 0 or index >= len(values):
        return None
    value = normalize_whitespace(values[index]) or ""
    return value or None


def _guess_name_from_values(values: list[str]) -> str | None:
    for value in values[:4]:
        if _looks_like_person_name(value):
            return normalize_whitespace(value)
        extracted = _extract_person_name_from_text(value)
        if extracted:
            return extracted
    return None


def _guess_title_from_values(values: list[str], name: str | None = None) -> str | None:
    normalized_name = _normalize_token(name or "")
    for value in values[:5]:
        candidate = normalize_whitespace(value) or ""
        if not candidate:
            continue
        if normalized_name and _normalize_token(candidate) == normalized_name:
            continue
        if _looks_like_title(candidate):
            return candidate
    return None


def _guess_department_from_values(values: list[str]) -> str | None:
    for value in values[:5]:
        candidate = normalize_whitespace(value) or ""
        if not candidate:
            continue
        if _looks_like_department(candidate):
            return candidate
    return None


def _find_heading_name(block) -> str | None:
    for heading in block.find_all(["h1", "h2", "h3", "h4", "strong"], recursive=True):
        text = normalize_whitespace(heading.get_text(" ", strip=True)) or ""
        if _looks_like_person_name(text):
            return text
    return None


def _discover_profile_blocks(soup) -> list:
    if soup is None:
        return []

    out: list = []
    seen: set[int] = set()
    for tag in soup.find_all(["article", "section", "li", "div"]):
        if tag.find_parent("table") is not None:
            continue
        snippet = normalize_whitespace(tag.get_text("\n", strip=True)) or ""
        if len(snippet) < 24 or len(snippet) > 420:
            continue
        emails = set(extract_emails(snippet))
        for anchor in tag.find_all("a", href=True):
            emails.update(extract_emails_from_href(str(anchor.get("href") or "")))
        phones = extract_phone_candidates(snippet)
        if not emails and not phones:
            continue
        marker_blob = (
            " ".join(str(token) for token in tag.get("class", []))
            + " "
            + str(tag.get("id") or "")
        ).lower()
        has_marker = any(
            token in marker_blob
            for token in ("staff", "profile", "employee", "team", "directory", "person", "card")
        )
        has_heading = _find_heading_name(tag) is not None
        if not has_marker and not has_heading:
            continue
        object_id = id(tag)
        if object_id in seen:
            continue
        seen.add(object_id)
        out.append(tag)
    return out


def _iter_department_sections(soup) -> list[tuple[str | None, str]]:
    sections: list[tuple[str | None, str]] = []
    for heading in soup.find_all(["h2", "h3", "h4", "strong"]):
        heading_text = normalize_whitespace(heading.get_text(" ", strip=True)) or ""
        if not _looks_like_department(heading_text):
            continue
        chunks: list[str] = []
        for sibling in heading.find_next_siblings():
            if sibling.name in {"h1", "h2", "h3", "h4", "strong"}:
                break
            line = normalize_whitespace(sibling.get_text("\n", strip=True)) or ""
            if not line:
                continue
            if len(line) > 1200:
                continue
            chunks.append(line)
            if len(chunks) >= 20:
                break
        if not chunks:
            continue
        sections.append((heading_text, "\n".join(chunks)))
    return sections


def _clean_lines(lines: Iterable[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        cleaned = normalize_whitespace(line) or ""
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if lowered in REVIZE_ACTION_TEXT_REJECTS:
            continue
        out.append(cleaned)
    return out


def _extract_lines(html_text: str) -> list[str]:
    if not html_text:
        return []
    if BeautifulSoup is None:
        raw = re.sub(r"(?s)<[^>]+>", "\n", html_text)
        lines = [normalize_whitespace(line) or "" for line in raw.splitlines()]
        return [line for line in lines if line]
    try:
        soup = BeautifulSoup(html_text, "html.parser")
    except Exception:
        return []
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    lines = [normalize_whitespace(line) or "" for line in soup.get_text("\n").splitlines()]
    return [line for line in lines if line]


def _nearest_department_heading(node) -> str | None:
    heading = node.find_previous(["h1", "h2", "h3", "h4", "strong"])
    if heading is None:
        return None
    text = normalize_whitespace(heading.get_text(" ", strip=True))
    if not text or not _looks_like_department(text):
        return None
    return text


def _normalize_revize_contact_row(
    row: dict[str, str | float | None],
    source_url: str,
    source_kind: str,
    reduction_counts: Counter[str],
) -> dict[str, str | float | None] | None:
    email = str(row.get("email") or "").strip().lower()
    source_context = normalize_whitespace(str(row.get("source_context") or "")) or ""
    source_context, source_context_merge_count = normalize_revize_fragmented_text(source_context)
    if source_context_merge_count > 0:
        reduction_counts["revize_split_text_merged"] += source_context_merge_count
    phone, parsed_phone_ext, phone_merge_count, ext_parse_count = parse_revize_phone_and_ext(
        phone_value=row.get("phone"),
        fallback_text=source_context,
        phone_ext_value=row.get("phone_ext"),
    )
    if phone_merge_count > 0:
        reduction_counts["revize_split_text_merged"] += phone_merge_count
    if ext_parse_count > 0:
        reduction_counts["revize_phone_extensions_parsed"] += ext_parse_count
    phone_ext = parsed_phone_ext or _normalize_phone_ext_string(row.get("phone_ext"))

    name = normalize_whitespace(str(row.get("name") or "")) or None
    if name:
        name, name_merge_count = normalize_revize_fragmented_text(name)
        if name_merge_count > 0:
            reduction_counts["revize_split_text_merged"] += name_merge_count
    title = normalize_whitespace(str(row.get("title") or "")) or None
    if title:
        title, title_merge_count = normalize_revize_fragmented_text(title)
        if title_merge_count > 0:
            reduction_counts["revize_split_text_merged"] += title_merge_count
    department = normalize_whitespace(str(row.get("department") or "")) or None
    if department:
        department, _ = normalize_revize_fragmented_text(department)
    source_type = str(row.get("revize_source_type") or "unknown").strip() or "unknown"
    page_class = str(row.get("revize_page_class") or "generic").strip().lower() or "generic"
    page_priority_score = float(row.get("revize_page_priority_score") or score_revize_page_class(page_class))
    source_context = _tag_revize_source_context(
        source_context,
        source_type,
        page_class=page_class,
        page_priority_score=page_priority_score,
    )
    block_class = str(row.get("revize_block_class") or _classify_revize_row_block(row))
    normalization_flags: list[str] = []
    is_likely_noise = False
    soft_kept = False

    if name and _is_vacancy_name(name):
        reduction_counts["suppressed_vacancy_rows"] += 1
        return None

    if name:
        lowered_name = name.lower()
        if _is_clear_name_reject(name):
            reduction_counts["revize_invalid_name_rejections"] += 1
            reduction_counts["soft_name_clear_reject"] += 1
            normalization_flags.append("invalid_name")
            name = None
            is_likely_noise = True
            soft_kept = True
        if _looks_like_address_or_location(name):
            reduction_counts["revize_invalid_name_rejections"] += 1
            reduction_counts["soft_name_address_like"] += 1
            normalization_flags.append("weak_person_name")
            is_likely_noise = True
            soft_kept = True
        if _is_non_person_label(name):
            reduction_counts["revize_invalid_name_rejections"] += 1
            reduction_counts["soft_name_non_person_label"] += 1
            normalization_flags.append("weak_person_name")
            is_likely_noise = True
            soft_kept = True
        if _looks_like_department(name):
            if not department:
                department = name
            name = None
            reduction_counts["converted_name_to_department"] += 1
            normalization_flags.append("role_only_row")
            is_likely_noise = True
            soft_kept = True
        elif _looks_like_title(name):
            if not title:
                title = name
            name = None
            reduction_counts["converted_name_to_title"] += 1
            normalization_flags.append("role_only_row")
            is_likely_noise = True
            soft_kept = True
        elif lowered_name in REVIZE_GENERIC_HEADING_REJECTS:
            reduction_counts["revize_invalid_name_rejections"] += 1
            reduction_counts["soft_name_generic_heading"] += 1
            normalization_flags.append("weak_person_name")
            is_likely_noise = True
            soft_kept = True
        elif not _accept_revize_person_name(name, title):
            reduction_counts["revize_invalid_name_rejections"] += 1
            reduction_counts["soft_name_invalid_pattern"] += 1
            normalization_flags.append("weak_person_name")
            is_likely_noise = True
            soft_kept = True

    if title and _is_action_text(title):
        title = None
        reduction_counts["soft_title_action_label"] += 1
        normalization_flags.append("weak_person_name")
        is_likely_noise = True
        soft_kept = True
    if department and _is_action_text(department):
        reduction_counts["soft_department_action_label"] += 1
        reduction_counts["revize_department_contamination_rejections"] += 1
        normalization_flags.append("department_contamination")
        is_likely_noise = True
        soft_kept = True
        department = None
    if department and _looks_like_address_or_location(department):
        reduction_counts["soft_department_address_like"] += 1
        reduction_counts["revize_department_contamination_rejections"] += 1
        normalization_flags.append("department_contamination")
        is_likely_noise = True
        soft_kept = True
        department = None
    if (
        department
        and department.strip().lower() == "education"
        and "education" not in (source_url or "").lower()
        and "education department" not in source_context.lower()
    ):
        reduction_counts["soft_department_ambiguous_education"] += 1
        reduction_counts["revize_department_contamination_rejections"] += 1
        normalization_flags.append("department_contamination")
        is_likely_noise = True
        soft_kept = True
        department = None
    if department and any(token in department.lower() for token in ("bids and rfp", "rfp", ", ct", " main street")):
        reduction_counts["soft_department_context_noise"] += 1
        reduction_counts["revize_department_contamination_rejections"] += 1
        normalization_flags.append("department_contamination")
        is_likely_noise = True
        soft_kept = True
        department = None
    if department:
        department = _to_department_label(department) or department

    if not name and (email or phone or title or department):
        source_type = "department_contact_block"
        block_class = "office_contact_block"
        reduction_counts["revize_office_contact_rows_classified"] += 1
        reduction_counts["revize_role_only_rows_demoted"] += 1
        normalization_flags.append("role_only_row")
        is_likely_noise = True
        soft_kept = True
        if not title:
            title = "Department Contact"
        elif _looks_like_title(title):
            title = "Department Contact"
        if not department:
            department = _infer_department_from_source_url(source_url)
        if department and _looks_like_title(department):
            department = None
        reduction_counts["contact_only_mapped_to_department_contact"] += 1
    elif name:
        reduction_counts["revize_person_rows_classified"] += 1

    if _count_contacts_in_context(source_context) > 1 and not name and source_type != "department_contact_block":
        reduction_counts["soft_multi_contact_block_ambiguous"] += 1
        normalization_flags.append("weak_person_name")
        is_likely_noise = True
        soft_kept = True

    if name and not (title or email or phone):
        # survivability rule: keep person rows with >=2 tokens even without direct contact signals.
        if _accept_revize_person_name(name, title):
            normalization_flags.append("missing_contact_method")
            is_likely_noise = True
            soft_kept = True
            reduction_counts["soft_keep_name_without_contact"] += 1
        else:
            reduction_counts["soft_person_missing_supporting_fields"] += 1
            normalization_flags.append("weak_person_name")
            is_likely_noise = True
            soft_kept = True

    if not name and not title and not department:
        reduction_counts["drop_missing_person_and_context"] += 1
        return None

    if not email and not phone:
        reduction_counts["soft_missing_contact_method"] += 1
        normalization_flags.append("missing_contact_method")
        is_likely_noise = True
        soft_kept = True
        if name and title:
            reduction_counts["keep_name_title_without_contact"] += 1

    if name and not _accept_revize_person_name(name, title):
        normalization_flags.append("weak_person_name")
        is_likely_noise = True
        soft_kept = True

    if phone:
        reduction_counts["revize_phone_string_preserved"] += 1

    if _looks_like_content_blob(source_context):
        normalization_flags.append("content_blob")
        is_likely_noise = True
        soft_kept = True
        reduction_counts["soft_content_blob"] += 1

    confidence = round(float(row.get("confidence") or 0.58), 3)
    if source_type == "department_contact_block" or block_class == "office_contact_block":
        confidence = min(confidence, 0.62)
    if _is_contact_hub_source_url(source_url) and (not name or block_class != "person_block"):
        confidence = min(confidence, 0.54)
    if (
        source_type == "department_contact_block"
        and title
        and _looks_like_title(title)
        and "department contact" not in title.lower()
    ):
        confidence = min(confidence, 0.5)
    if is_likely_noise:
        confidence = min(confidence, 0.46)
        reduction_counts["rows_flagged_as_noise"] += 1
    if soft_kept:
        reduction_counts["rows_soft_kept"] += 1

    source_context = _append_normalization_tags(source_context, normalization_flags, is_likely_noise)
    normalization_flag_value = ",".join(sorted(set(normalization_flags))) if normalization_flags else None

    normalized = {
        "name": name,
        "title": title,
        "department": department,
        "email": email or None,
        "email_type": str(row.get("email_type") or infer_email_type(email)),
        "phone": phone or None,
        "phone_ext": (phone_ext or None),
        "address": row.get("address"),
        "hours": row.get("hours"),
        "source_context": source_context,
        "source_url": str(row.get("source_url") or source_url),
        "confidence": confidence,
        "revize_source_kind": source_kind,
        "revize_source_type": source_type or "unknown",
        "revize_page_class": page_class,
        "revize_page_priority_score": page_priority_score,
        "is_likely_noise": 1 if is_likely_noise else 0,
        "normalization_flag": normalization_flag_value,
        "normalization_soft_keep": 1 if soft_kept else 0,
    }
    for extra_key in (
        "revize_block_type",
        "original_lines",
        "reconstructed_name",
        "reconstructed_title",
        "reconstructed_email",
        "reconstructed_phone",
        "reconstructed_phone_ext",
        "reconstruction_accepted",
        "reconstruction_rejection_reason",
    ):
        if extra_key in row:
            normalized[extra_key] = row.get(extra_key)
    return normalized


def _count_contacts_in_context(source_context: str) -> int:
    if not source_context:
        return 0
    emails = extract_emails(source_context)
    phones = extract_phone_candidates(source_context)
    return max(len(emails), len(phones))


def _is_clear_name_reject(value: str) -> bool:
    lowered = (normalize_whitespace(value) or "").lower()
    if not lowered:
        return False
    if re.search(r"https?://|www\.", lowered):
        return True
    return any(token in lowered for token in REVIZE_CLEAR_NAME_DROP_PATTERNS)


def _revize_name_token_count(value: str) -> int:
    cleaned = normalize_whitespace(value) or ""
    tokens = [token for token in re.split(r"\s+", cleaned) if re.search(r"[A-Za-z]", token)]
    return len(tokens)


def _accept_revize_person_name(name: str | None, title: str | None) -> bool:
    if not name:
        return False
    token_count = _revize_name_token_count(name)
    if token_count >= 2:
        return True
    if normalize_whitespace(name) and normalize_whitespace(title or ""):
        return True
    return False


def _looks_like_content_blob(value: str) -> bool:
    text = normalize_whitespace(value) or ""
    if len(text) <= 300:
        return False
    sentence_count = len([segment for segment in re.split(r"[.!?]\s+", text) if segment.strip()])
    return sentence_count >= 2


def _append_normalization_tags(
    source_context: str,
    flags: list[str],
    is_likely_noise: bool,
) -> str:
    cleaned = normalize_whitespace(source_context) or ""
    tags: list[str] = []
    if is_likely_noise:
        tags.append("noise=1")
    if flags:
        tags.append("norm_flag=" + ",".join(sorted(set(flags))))
    if not tags:
        return cleaned
    suffix = " | " + ";".join(tags)
    return f"{cleaned}{suffix}"[:240]


def _contact_dedupe_key(row: dict[str, str | float | None]) -> tuple[str, ...]:
    name = _normalize_token(str(row.get("name") or ""))
    title = _normalize_token(str(row.get("title") or ""))
    department = _normalize_token(str(row.get("department") or ""))
    phone = _normalize_token(str(row.get("phone") or ""))
    if name:
        return (
            "person",
            name,
            title,
            phone,
        )
    email = str(row.get("email") or "").strip().lower()
    if email:
        return ("email", email)
    return (
        "row",
        name,
        title,
        department,
        phone,
    )


def _merge_contacts(
    left: dict[str, str | float | None] | None,
    right: dict[str, str | float | None],
) -> dict[str, str | float | None]:
    if left is None:
        return dict(right)
    merged = dict(left)
    for field in (
        "name",
        "title",
        "department",
        "email",
        "phone",
        "phone_ext",
        "address",
        "hours",
        "source_context",
    ):
        if not str(merged.get(field) or "").strip():
            merged[field] = right.get(field)
    if str(merged.get("email_type") or "").strip().lower() in {"", "unknown"}:
        merged["email_type"] = right.get("email_type")
    if str(merged.get("revize_source_type") or "").strip().lower() in {"", "unknown"}:
        merged["revize_source_type"] = right.get("revize_source_type")
    if str(merged.get("revize_block_type") or "").strip().lower() in {"", "unknown"}:
        merged["revize_block_type"] = right.get("revize_block_type")
    if str(merged.get("revize_page_class") or "").strip().lower() in {"", "generic"}:
        merged["revize_page_class"] = right.get("revize_page_class")
    merged["revize_page_priority_score"] = max(
        float(left.get("revize_page_priority_score") or 0.0),
        float(right.get("revize_page_priority_score") or 0.0),
    )
    merged["confidence"] = max(float(left.get("confidence") or 0.0), float(right.get("confidence") or 0.0))
    return merged


def _dedupe_contact_list(
    contacts: list[dict[str, str | float | None]],
) -> list[dict[str, str | float | None]]:
    deduped: dict[tuple[str, ...], dict[str, str | float | None]] = {}
    for contact in contacts:
        key = _contact_dedupe_key(contact)
        prior = deduped.get(key)
        deduped[key] = _merge_contacts(prior, contact)
    return sorted(
        deduped.values(),
        key=lambda item: (
            -float(item.get("confidence") or 0.0),
            str(item.get("email") or ""),
            str(item.get("name") or ""),
        ),
    )


def _count_extraction_sources(
    contacts: list[dict[str, str | float | None]],
) -> dict[str, int]:
    counts = {
        REVIZE_RECONSTRUCTION_SOURCE_TYPE: 0,
        "sidebar_staff": 0,
        "contact_card": 0,
        "inline_staff_list": 0,
        "labeled_staff": 0,
        "department_contact_block": 0,
        "table_directory": 0,
        "profile_block": 0,
        "department_section": 0,
        "single_profile_page": 0,
        "unknown": 0,
    }
    for row in contacts:
        source = str(row.get("revize_source_type") or "unknown").strip().lower()
        if source in counts:
            counts[source] += 1
        else:
            counts["unknown"] += 1
    return counts


def _count_page_class_sources(
    contacts: list[dict[str, str | float | None]],
) -> dict[str, int]:
    counts = {
        "staff_directory": 0,
        "department_page": 0,
        "contact_hub": 0,
        "generic": 0,
    }
    for row in contacts:
        page_class = str(row.get("revize_page_class") or "generic").strip().lower() or "generic"
        if page_class not in counts:
            page_class = "generic"
        counts[page_class] += 1
    return counts


def _revize_candidate_text_blob(row: dict[str, object]) -> str:
    original_lines_raw = row.get("original_lines") or []
    original_lines: list[str] = []
    if isinstance(original_lines_raw, (list, tuple)):
        for item in original_lines_raw:
            cleaned = normalize_whitespace(str(item) or "") or ""
            if cleaned:
                original_lines.append(cleaned)
            if len(original_lines) >= 16:
                break
    parts = original_lines or [
        normalize_whitespace(str(row.get("source_context") or "")) or "",
        normalize_whitespace(str(row.get("name") or "")) or "",
        normalize_whitespace(str(row.get("title") or "")) or "",
        normalize_whitespace(str(row.get("department") or "")) or "",
    ]
    blob = normalize_whitespace(" ".join(part for part in parts if part)) or ""
    return blob[:500]


def _revize_candidate_has_structured_fields(row: dict[str, object]) -> bool:
    email = str(row.get("email") or "").strip()
    phone = str(row.get("phone") or "").strip()
    phone_ext = str(row.get("phone_ext") or "").strip()
    title = normalize_whitespace(str(row.get("title") or "")) or ""
    return bool(email or phone or phone_ext or title)


def _classify_revize_candidate_block_type(row: dict[str, object]) -> str:
    source_type = (normalize_whitespace(str(row.get("revize_source_type") or "")) or "unknown").lower()
    if source_type in {"contact_card", "profile_block", "single_profile_page", "department_contact_block"}:
        return "contact_card"
    if source_type == REVIZE_RECONSTRUCTION_SOURCE_TYPE:
        return "contact_card"
    if source_type in {"table_directory", "sidebar_staff", "inline_staff_list"}:
        return "staff_row"
    if source_type == "labeled_staff":
        return "staff_row" if _revize_candidate_has_structured_fields(row) else "department_content"
    if source_type == "department_section":
        return "department_content"
    return "unknown"


def _is_revize_non_contact_name(name: str) -> bool:
    normalized_name = normalize_whitespace(name) or ""
    if not normalized_name:
        return False
    lowered_name = normalized_name.lower()
    if lowered_name in REVIZE_NON_CONTACT_NAME_LITERALS:
        return True
    parts = [part for part in normalized_name.split() if part]
    if len(parts) == 1 and not re.fullmatch(r"[A-Z][a-zA-Z'`.-]{1,40}", parts[0]):
        return True
    return False


def _classify_revize_non_contact_block(
    row: dict[str, object],
) -> tuple[str, bool]:
    source_type = (normalize_whitespace(str(row.get("revize_source_type") or "")) or "unknown").lower()
    block_type = _classify_revize_candidate_block_type(row)
    text_blob = _revize_candidate_text_blob(row)
    lowered_blob = text_blob.lower()
    name = normalize_whitespace(str(row.get("name") or "")) or ""
    explicit_non_contact_name = _is_revize_non_contact_name(name)
    keyword_hit = any(keyword in lowered_blob for keyword in REVIZE_NON_CONTACT_KEYWORDS)
    multiple_sentences = len(re.findall(r"[.!?](?:\s|$)", text_blob)) >= 2
    colon_count = text_blob.count(":")
    long_text = len(text_blob) > 120
    structured_fields = _revize_candidate_has_structured_fields(row)
    source_demoted = source_type == "department_section" or (source_type == "labeled_staff" and not structured_fields)

    if keyword_hit or explicit_non_contact_name:
        block_type = "document_content"
    elif block_type == "unknown" and source_demoted:
        block_type = "department_content"

    is_non_contact = False
    if explicit_non_contact_name:
        is_non_contact = True
    elif source_demoted and (long_text or multiple_sentences or colon_count >= 2 or keyword_hit):
        is_non_contact = True
    elif keyword_hit and (long_text or multiple_sentences or colon_count >= 2):
        is_non_contact = True

    if is_non_contact and block_type == "unknown":
        block_type = "document_content" if keyword_hit else "department_content"
    return block_type, is_non_contact


def _extract_person_name_from_text(value: str) -> str | None:
    normalized_value, _ = normalize_revize_fragmented_text(value or "")
    for match in re.finditer(
        r"\b([A-Z][a-zA-Z'`-]+(?:\s+(?:[A-Z]\.|[A-Z][a-zA-Z'`-]+)){1,4}(?:,?\s*(?:Jr\.?|Sr\.?|II|III|IV))?)\b",
        normalized_value,
    ):
        candidate = normalize_whitespace(match.group(1)) or ""
        if _looks_like_person_name(candidate):
            return candidate
    return None


def _looks_like_person_name(value: str) -> bool:
    candidate = normalize_whitespace(value) or ""
    candidate, _ = normalize_revize_fragmented_text(candidate)
    if not candidate:
        return False
    if _is_vacancy_name(candidate):
        return False
    if _is_rejected_name_literal(candidate):
        return False
    if _is_non_person_label(candidate):
        return False
    if _looks_like_address_or_location(candidate):
        return False
    if len(candidate.split()) < 2:
        return False
    if candidate.upper() == candidate and re.search(r"[A-Z]", candidate):
        return False
    if re.search(r"\d", candidate):
        return False
    parts = [part for part in re.split(r"\s+", candidate) if part]
    lowered_candidate = candidate.lower()
    lowered_key = _normalize_token(candidate)
    if lowered_key in {_normalize_token(item) for item in REVIZE_NAME_PHRASE_REJECTS}:
        return False
    if any(token in lowered_candidate for token in REVIZE_NAME_PHRASE_REJECTS):
        return False
    tokenized = [re.sub(r"[^a-z]", "", part.lower()) for part in parts]
    tokenized = [token for token in tokenized if token]
    if any(token in REVIZE_NAME_TOKEN_REJECTS for token in tokenized):
        return False
    if parts and parts[-1].lower().strip(".") in {
        "st",
        "street",
        "rd",
        "road",
        "ave",
        "avenue",
        "ln",
        "lane",
        "dr",
        "drive",
        "blvd",
        "boulevard",
        "ct",
        "court",
        "way",
    }:
        return False
    lowered = candidate.lower()
    if re.search(r"\b(building|annex|municipal|academy|school)\b", lowered):
        return False
    if _is_action_text(candidate):
        return False
    if lowered in REVIZE_GENERIC_HEADING_REJECTS:
        return False
    if _looks_like_department(candidate):
        return False
    if _looks_like_title(candidate):
        return False
    normalized_for_pattern = re.sub(r",\s*(Jr\.?|Sr\.?|II|III|IV)$", r" \1", candidate, flags=re.IGNORECASE)
    normalized_for_pattern = normalize_whitespace(normalized_for_pattern) or ""
    person_name_re = re.compile(
        r"""
        ^
        [A-Z][a-zA-Z'`-]+
        (?:
            \s+(?:[A-Z]\.|[A-Z][a-zA-Z'`-]+|de|del|la|van|von|da|di)
        ){1,4}
        (?:\s+(?:Jr\.?|Sr\.?|II|III|IV))?
        $
        """,
        re.VERBOSE,
    )
    return person_name_re.fullmatch(normalized_for_pattern) is not None


def _looks_like_department(value: str) -> bool:
    lowered = (normalize_whitespace(value) or "").lower()
    if not lowered:
        return False
    return any(token in lowered for token in REVIZE_DEPARTMENT_HINTS)


def _looks_like_title(value: str) -> bool:
    lowered = (normalize_whitespace(value) or "").lower()
    if not lowered:
        return False
    return any(token in lowered for token in REVIZE_TITLE_HINTS)


def _is_action_text(value: str) -> bool:
    lowered = (normalize_whitespace(value) or "").lower()
    if not lowered:
        return False
    if lowered in REVIZE_ACTION_TEXT_REJECTS:
        return True
    return any(token in lowered for token in ("email", "contact", "read more", "click", "view"))


def _is_vacancy_name(value: str) -> bool:
    lowered = (normalize_whitespace(value) or "").lower()
    if not lowered:
        return False
    if lowered in REVIZE_VACANCY_TOKENS:
        return True
    return any(token in lowered for token in REVIZE_VACANCY_TOKENS)


def _is_rejected_name_literal(value: str) -> bool:
    lowered = (normalize_whitespace(value) or "").lower()
    if not lowered:
        return False
    return lowered in REVIZE_NAME_LITERAL_REJECTS


def _is_non_person_label(value: str) -> bool:
    lowered = (normalize_whitespace(value) or "").lower()
    if not lowered:
        return False
    if lowered in REVIZE_NAME_LITERAL_REJECTS:
        return True
    if any(phrase in lowered for phrase in REVIZE_NAME_PHRASE_REJECTS):
        return True
    return any(token in lowered for token in REVIZE_NAME_REJECT_FRAGMENT_TOKENS)


def _looks_like_address_or_location(value: str) -> bool:
    lowered = (normalize_whitespace(value) or "").lower()
    if not lowered:
        return False
    if re.search(r"\b\d{1,5}\b", lowered):
        return True
    if re.search(r"\bct\b|\bconnecticut\b|\b\d{5}(?:-\d{4})?\b", lowered):
        return True
    if re.search(r"\b(?:city|town|village)\b.*\b(?:ct|connecticut)\b", lowered):
        return True
    if any(token in lowered for token in REVIZE_LOCATION_NAME_REJECT_TOKENS):
        return True
    return False


def _normalize_phone_string(value: object) -> str:
    phone, _, _, _ = parse_revize_phone_and_ext(phone_value=value, fallback_text="")
    return phone


def _normalize_phone_ext_string(value: object) -> str:
    if value is None:
        return ""
    digits = re.sub(r"[^0-9]", "", str(value))
    return digits[:6] if digits else ""


def _tag_revize_source_context(
    source_context: str,
    source_type: str,
    page_class: str = "generic",
    page_priority_score: float = 0.0,
) -> str:
    cleaned_context = normalize_whitespace(source_context) or ""
    normalized_source_type = (source_type or "unknown").strip().lower()
    normalized_page_class = (page_class or "generic").strip().lower()
    prefix = (
        f"revize:{normalized_source_type}"
        f"|page_class={normalized_page_class}"
        f"|page_priority={round(float(page_priority_score or 0.0), 2):.2f}"
    )
    if cleaned_context:
        return f"{prefix} | {cleaned_context}"[:240]
    return prefix


def _is_contact_hub_source_url(source_url: str) -> bool:
    lowered = (source_url or "").lower()
    if not lowered:
        return False
    return any(
        token in lowered
        for token in (
            "/contact",
            "contact_us",
            "contact-us",
            "staff_directory",
            "directory",
        )
    )


def _normalize_token(value: str) -> str:
    lowered = (value or "").lower()
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def _trace_row_payload(row: dict[str, object]) -> dict[str, object]:
    original_lines_raw = row.get("original_lines") or row.get("revize_reconstruction_original_lines") or []
    original_lines: list[str] = []
    if isinstance(original_lines_raw, (list, tuple)):
        for item in original_lines_raw:
            cleaned = normalize_whitespace(str(item) or "") or ""
            if cleaned:
                original_lines.append(cleaned)
            if len(original_lines) >= 16:
                break
    reconstruction_accepted = _coerce_int(
        row.get("reconstruction_accepted")
        if row.get("reconstruction_accepted") is not None
        else row.get("accepted")
    )
    reconstruction_rejection_reason = normalize_whitespace(
        str(
            row.get("reconstruction_rejection_reason")
            or row.get("rejection_reason")
            or ""
        )
    ) or None
    return {
        "name": normalize_whitespace(str(row.get("name") or "")) or None,
        "title": normalize_whitespace(str(row.get("title") or "")) or None,
        "department": normalize_whitespace(str(row.get("department") or "")) or None,
        "email": (str(row.get("email") or "").strip().lower() or None),
        "phone": (str(row.get("phone") or "").strip() or None),
        "phone_ext": (str(row.get("phone_ext") or "").strip() or None),
        "source_url": normalize_whitespace(str(row.get("source_url") or "")) or None,
        "source_context": normalize_whitespace(str(row.get("source_context") or "")) or None,
        "revize_source_type": normalize_whitespace(str(row.get("revize_source_type") or "")) or None,
        "revize_block_type": normalize_whitespace(str(row.get("revize_block_type") or "")) or None,
        "revize_page_class": normalize_whitespace(str(row.get("revize_page_class") or "")) or None,
        "revize_page_priority_score": float(row.get("revize_page_priority_score") or 0.0),
        "is_likely_noise": _coerce_int(row.get("is_likely_noise")),
        "normalization_flag": normalize_whitespace(str(row.get("normalization_flag") or "")) or None,
        "confidence": float(row.get("confidence") or 0.0),
        "original_lines": original_lines,
        "reconstructed_name": normalize_whitespace(
            str(row.get("reconstructed_name") or row.get("name") or "")
        ) or None,
        "reconstructed_title": normalize_whitespace(
            str(row.get("reconstructed_title") or row.get("title") or "")
        ) or None,
        "reconstructed_email": (
            str(row.get("reconstructed_email") or row.get("email") or "").strip().lower() or None
        ),
        "reconstructed_phone": normalize_whitespace(
            str(row.get("reconstructed_phone") or row.get("phone") or "")
        ) or None,
        "reconstructed_phone_ext": normalize_whitespace(
            str(
                row.get("reconstructed_phone_ext")
                or row.get("reconstructed_ext")
                or row.get("phone_ext")
                or ""
            )
        ) or None,
        "accepted": reconstruction_accepted,
        "rejection_reason": reconstruction_rejection_reason,
    }


def _pick_row_drop_reason(before: Counter[str], after: Counter[str]) -> str:
    delta: Counter[str] = Counter()
    for key, after_count in after.items():
        increment = int(after_count) - int(before.get(key, 0))
        if increment > 0:
            delta[key] = increment
    if not delta:
        return "unknown_pipeline_drop"
    top = sorted(delta.items(), key=lambda item: (-item[1], item[0]))[0][0]
    return str(top or "unknown_pipeline_drop")


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


def _classify_attempt_outcome(
    status_code: int | None,
    has_body: bool,
    detected: bool,
    http_response_received: bool,
) -> str:
    if status_code == 404:
        return "not_found"
    if not http_response_received:
        return "other_http_error"
    if status_code is not None and status_code >= 400:
        return "other_http_error"
    if not has_body:
        return "empty_response"
    if detected:
        return "ok_detected"
    return "not_detected"


def _coerce_fetch_result(result: FetchResult, request_url: str) -> dict[str, object]:
    status_code = result.status_code
    final_url = normalize_url(result.final_url or request_url) or (result.final_url or request_url)
    text = result.text or ""
    return {
        "request_url": request_url,
        "final_url": final_url,
        "status_code": status_code,
        "redirect_count": int(result.redirect_count or 0),
        "content_type": str(result.content_type or ""),
        "response_headers": dict(result.response_headers or {}),
        "error": str(result.error or ""),
        "text": text,
        "http_response_received": status_code is not None,
    }


def _fetch_revize_http(
    url: str,
    timeout: int,
    session,
    referer: str | None,
    request_headers: dict[str, str],
) -> dict[str, object]:
    headers = dict(request_headers or {})
    if referer:
        headers["Referer"] = referer
    try:
        response = session.get(
            url,
            headers=headers,
            timeout=timeout,
            allow_redirects=True,
        )
    except Exception as exc:
        return {
            "request_url": url,
            "final_url": None,
            "status_code": None,
            "content_type": "",
            "response_headers": {},
            "error": f"request_error:{exc}",
            "text": "",
            "http_response_received": False,
        }

    content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    can_read_text = (
        "text/" in content_type
        or "html" in content_type
        or "xml" in content_type
        or "json" in content_type
        or content_type == ""
    )
    text = response.text if can_read_text else ""
    return {
        "request_url": url,
        "final_url": normalize_url(response.url) or response.url,
        "status_code": int(response.status_code),
        "content_type": content_type,
        "response_headers": dict(response.headers or {}),
        "error": "",
        "text": text or "",
        "http_response_received": True,
    }
