from __future__ import annotations

from collections import deque
import re
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover - fallback for minimal environments
    BeautifulSoup = None

from src.http_client import FetchResult, fetch_url
from src.normalize import ensure_url_has_scheme, normalize_url, normalize_whitespace
from src.parsers import (
    extract_contacts,
    extract_emails,
    extract_emails_from_href,
    extract_phone_candidates,
    infer_email_type,
)

GRANICUS_DIRECTORY_PATHS = (
    "/Directory.aspx",
    "/directory.aspx",
)
GRANICUS_DIRECT_PATHS = (
    "/Directory.aspx",
    "/directory.aspx",
    "/staff-directory",
    "/directory",
    "/departments",
    "/government",
    "/town-hall",
)
GRANICUS_DID_MIN = 1
GRANICUS_DID_MAX = 25
GRANICUS_BLOCKED_STATUS_CODES = {401, 403, 429}
GRANICUS_MAX_DISCOVERED_ENTRY_PAGES = 40
GRANICUS_MAX_TOTAL_CANDIDATES = 140
GRANICUS_REQUEST_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

CONTACT_COLUMN_HINTS: dict[str, tuple[str, ...]] = {
    "name": ("name", "employee", "staff"),
    "title": ("title", "position", "role"),
    "department": ("department", "office", "division"),
    "phone": ("phone", "telephone", "tel", "additional phone"),
    "email": ("email",),
}
DIRECTORY_LABEL_TOKENS = ("name", "title", "phone", "email", "department", "staff")
ACTION_NAME_REJECTS = {"email", "contact", "details", "view", "click here"}
DEPARTMENT_HINTS = (
    "department",
    "office",
    "division",
    "town clerk",
    "tax collector",
    "assessor",
    "public works",
    "planning",
    "zoning",
    "human resources",
    "finance",
)
TITLE_HINTS = (
    "director",
    "manager",
    "administrator",
    "clerk",
    "collector",
    "assessor",
    "chief",
    "officer",
    "coordinator",
)

FetchFn = Callable[[str, str | None, dict[str, str] | None], FetchResult]


def build_granicus_candidate_urls(
    municipality_homepage: str,
    did_max: int = GRANICUS_DID_MAX,
) -> list[dict[str, str | int]]:
    base = normalize_url(ensure_url_has_scheme(municipality_homepage))
    if not base:
        return []

    root = base.rstrip("/")
    max_did = max(GRANICUS_DID_MIN, int(did_max))
    out: list[dict[str, str | int]] = []
    seen: set[str] = set()

    def add_candidate(url: str, source_kind: str, candidate_origin: str) -> None:
        normalized = normalize_url(url)
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        out.append(
            {
                "url": normalized,
                "source_kind": source_kind,
                "candidate_origin": candidate_origin,
            }
        )

    for path in GRANICUS_DIRECT_PATHS:
        source_kind = "direct_directory_page" if "directory" in path.lower() else "landing_page"
        add_candidate(f"{root}{path}", source_kind, "direct_path")

    for path in GRANICUS_DIRECTORY_PATHS:
        for did in range(GRANICUS_DID_MIN, max_did + 1):
            for param in ("did", "DID"):
                candidate = _replace_or_add_query_param(f"{root}{path}", param, str(did))
                add_candidate(candidate, "did_page", "did_enumeration")

    return out


def fetch_granicus_candidates(
    municipality_homepage: str,
    candidates: list[dict[str, str | int]],
    timeout: int = 20,
    session=None,
    request_headers: dict[str, str] | None = None,
    fetch_fn: FetchFn | None = None,
) -> list[dict[str, object]]:
    referer = normalize_url(ensure_url_has_scheme(municipality_homepage)) or municipality_homepage
    headers = {**GRANICUS_REQUEST_HEADERS, **(request_headers or {})}
    out: list[dict[str, object]] = []
    for candidate in candidates:
        request_url = str(candidate.get("url") or "")
        if not request_url:
            continue
        if fetch_fn:
            result = fetch_fn(request_url, referer, headers)
        else:
            result = fetch_url(
                request_url,
                timeout=timeout,
                session=session,
                referer=referer,
                request_headers=headers,
            )
        final_url = normalize_url(result.final_url or request_url) or (result.final_url or request_url)
        status_code = result.status_code
        blocked = status_code in GRANICUS_BLOCKED_STATUS_CODES
        out.append(
            {
                "request_url": request_url,
                "final_url": final_url,
                "status_code": status_code,
                "blocked": blocked,
                "fetch_outcome": _classify_fetch_outcome(result),
                "redirect_count": int(result.redirect_count or 0),
                "content_type": result.content_type or "",
                "response_headers": result.response_headers or {},
                "error": result.error or "",
                "source_kind": str(candidate.get("source_kind") or "unknown"),
                "candidate_origin": str(candidate.get("candidate_origin") or ""),
                "text": result.text or "",
                "page_title": _extract_html_title(result.text or ""),
            }
        )
    return out


def is_granicus_directory_page(
    html_text: str,
    url: str,
) -> tuple[bool, list[str]]:
    blob = _extract_text_blob(html_text)
    if not blob:
        return False, []

    lowered_url = (url or "").lower()
    lowered_blob = blob.lower()
    signals: list[str] = []

    if "directory.aspx" in lowered_url:
        signals.append("url:directory.aspx")
    if any(token in lowered_url for token in ("/staff-directory", "/directory")):
        signals.append("url:directory_path")
    if "staff directory" in lowered_blob:
        signals.append("text:staff_directory")
    if "return to directory" in lowered_blob or "return to staff directory" in lowered_blob:
        signals.append("text:return_to_directory")
    if "civicengage" in lowered_blob or "granicus" in lowered_blob:
        signals.append("text:civicengage")

    directory_label_hits = sum(1 for token in DIRECTORY_LABEL_TOKENS if token in lowered_blob)
    if directory_label_hits >= 4:
        signals.append("text:directory_labels")

    header_hits = _count_contact_header_hits(html_text)
    if header_hits >= 2:
        signals.append("table:contact_headers")

    strong_signal = (
        "table:contact_headers" in signals
        or "text:staff_directory" in signals
        or ("url:directory.aspx" in signals and "text:return_to_directory" in signals)
    )
    if not strong_signal and "url:directory.aspx" in signals and "text:directory_labels" in signals:
        strong_signal = True

    return strong_signal, signals


def extract_granicus_contacts(
    html_text: str,
    source_url: str,
    source_kind: str = "direct_directory_page",
) -> list[dict[str, str | float | None]]:
    extracted: list[dict[str, str | float | None]] = []
    extracted.extend(_extract_table_contacts(html_text, source_url))
    extracted.extend(_extract_department_block_contacts(html_text, source_url))
    extracted.extend(_extract_single_entry_contacts(html_text, source_url))

    # Reuse the generic extractor as a low-priority fallback if focused extraction is empty.
    if not extracted:
        extracted.extend(extract_contacts(html_text, source_url, page_type="directory_page"))

    deduped: dict[tuple[str, ...], dict[str, str | float | None]] = {}
    for row in extracted:
        normalized = _normalize_contact_row(row, source_url, source_kind)
        if normalized is None:
            continue
        key = _contact_dedupe_key(normalized)
        prior = deduped.get(key)
        deduped[key] = _merge_contacts(prior, normalized) if prior else normalized

    return sorted(
        deduped.values(),
        key=lambda item: (
            -float(item.get("confidence") or 0.0),
            str(item.get("email") or ""),
            str(item.get("name") or ""),
        ),
    )


def run_granicus_strategy_for_municipality(
    municipality_homepage: str,
    timeout: int = 20,
    did_max: int = GRANICUS_DID_MAX,
    session=None,
    request_headers: dict[str, str] | None = None,
    fetch_fn: FetchFn | None = None,
    max_total_candidates: int = GRANICUS_MAX_TOTAL_CANDIDATES,
) -> dict[str, object]:
    initial_candidates = build_granicus_candidate_urls(municipality_homepage, did_max=did_max)
    queue: deque[dict[str, str | int]] = deque(initial_candidates)
    seen_urls: set[str] = set()
    attempted_rows: list[dict[str, object]] = []
    blocked_urls: list[str] = []
    matched_urls: list[str] = []
    contacts_by_url: list[dict[str, object]] = []
    all_contacts: list[dict[str, str | float | None]] = []

    while queue and len(attempted_rows) < max_total_candidates:
        candidate = queue.popleft()
        url = normalize_url(str(candidate.get("url") or "")) or str(candidate.get("url") or "")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)

        fetch_rows = fetch_granicus_candidates(
            municipality_homepage=municipality_homepage,
            candidates=[candidate],
            timeout=timeout,
            session=session,
            request_headers=request_headers,
            fetch_fn=fetch_fn,
        )
        if not fetch_rows:
            continue
        fetch_row = fetch_rows[0]
        text = str(fetch_row.get("text") or "")
        final_url = str(fetch_row.get("final_url") or url)
        source_kind = str(fetch_row.get("source_kind") or "unknown")
        matched, detection_signals = (False, [])
        extracted_rows: list[dict[str, str | float | None]] = []

        if fetch_row.get("blocked"):
            blocked_urls.append(final_url)

        if fetch_row.get("fetch_outcome") == "fetched_parseable" and text:
            matched, detection_signals = is_granicus_directory_page(text, final_url)
            if matched or source_kind == "single_staff_entry_page":
                extracted_rows = extract_granicus_contacts(
                    text,
                    source_url=final_url,
                    source_kind=source_kind,
                )
                if extracted_rows:
                    contacts_by_url.append(
                        {
                            "url": final_url,
                            "source_kind": source_kind,
                            "contacts_extracted": len(extracted_rows),
                        }
                    )
                    all_contacts.extend(extracted_rows)

            if matched:
                matched_urls.append(final_url)
                for entry_candidate in discover_granicus_entry_candidates(
                    text,
                    base_url=final_url,
                    max_candidates=GRANICUS_MAX_DISCOVERED_ENTRY_PAGES,
                ):
                    entry_url = normalize_url(str(entry_candidate.get("url") or "")) or str(entry_candidate.get("url") or "")
                    if not entry_url or entry_url in seen_urls:
                        continue
                    queue.append(entry_candidate)

        attempted_rows.append(
            {
                "request_url": str(fetch_row.get("request_url") or url),
                "final_url": final_url,
                "status_code": fetch_row.get("status_code"),
                "fetch_outcome": fetch_row.get("fetch_outcome"),
                "blocked": bool(fetch_row.get("blocked")),
                "source_kind": source_kind,
                "candidate_origin": str(fetch_row.get("candidate_origin") or ""),
                "directory_match": matched,
                "detection_signals": ",".join(detection_signals),
                "contacts_extracted": len(extracted_rows),
                "page_title": str(fetch_row.get("page_title") or ""),
            }
        )

    deduped_contacts = _dedupe_contact_list(all_contacts)
    extraction_source_counts = _count_extraction_sources(deduped_contacts)
    attempted_urls = [str(row.get("request_url") or "") for row in attempted_rows if str(row.get("request_url") or "")]
    unique_blocked = sorted({url for url in blocked_urls if url})
    unique_matched = sorted({url for url in matched_urls if url})

    return {
        "attempted_count": len(attempted_rows),
        "candidate_urls_attempted": attempted_urls,
        "blocked_urls": unique_blocked,
        "matched_directory_urls": unique_matched,
        "attempted_rows": attempted_rows,
        "contacts_by_url": contacts_by_url,
        "contacts": deduped_contacts,
        "contacts_total": len(deduped_contacts),
        "extraction_source_counts": extraction_source_counts,
    }


def discover_granicus_entry_candidates(
    html_text: str,
    base_url: str,
    max_candidates: int = GRANICUS_MAX_DISCOVERED_ENTRY_PAGES,
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
        parsed = urlparse(href)
        path = (parsed.path or "").lower()
        if not path.endswith("directory.aspx"):
            continue
        query = {str(k).lower(): str(v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)}
        source_kind = ""
        if "did" in query:
            source_kind = "did_page"
        elif "eid" in query:
            source_kind = "single_staff_entry_page"
        if not source_kind:
            continue
        seen.add(href)
        out.append(
            {
                "url": href,
                "source_kind": source_kind,
                "candidate_origin": "discovered_entry_link",
            }
        )
        if len(out) >= max_candidates:
            break
    return out


def _replace_or_add_query_param(url: str, key: str, value: str) -> str:
    parsed = urlparse(url)
    filtered = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k.lower() != key.lower()]
    filtered.append((key, value))
    return urlunparse(parsed._replace(query=urlencode(filtered, doseq=True)))


def _classify_fetch_outcome(result: FetchResult) -> str:
    if result.status_code in GRANICUS_BLOCKED_STATUS_CODES:
        return "blocked"
    if not result.ok:
        if result.error == "http_error":
            return "http_error"
        return "fetch_error"
    if not (result.text or "").strip():
        return "fetched_empty"
    return "fetched_parseable"


def _extract_html_title(html_text: str) -> str:
    match = re.search(r"(?is)<title[^>]*>(.*?)</title>", html_text or "")
    return normalize_whitespace(match.group(1)) or "" if match else ""


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


def _count_contact_header_hits(html_text: str) -> int:
    if BeautifulSoup is None:
        return 0
    try:
        soup = BeautifulSoup(html_text or "", "html.parser")
    except Exception:
        return 0
    hits = 0
    for table in soup.find_all("table"):
        headers = _extract_table_headers(table)
        normalized_headers = [_normalize_token(header) for header in headers]
        for token in DIRECTORY_LABEL_TOKENS:
            if any(token in header for header in normalized_headers):
                hits += 1
    return hits


def _extract_table_contacts(html_text: str, source_url: str) -> list[dict[str, str | float | None]]:
    if BeautifulSoup is None:
        return []
    try:
        soup = BeautifulSoup(html_text or "", "html.parser")
    except Exception:
        return []

    out: list[dict[str, str | float | None]] = []
    for table in soup.find_all("table"):
        headers = _extract_table_headers(table)
        mapping = _map_contact_columns(headers)
        if not mapping and _count_contact_header_hits(str(table)) < 2:
            continue

        department_hint = _nearest_heading_text(table)
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
                name = _guess_person_name(values)
            if name and not _is_valid_person_name(name):
                if _looks_like_department(name) and not department:
                    department = name
                name = None
            if name and _is_action_text(name):
                name = None

            if not title and not department and _looks_like_department(row_blob):
                department = _first_department_phrase(row_blob)

            phone = str(phones[0].get("phone") or "") if phones else ""
            phone_ext = str(phones[0].get("phone_ext") or "") if phones else ""
            email = sorted(emails)[0] if emails else ""
            if not email and not phone:
                continue
            if not name and not title and not department:
                continue

            out.append(
                {
                    "name": name,
                    "title": title,
                    "department": department,
                    "email": email.lower() if email else None,
                    "email_type": infer_email_type(email),
                    "phone": phone or None,
                    "phone_ext": phone_ext or None,
                    "address": None,
                    "hours": None,
                    "source_context": row_blob[:220],
                    "source_url": source_url,
                    "confidence": 0.84,
                }
            )
    return out


def _extract_department_block_contacts(html_text: str, source_url: str) -> list[dict[str, str | float | None]]:
    lines = _extract_lines(html_text)
    if not lines:
        return []

    out: list[dict[str, str | float | None]] = []
    seen_keys: set[tuple[str, str]] = set()
    current_department: str | None = None
    for idx, line in enumerate(lines):
        if _looks_like_department(line):
            current_department = line
            continue

        nearby = " ".join(lines[max(0, idx - 1): min(len(lines), idx + 2)])
        emails = extract_emails(nearby)
        phones = extract_phone_candidates(nearby)
        if not emails and not phones:
            continue

        name = _extract_person_name_from_text(line)
        if not name and idx > 0:
            name = _extract_person_name_from_text(lines[idx - 1])
        if name and (not _is_valid_person_name(name) or _is_action_text(name)):
            name = None

        title = None
        if idx + 1 < len(lines) and _looks_like_title(lines[idx + 1]):
            title = lines[idx + 1]
        elif idx > 0 and _looks_like_title(lines[idx - 1]):
            title = lines[idx - 1]

        department = current_department
        if not department and title and _looks_like_department(title):
            department = title

        phone = str(phones[0].get("phone") or "") if phones else ""
        phone_ext = str(phones[0].get("phone_ext") or "") if phones else ""
        email = emails[0].lower() if emails else ""
        if not email and not phone:
            continue
        if not name and not title and not department:
            continue

        dedupe = (email, phone)
        if dedupe in seen_keys:
            continue
        seen_keys.add(dedupe)
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
                "source_context": nearby[:220],
                "source_url": source_url,
                "confidence": 0.66,
            }
        )
    return out


def _extract_single_entry_contacts(html_text: str, source_url: str) -> list[dict[str, str | float | None]]:
    lines = _extract_lines(html_text)
    if not lines:
        return []

    key_values: dict[str, str] = {}
    for line in lines:
        match = re.match(r"(?i)^\s*(name|title|department|phone|additional phone|email)\s*[:\-]\s*(.+)$", line)
        if not match:
            continue
        key = match.group(1).strip().lower()
        value = normalize_whitespace(match.group(2)) or ""
        if value and key not in key_values:
            key_values[key] = value

    if not key_values:
        return []
    email_candidates = extract_emails(key_values.get("email", ""))
    phone_candidates = extract_phone_candidates(
        f"{key_values.get('phone', '')} {key_values.get('additional phone', '')}"
    )
    if not email_candidates and not phone_candidates:
        return []

    name = key_values.get("name")
    if name and not _is_valid_person_name(name):
        name = None
    title = key_values.get("title")
    department = key_values.get("department")
    if not name and not title and not department:
        return []

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
            "source_context": "; ".join(f"{k}:{v}" for k, v in key_values.items())[:220],
            "source_url": source_url,
            "confidence": 0.72,
        }
    ]


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


def _map_contact_columns(headers: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    normalized = [_normalize_token(header) for header in headers]
    for field, tokens in CONTACT_COLUMN_HINTS.items():
        for idx, header in enumerate(normalized):
            if any(token in header for token in tokens):
                out[field] = idx
                break
    return out


def _safe_cell_value(values: list[str], index: int | None) -> str | None:
    if index is None:
        return None
    if index < 0 or index >= len(values):
        return None
    value = normalize_whitespace(values[index]) or ""
    return value or None


def _guess_person_name(values: list[str]) -> str | None:
    for value in values[:3]:
        candidate = _extract_person_name_from_text(value)
        if candidate:
            return candidate
    return None


def _extract_person_name_from_text(value: str) -> str | None:
    for match in re.finditer(r"\b([A-Z][a-zA-Z'`-]+(?:\s+[A-Z][a-zA-Z'`-]+){1,2})\b", value or ""):
        candidate = normalize_whitespace(match.group(1)) or ""
        if _is_valid_person_name(candidate):
            return candidate
    return None


def _is_valid_person_name(value: str) -> bool:
    candidate = normalize_whitespace(value) or ""
    if not candidate:
        return False
    lowered = candidate.lower()
    if lowered in ACTION_NAME_REJECTS:
        return False
    if any(lowered.startswith(prefix) for prefix in ("email ", "contact ", "click ", "view ")):
        return False
    if re.search(r"\d", candidate):
        return False
    if _looks_like_department(candidate):
        return False
    return re.fullmatch(r"[A-Z][a-zA-Z'`-]+(?:\s+[A-Z][a-zA-Z'`-]+){1,2}", candidate) is not None


def _is_action_text(value: str) -> bool:
    lowered = (normalize_whitespace(value) or "").lower()
    if not lowered:
        return False
    if lowered in ACTION_NAME_REJECTS:
        return True
    return any(token in lowered for token in ("email", "click", "view details", "contact"))


def _looks_like_department(value: str) -> bool:
    lowered = (normalize_whitespace(value) or "").lower()
    if not lowered:
        return False
    return any(token in lowered for token in DEPARTMENT_HINTS)


def _first_department_phrase(value: str) -> str | None:
    normalized = normalize_whitespace(value) or ""
    if not normalized:
        return None
    for token in DEPARTMENT_HINTS:
        if token in normalized.lower():
            return normalized
    return None


def _looks_like_title(value: str) -> bool:
    lowered = (normalize_whitespace(value) or "").lower()
    if not lowered or _looks_like_department(lowered):
        return False
    return any(token in lowered for token in TITLE_HINTS)


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


def _nearest_heading_text(table) -> str | None:
    heading = table.find_previous(["h1", "h2", "h3", "h4", "strong"])
    if heading is None:
        return None
    text = normalize_whitespace(heading.get_text(" ", strip=True))
    if not text or not _looks_like_department(text):
        return None
    return text


def _normalize_token(value: str) -> str:
    lowered = (value or "").lower()
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def _normalize_contact_row(
    row: dict[str, str | float | None],
    source_url: str,
    source_kind: str,
) -> dict[str, str | float | None] | None:
    email = str(row.get("email") or "").strip().lower()
    phone = str(row.get("phone") or "").strip()
    if not email and not phone:
        return None

    name = normalize_whitespace(str(row.get("name") or ""))
    if name and not _is_valid_person_name(name):
        name = None
    if name and _is_action_text(name):
        name = None

    title = normalize_whitespace(str(row.get("title") or ""))
    department = normalize_whitespace(str(row.get("department") or ""))
    if not name and department and _is_valid_person_name(department):
        # Keep precision by avoiding role/department strings promoted to names.
        name = None
    if not name and not title and not department:
        return None

    return {
        "name": name or None,
        "title": title or None,
        "department": department or None,
        "email": email or None,
        "email_type": str(row.get("email_type") or infer_email_type(email)),
        "phone": phone or None,
        "phone_ext": (str(row.get("phone_ext") or "").strip() or None),
        "address": row.get("address"),
        "hours": row.get("hours"),
        "source_context": normalize_whitespace(str(row.get("source_context") or "")),
        "source_url": str(row.get("source_url") or source_url),
        "confidence": round(float(row.get("confidence") or 0.55), 3),
        "granicus_source_kind": source_kind,
    }


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
        _normalize_token(str(row.get("source_url") or "")),
    )


def _merge_contacts(
    left: dict[str, str | float | None] | None,
    right: dict[str, str | float | None],
) -> dict[str, str | float | None]:
    if left is None:
        return dict(right)
    merged = dict(left)
    for field in ("name", "title", "department", "email", "phone", "phone_ext", "address", "hours", "source_context"):
        if not str(merged.get(field) or "").strip():
            merged[field] = right.get(field)
    if str(merged.get("email_type") or "").strip().lower() in {"", "unknown"}:
        merged["email_type"] = right.get("email_type")
    merged["confidence"] = max(float(left.get("confidence") or 0.0), float(right.get("confidence") or 0.0))
    if not str(merged.get("granicus_source_kind") or "").strip():
        merged["granicus_source_kind"] = right.get("granicus_source_kind")
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
    counts: dict[str, int] = {
        "direct_directory_page": 0,
        "did_page": 0,
        "single_staff_entry_page": 0,
        "other": 0,
    }
    for row in contacts:
        source_kind = str(row.get("granicus_source_kind") or "")
        if source_kind in counts:
            counts[source_kind] += 1
        else:
            counts["other"] += 1
    return counts
