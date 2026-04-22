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
    "contact",
    "read more",
    "office phone",
    "location",
    "learn more",
    "details",
    "view",
    "click here",
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
    if key_value_hits >= 3 and profile_block_hits <= 1 and header_hits == 0:
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
            header_hits >= 2
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
    contacts, _, _ = _extract_revize_contacts_with_diagnostics(
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
    }

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
        if detected and text:
            extracted_rows, local_suppression, source_type_counts = _extract_revize_contacts_with_diagnostics(
                html_text=text,
                source_url=final_url,
                source_kind=str(fetch_row.get("source_kind") or "unknown"),
            )
            suppression_reasons.update(local_suppression)
            if extracted_rows:
                contacts_by_url.append(
                    {
                        "url": final_url,
                        "source_kind": str(fetch_row.get("source_kind") or "unknown"),
                        "extraction_source_type": str(page_classification.get("source_type") or "unknown"),
                        "contacts_extracted": len(extracted_rows),
                        "source_type_counts": source_type_counts,
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
        "suspicious_reduction_counts": dict(sorted(suppression_reasons.items())),
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
) -> tuple[list[dict[str, str | float | None]], Counter[str], dict[str, int]]:
    extracted: list[dict[str, str | float | None]] = []
    extracted.extend(extract_revize_table_directory(html_text, source_url))
    extracted.extend(extract_revize_profile_blocks(html_text, source_url))
    extracted.extend(extract_revize_department_sections(html_text, source_url))
    extracted.extend(extract_revize_single_profile_page(html_text, source_url))
    if not extracted:
        classified = classify_revize_page(html_text=html_text, url=source_url)
        fallback_source_type = str(classified.get("source_type") or "unknown")
        for row in extract_contacts(html_text, source_url, page_type="directory_page"):
            candidate = dict(row)
            candidate["revize_source_type"] = fallback_source_type
            extracted.append(candidate)

    reduction_counts: Counter[str] = Counter()
    deduped: dict[tuple[str, ...], dict[str, str | float | None]] = {}
    for row in extracted:
        normalized = _normalize_revize_contact_row(
            row=row,
            source_url=source_url,
            source_kind=source_kind,
            reduction_counts=reduction_counts,
        )
        if normalized is None:
            continue
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
    return contacts, reduction_counts, _count_extraction_sources(contacts)


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
    phone = str(row.get("phone") or "").strip()
    if not email and not phone:
        reduction_counts["drop_missing_contact_method"] += 1
        return None

    name = normalize_whitespace(str(row.get("name") or "")) or None
    title = normalize_whitespace(str(row.get("title") or "")) or None
    department = normalize_whitespace(str(row.get("department") or "")) or None
    source_context = normalize_whitespace(str(row.get("source_context") or "")) or ""
    source_type = str(row.get("revize_source_type") or "unknown")

    if name:
        lowered_name = name.lower()
        if _is_action_text(name):
            reduction_counts["drop_name_action_label"] += 1
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
            reduction_counts["drop_name_generic_heading"] += 1
            return None
        elif not _looks_like_person_name(name):
            reduction_counts["drop_name_invalid_pattern"] += 1
            name = None

    if title and _is_action_text(title):
        title = None
        reduction_counts["drop_title_action_label"] += 1
    if department and _is_action_text(department):
        department = None
        reduction_counts["drop_department_action_label"] += 1

    if _count_contacts_in_context(source_context) > 1 and not name:
        reduction_counts["drop_multi_contact_block_ambiguous"] += 1
        return None

    if not name and not title and not department:
        reduction_counts["drop_missing_person_and_context"] += 1
        return None

    normalized = {
        "name": name,
        "title": title,
        "department": department,
        "email": email or None,
        "email_type": str(row.get("email_type") or infer_email_type(email)),
        "phone": phone or None,
        "phone_ext": (str(row.get("phone_ext") or "").strip() or None),
        "address": row.get("address"),
        "hours": row.get("hours"),
        "source_context": source_context,
        "source_url": str(row.get("source_url") or source_url),
        "confidence": round(float(row.get("confidence") or 0.58), 3),
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
    if re.search(r"\d", candidate):
        return False
    lowered = candidate.lower()
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


def _normalize_token(value: str) -> str:
    lowered = (value or "").lower()
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
