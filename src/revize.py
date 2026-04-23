from __future__ import annotations

from collections import Counter, deque
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
REVIZE_SECTION_ROOTS = (
    "/government/",
    "/departments/",
    "/town_hall/",
    "/city_hall/",
    "/administration/",
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
)
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

FetchFn = Callable[[str, str | None, dict[str, str] | None], FetchResult]


def build_revize_candidate_urls(
    municipality_homepage: str,
    harvested_links: Iterable[dict[str, str] | str] | None = None,
    max_candidates: int = REVIZE_MAX_GENERATED_CANDIDATES,
) -> list[dict[str, str]]:
    base = normalize_url(ensure_url_has_scheme(municipality_homepage))
    if not base:
        return []
    roots = _candidate_base_roots(base)
    out: list[dict[str, str]] = []
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
        seen.add(normalized)
        out.append(
            {
                "url": normalized,
                "source_kind": source_kind,
                "candidate_origin": candidate_origin,
            }
        )

    for root in roots:
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

    return out


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

    return {
        "page_kind": "staff_directory_or_profile" if matched else "unknown",
        "signals": sorted(set(signals)),
        "source_type": source_type,
        "sidebar_staff_hits": sidebar_staff_hits,
        "contact_card_hits": contact_card_hits,
        "inline_staff_hits": inline_staff_hits,
        "labeled_staff_hits": labeled_staff_hits,
        "header_hits": header_hits,
        "profile_block_hits": profile_block_hits,
        "department_section_hits": department_section_hits,
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
                    "source_context": normalize_whitespace(staff.get_text(" ", strip=True)) or "",
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
                "source_context": normalize_whitespace(staff.get_text(" ", strip=True)) or "",
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
    queue: deque[dict[str, str]] = deque(initial_candidates)
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
        "http_responses_received_count": 0,
        "pages_fetched_with_body_count": 0,
        "pages_classified_detected_count": 0,
        "rows_extracted_total": 0,
        "rows_normalized_seen": 0,
        "rows_normalized_kept": 0,
        "rows_normalized_rejected": 0,
        "sidebar_staff_blocks_found": 0,
        "sidebar_staff_contacts_extracted": 0,
        "department_contact_blocks_found": 0,
        "department_contact_rows_extracted": 0,
        "revize_footer_blocks_ignored": 0,
        "revize_hours_blocks_ignored": 0,
        "revize_office_contact_blocks": 0,
        "revize_person_blocks": 0,
        "revize_structural_blocks_dropped": 0,
        "revize_invalid_name_rejections": 0,
        "revize_phone_string_preserved": 0,
    }
    extracted_rows_sample: list[dict[str, object]] = []
    normalized_rows_sample: list[dict[str, object]] = []
    rejected_rows_sample: list[dict[str, object]] = []

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
            counters["revize_structural_blocks_dropped"] += _coerce_int(
                per_page_metrics.get("revize_structural_blocks_dropped")
            )
            counters["revize_invalid_name_rejections"] += _coerce_int(
                per_page_metrics.get("revize_invalid_name_rejections")
            )
            counters["revize_phone_string_preserved"] += _coerce_int(
                per_page_metrics.get("revize_phone_string_preserved")
            )
            counters["rows_extracted_total"] += _coerce_int(per_page_metrics.get("rows_extracted_total"))
            counters["rows_normalized_seen"] += _coerce_int(per_page_metrics.get("rows_normalized_seen"))
            counters["rows_normalized_kept"] += _coerce_int(per_page_metrics.get("rows_normalized_kept"))
            counters["rows_normalized_rejected"] += _coerce_int(per_page_metrics.get("rows_normalized_rejected"))
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
            if extracted_rows:
                contacts_by_url.append(
                    {
                        "url": final_url,
                        "source_kind": str(fetch_row.get("source_kind") or "unknown"),
                        "extraction_source_type": str(page_classification.get("source_type") or "unknown"),
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
                queue.append(discovered)

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
                "directory_match": detected,
                "page_kind": page_kind,
                "detection_signals": list(page_classification.get("signals") or []),
                "extraction_source_type": str(page_classification.get("source_type") or "unknown"),
                "response_body_length": len(text),
                "contacts_extracted": len(extracted_rows),
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
                "revize_structural_blocks_dropped": _coerce_int(
                    per_page_metrics.get("revize_structural_blocks_dropped")
                ),
                "revize_invalid_name_rejections": _coerce_int(
                    per_page_metrics.get("revize_invalid_name_rejections")
                ),
                "revize_phone_string_preserved": _coerce_int(
                    per_page_metrics.get("revize_phone_string_preserved")
                ),
                "page_title": str(fetch_row.get("page_title") or ""),
            }
        )

    deduped_contacts = _dedupe_contact_list(all_contacts)
    extraction_source_counts = _count_extraction_sources(deduped_contacts)
    attempted_urls = [
        str(row.get("request_url") or "")
        for row in attempted_rows
        if str(row.get("request_url") or "")
    ]
    matched_urls_unique = sorted({url for url in matched_urls if url})

    return {
        "candidate_urls_generated": [str(item.get("url") or "") for item in initial_candidates],
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
        "source_type_counts": extraction_source_counts,
        "suspicious_reduction_counts": dict(sorted(suppression_reasons.items())),
        "suppressed_vacancy_rows": _coerce_int(suppression_reasons.get("suppressed_vacancy_rows")),
        "extracted_rows_sample": extracted_rows_sample,
        "normalized_rows_sample": normalized_rows_sample,
        "rejected_rows_sample": rejected_rows_sample,
        "revize_pass_produced_contacts": len(deduped_contacts) > 0,
    }


def discover_revize_profile_candidates(
    html_text: str,
    base_url: str,
    max_candidates: int = REVIZE_MAX_DISCOVERED_PROFILE_PAGES,
) -> list[dict[str, str]]:
    if BeautifulSoup is None:
        return []
    try:
        soup = BeautifulSoup(html_text or "", "html.parser")
    except Exception:
        return []

    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = normalize_url(str(anchor.get("href") or ""), base_url=base_url)
        if not href or href in seen:
            continue
        label = normalize_whitespace(anchor.get_text(" ", strip=True)) or ""
        if not _looks_revize_contact_like(href, label):
            continue
        seen.add(href)
        out.append(
            {
                "url": href,
                "source_kind": _source_kind_from_link(href, label),
                "candidate_origin": "discovered_link",
            }
        )
        if len(out) >= max_candidates:
            break
    return out


def _extract_revize_contacts_with_diagnostics(
    html_text: str,
    source_url: str,
    source_kind: str,
) -> tuple[list[dict[str, str | float | None]], Counter[str], dict[str, int], dict[str, object]]:
    sanitized_html_text, soup, structural_counts = _sanitize_revize_html(html_text)
    page_context = _build_page_context(html_text=sanitized_html_text, source_url=source_url, soup=soup)
    department_like = _is_department_like_page(source_url, page_context)

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

    if not extracted:
        classified = classify_revize_page(html_text=sanitized_html_text, url=source_url)
        fallback_source_type = str(classified.get("source_type") or "unknown")
        for row in extract_contacts(sanitized_html_text, source_url, page_type="directory_page"):
            candidate = dict(row)
            candidate["revize_source_type"] = fallback_source_type
            extracted.append(candidate)

    block_classification_counts = {
        "person_block": 0,
        "office_contact_block": 0,
        "structural_block": 0,
    }
    for row in extracted:
        block_class = _classify_revize_row_block(row)
        block_classification_counts[block_class] = block_classification_counts.get(block_class, 0) + 1
        row["revize_block_class"] = block_class

    reduction_counts: Counter[str] = Counter()
    deduped: dict[tuple[str, ...], dict[str, str | float | None]] = {}
    normalized_kept_count = 0
    extracted_rows_sample: list[dict[str, object]] = []
    normalized_rows_sample: list[dict[str, object]] = []
    rejected_rows_sample: list[dict[str, object]] = []
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
        _coerce_int(reduction_counts.get("drop_name_literal_reject"))
        + _coerce_int(reduction_counts.get("drop_name_action_label"))
        + _coerce_int(reduction_counts.get("drop_name_generic_heading"))
        + _coerce_int(reduction_counts.get("drop_name_invalid_pattern"))
        + _coerce_int(reduction_counts.get("drop_name_address_like"))
        + _coerce_int(reduction_counts.get("drop_name_non_person_label"))
    )
    metrics = {
        "rows_extracted_total": len(extracted),
        "rows_normalized_seen": len(extracted),
        "rows_normalized_kept": normalized_kept_count,
        "rows_normalized_rejected": max(0, len(extracted) - normalized_kept_count),
        "sidebar_staff_blocks_found": _count_sidebar_staff_blocks(sanitized_html_text),
        "sidebar_staff_contacts_extracted": _coerce_int(source_counts.get("sidebar_staff")),
        "department_contact_blocks_found": _count_department_contact_blocks(sanitized_html_text),
        "department_contact_rows_extracted": _coerce_int(source_counts.get("department_contact_block")),
        "revize_footer_blocks_ignored": _coerce_int(structural_counts.get("revize_footer_blocks_ignored")),
        "revize_hours_blocks_ignored": _coerce_int(structural_counts.get("revize_hours_blocks_ignored")),
        "revize_structural_blocks_dropped": _coerce_int(structural_counts.get("revize_structural_blocks_dropped")),
        "revize_person_blocks": _coerce_int(block_classification_counts.get("person_block")),
        "revize_office_contact_blocks": _coerce_int(block_classification_counts.get("office_contact_block")),
        "revize_invalid_name_rejections": invalid_name_rejections,
        "revize_phone_string_preserved": _coerce_int(reduction_counts.get("revize_phone_string_preserved")),
        "extracted_rows_sample": extracted_rows_sample,
        "normalized_rows_sample": normalized_rows_sample,
        "rejected_rows_sample": rejected_rows_sample,
    }
    return contacts, reduction_counts, source_counts, metrics


def _fetch_revize_candidate(
    municipality_homepage: str,
    candidate: dict[str, str],
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
    if "staff_directory.php" in lowered:
        return "staff_directory_php"
    if "staff-directory" in lowered:
        return "staff_directory_path"
    if "directory" in lowered:
        return "directory_path"
    if "contact" in lowered:
        return "contact_path"
    return "unknown"


def _source_kind_from_link(url: str, label: str) -> str:
    lowered = f"{url} {label}".lower()
    if "read more" in lowered or "profile" in lowered:
        return "single_profile_page"
    return _source_kind_from_path(lowered)


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

    context["department_inferred"] = _infer_department_from_page_context(context, source_url)
    return context


def _infer_department_from_page_context(
    page_context: dict[str, object] | None,
    source_url: str,
) -> str | None:
    context = dict(page_context or {})
    candidates: list[str] = []
    for key in ("h1", "page_title", "url_department"):
        value = normalize_whitespace(str(context.get(key) or "")) or ""
        if value:
            candidates.append(value)
    for value in context.get("breadcrumbs") or []:
        cleaned = normalize_whitespace(str(value) or "") or ""
        if cleaned:
            candidates.append(cleaned)
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
    lowered = re.sub(r"(?i)\btown of\b.*$", "", lowered).strip(" -|")
    lowered = re.sub(r"(?i)\bcity of\b.*$", "", lowered).strip(" -|")
    lowered = lowered.replace("_", " ").replace("-", " ")
    lowered = re.sub(r"\s+", " ", lowered).strip()
    if not lowered:
        return None
    lowered = re.sub(r"(?i)\bdepartment(s)?\b", "", lowered).strip()
    lowered = re.sub(r"(?i)\boffice\b", "", lowered).strip()
    lowered = re.sub(r"(?i)\bcontact info\b", "", lowered).strip()
    if not lowered:
        return None
    if lowered in REVIZE_GENERIC_HEADING_REJECTS:
        return None
    if lowered in {"government", "departments", "home", "staff", "directory"}:
        return None
    if lowered in {"education", "contact us", "office hours", "resources", "related links"}:
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
    if not email:
        emails = extract_emails(blob)
        if emails:
            email = emails[0].lower()
    if not phone:
        phone_candidates = extract_phone_candidates(blob)
        if phone_candidates:
            phone = str(phone_candidates[0].get("phone") or "") or None
            phone_ext = str(phone_candidates[0].get("phone_ext") or "") or phone_ext
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
                    "source_context": normalize_whitespace(_strip_tags(block_html)) or "",
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
                "source_context": normalize_whitespace(_strip_tags(block_html)) or "",
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
                "source_context": (normalize_whitespace(_strip_tags(segment)) or "")[:240],
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
    return normalize_whitespace(text) or ""


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
    phone = _normalize_phone_string(row.get("phone"))

    name = normalize_whitespace(str(row.get("name") or "")) or None
    title = normalize_whitespace(str(row.get("title") or "")) or None
    department = normalize_whitespace(str(row.get("department") or "")) or None
    source_context = normalize_whitespace(str(row.get("source_context") or "")) or ""
    source_type = str(row.get("revize_source_type") or "unknown").strip() or "unknown"
    source_context = _tag_revize_source_context(source_context, source_type)
    block_class = str(row.get("revize_block_class") or _classify_revize_row_block(row))

    if name and _is_vacancy_name(name):
        reduction_counts["suppressed_vacancy_rows"] += 1
        return None
    if name and _is_rejected_name_literal(name):
        reduction_counts["revize_invalid_name_rejections"] += 1
        reduction_counts["drop_name_literal_reject"] += 1
        return None

    if name:
        lowered_name = name.lower()
        if _is_action_text(name):
            reduction_counts["revize_invalid_name_rejections"] += 1
            reduction_counts["drop_name_action_label"] += 1
            return None
        if _looks_like_address_or_location(name):
            reduction_counts["revize_invalid_name_rejections"] += 1
            reduction_counts["drop_name_address_like"] += 1
            return None
        if _is_non_person_label(name):
            reduction_counts["revize_invalid_name_rejections"] += 1
            reduction_counts["drop_name_non_person_label"] += 1
            return None
        if _looks_like_department(name):
            if not department:
                department = name
            name = None
            reduction_counts["converted_name_to_department"] += 1
        elif _looks_like_title(name):
            if not title:
                title = name
            name = None
            reduction_counts["converted_name_to_title"] += 1
        elif lowered_name in REVIZE_GENERIC_HEADING_REJECTS:
            reduction_counts["revize_invalid_name_rejections"] += 1
            reduction_counts["drop_name_generic_heading"] += 1
            return None
        elif not _looks_like_person_name(name):
            reduction_counts["revize_invalid_name_rejections"] += 1
            reduction_counts["drop_name_invalid_pattern"] += 1
            name = None

    if title and _is_action_text(title):
        title = None
        reduction_counts["drop_title_action_label"] += 1
    if department and _is_action_text(department):
        department = None
        reduction_counts["drop_department_action_label"] += 1
    if department and _looks_like_address_or_location(department):
        reduction_counts["drop_department_address_like"] += 1
        department = None
    if (
        department
        and department.strip().lower() == "education"
        and "education" not in (source_url or "").lower()
        and "education department" not in source_context.lower()
    ):
        reduction_counts["drop_department_ambiguous_education"] += 1
        department = None
    if department:
        department = _to_department_label(department) or department

    if not name and (email or phone):
        source_type = "department_contact_block"
        block_class = "office_contact_block"
        if not title:
            title = "Department Contact"
        elif _looks_like_title(title):
            title = "Department Contact"
        if not department:
            department = _infer_department_from_source_url(source_url)
        if department and _looks_like_title(department):
            department = None
        reduction_counts["contact_only_mapped_to_department_contact"] += 1

    if _count_contacts_in_context(source_context) > 1 and not name and source_type != "department_contact_block":
        reduction_counts["drop_multi_contact_block_ambiguous"] += 1
        return None

    if name and not (title or email or phone):
        reduction_counts["drop_person_missing_supporting_fields"] += 1
        return None

    if not name and not title and not department:
        reduction_counts["drop_missing_person_and_context"] += 1
        return None

    if not email and not phone:
        if source_type == "sidebar_staff" and name and title:
            reduction_counts["keep_sidebar_staff_without_contact"] += 1
        else:
            reduction_counts["drop_missing_contact_method"] += 1
            return None

    if phone:
        reduction_counts["revize_phone_string_preserved"] += 1

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

    normalized = {
        "name": name,
        "title": title,
        "department": department,
        "email": email or None,
        "email_type": str(row.get("email_type") or infer_email_type(email)),
        "phone": phone or None,
        "phone_ext": (_normalize_phone_ext_string(row.get("phone_ext")) or None),
        "address": row.get("address"),
        "hours": row.get("hours"),
        "source_context": source_context,
        "source_url": str(row.get("source_url") or source_url),
        "confidence": confidence,
        "revize_source_kind": source_kind,
        "revize_source_type": source_type or "unknown",
    }
    return normalized


def _count_contacts_in_context(source_context: str) -> int:
    if not source_context:
        return 0
    emails = extract_emails(source_context)
    phones = extract_phone_candidates(source_context)
    return max(len(emails), len(phones))


def _contact_dedupe_key(row: dict[str, str | float | None]) -> tuple[str, ...]:
    email = str(row.get("email") or "").strip().lower()
    if email:
        return ("email", email)
    return (
        "row",
        _normalize_token(str(row.get("name") or "")),
        _normalize_token(str(row.get("title") or "")),
        _normalize_token(str(row.get("department") or "")),
        _normalize_token(str(row.get("phone") or "")),
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


def _extract_person_name_from_text(value: str) -> str | None:
    for match in re.finditer(r"\b([A-Z][a-zA-Z'`-]+(?:\s+[A-Z][a-zA-Z'`-]+){1,2})\b", value or ""):
        candidate = normalize_whitespace(match.group(1)) or ""
        if _looks_like_person_name(candidate):
            return candidate
    return None


def _looks_like_person_name(value: str) -> bool:
    candidate = normalize_whitespace(value) or ""
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
    if re.search(r"\d", candidate):
        return False
    parts = candidate.split()
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
    if re.search(r"\b(building|annex)\b", lowered):
        return False
    if _is_action_text(candidate):
        return False
    if lowered in REVIZE_GENERIC_HEADING_REJECTS:
        return False
    if _looks_like_department(candidate):
        return False
    if _looks_like_title(candidate):
        return False
    return re.fullmatch(r"[A-Z][a-zA-Z'`.-]+(?:\s+[A-Z][a-zA-Z'`.-]+){1,2}", candidate) is not None


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
    return any(token in lowered for token in REVIZE_NAME_REJECT_FRAGMENT_TOKENS)


def _looks_like_address_or_location(value: str) -> bool:
    lowered = (normalize_whitespace(value) or "").lower()
    if not lowered:
        return False
    if re.search(r"\b\d{1,5}\b", lowered):
        return True
    if re.search(r"\bct\b|\bconnecticut\b|\b\d{5}(?:-\d{4})?\b", lowered):
        return True
    if any(token in lowered for token in REVIZE_LOCATION_NAME_REJECT_TOKENS):
        return True
    return False


def _normalize_phone_string(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    phone_candidates = extract_phone_candidates(text)
    if phone_candidates:
        return str(phone_candidates[0].get("phone") or "").strip()
    digits = re.sub(r"[^0-9]", "", text)
    if len(digits) >= 10:
        if len(digits) == 11 and digits.startswith("1"):
            return digits[1:]
        return digits
    return ""


def _normalize_phone_ext_string(value: object) -> str:
    if value is None:
        return ""
    digits = re.sub(r"[^0-9]", "", str(value))
    return digits[:6] if digits else ""


def _tag_revize_source_context(source_context: str, source_type: str) -> str:
    cleaned_context = normalize_whitespace(source_context) or ""
    prefix = f"revize:{(source_type or 'unknown').strip().lower()}"
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
        "confidence": float(row.get("confidence") or 0.0),
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
