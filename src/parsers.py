from __future__ import annotations

import re
from urllib.parse import urlparse

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover - fallback for minimal environments
    BeautifulSoup = None

from src.normalize import normalize_whitespace

EMAIL_RE = re.compile(r"\b([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})\b", re.IGNORECASE)
PHONE_RE = re.compile(
    r"""
    (?P<full>
        (?:(?:\+?1[\s.\-]?)?\(?(?P<area>[2-9][0-9]{2})\)?[\s.\-]?(?P<prefix>[0-9]{3})[\s.\-]?(?P<line>[0-9]{4}))
        (?:
            \s*(?:,|;)?\s*
            (?:ext\.?|extension|x)
            \s*(?P<ext>[0-9]{1,6})
        )?
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)
ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+[A-Z0-9][A-Z0-9\s.\-#]{2,}\b(?:Street|St|Road|Rd|Avenue|Ave|Lane|Ln|Drive|Dr|Boulevard|Blvd|Court|Ct|Way)\b(?:[^\n]{0,80})",
    re.IGNORECASE,
)
TIME_RE = re.compile(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b", re.IGNORECASE)
DATE_LIKE_RE = re.compile(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", re.IGNORECASE)

DAY_TOKENS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "mon-fri")
GENERIC_NAV_TEXT = {
    "contact us",
    "phone numbers",
    "staff directory",
    "home",
    "government",
    "departments",
    "services",
    "view map",
    "click here",
    "email us",
    "hours",
    "physical address",
    "mailing address",
    "quick links",
    "popular links",
}

TITLE_HINTS = (
    "first selectman",
    "town manager",
    "town administrator",
    "mayor",
    "assessor",
    "tax collector",
    "town clerk",
    "building official",
    "zoning enforcement officer",
    "planner",
    "finance director",
    "assistant town clerk",
    "regional animal control officer",
    "human resources",
    "police chief",
    "fire chief",
)
TITLE_AS_DEPARTMENT_HINTS = {
    "assessor",
    "tax collector",
    "town clerk",
    "building official",
    "human resources",
    "treasurer",
    "registrar",
}
DEPARTMENT_ENTITY_KEYWORDS = (
    "department",
    "office",
    "agency",
    "board",
    "commission",
    "committee",
    "authority",
    "division",
    "bureau",
)
DEPARTMENT_FUNCTION_KEYWORDS = (
    "clerk",
    "assessor",
    "collector",
    "treasurer",
    "finance",
    "hr",
    "human resources",
    "public works",
    "planning",
    "zoning",
    "land use",
    "wetlands",
    "building",
    "permitting",
    "recreation",
    "library",
    "police",
    "fire",
    "emergency management",
    "registrar",
    "health",
)
DEPARTMENT_STRONG_PHRASES = (
    "inland wetlands and watercourses agency",
    "assessor",
    "tax collector",
    "building department",
    "planning and zoning commission",
    "public works department",
    "human resources",
    "town clerk",
    "board of selectmen",
)
ROLE_ONLY_DEPARTMENT_LABELS = (
    "assessor",
    "tax collector",
    "town clerk",
    "treasurer",
    "registrar",
    "human resources",
    "public works department",
    "building department",
    "planning and zoning commission",
)
ROLE_EMAIL_HINTS = {
    "bldg",
    "bldgofficial",
    "building",
    "assessor",
    "clerk",
    "tax",
    "taxcollector",
    "collector",
    "zoning",
    "planning",
    "wetlands",
    "permit",
    "office",
    "finance",
    "admin",
    "info",
    "hr",
    "humanresources",
    "police",
    "fire",
    "recreation",
}
PERSON_NAME_LEADIN_WORDS = [
    "assessor",
    "tax collector",
    "town clerk",
    "building official",
    "planner",
    "registrar",
    "treasurer",
    "director",
    "chief",
]
NAME_STOPWORDS = {
    "town",
    "department",
    "office",
    "hall",
    "street",
    "avenue",
    "usage",
    "fees",
    "information",
    "services",
    "contact",
    "board",
    "commission",
    "agency",
    "committee",
    "public",
    "works",
    "planning",
    "zoning",
    "home",
    "government",
    "hours",
    "staff",
    "directory",
    "click",
    "view",
    "will",
    "try",
    "return",
    "calls",
}
ROLE_LABEL_NAME_REJECTS = {
    "first selectman",
    "board of selectmen",
    "selectman",
    "mayor",
    "town manager",
    "town administrator",
    "town clerk",
    "assistant town clerk",
    "tax collector",
    "assessor",
    "finance director",
    "building official",
    "registrar",
    "regional animal control officer",
    "animal control officer",
}
NAME_ARTIFACT_PREFIXES = (
    "email ",
    "for ",
    "click ",
    "view ",
    "contact ",
    "call ",
)
NAME_ARTIFACT_TOKENS = {
    "email",
    "phone",
    "contact",
    "click",
    "view",
    "here",
    "learn more",
    "read more",
    "details",
}
TITLE_NEAR_NAME_HINTS = {
    "assistant",
    "director",
    "officer",
    "chief",
    "manager",
    "administrator",
    "clerk",
    "collector",
    "assessor",
    "selectman",
    "mayor",
    "finance",
    "animal control",
    "registrar",
    "planner",
}
STAFF_FRIENDLY_PAGE_TYPES = {
    "official_page",
    "department_page",
    "directory_page",
    "directory_category_page",
    "contact_page",
}

SERVICE_RULES: dict[str, dict[str, tuple[str, ...]]] = {
    "gis": {
        "anchor": ("gis", "geographic information", "parcel map", "mapping"),
        "url": ("gis", "axisgis", "parcel", "map"),
    },
    "property_cards": {
        "anchor": ("property card", "property record", "record card"),
        "url": ("property-card", "propertycard", "property-record", "record-card", "propertyrecordcards"),
    },
    "tax_payment": {
        "anchor": ("pay tax", "tax payment", "tax bill", "tax collector"),
        "url": ("tax", "mytaxbill", "pay", "collector"),
    },
    "jobs": {
        "anchor": ("jobs", "employment", "career", "human resources"),
        "url": ("jobs", "employment", "career"),
    },
    "permits": {
        "anchor": ("permit", "permitting", "building permit", "land use permit"),
        "url": ("permit", "permitting", "building", "land-use"),
    },
    "agendas_minutes": {
        "anchor": ("agenda", "minutes", "meeting minutes", "agendas"),
        "url": ("agenda", "minutes", "agendacenter", "meeting"),
    },
}
PERMIT_NEGATIVE_TOKENS = ("calendar", "agenda", "minutes", "meeting", "event")


def extract_emails(text: str) -> list[str]:
    return sorted({match.group(1).strip().lower() for match in EMAIL_RE.finditer(text or "")})


def extract_phone_candidates(text: str) -> list[dict[str, str | None]]:
    candidates: list[dict[str, str | None]] = []
    seen: set[tuple[str, str, str]] = set()
    for match in PHONE_RE.finditer(text or ""):
        normalized = _normalize_phone_match(match)
        if not normalized:
            continue
        key = (
            str(normalized["phone"] or ""),
            str(normalized["phone_ext"] or ""),
            str(normalized["source_context"] or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        candidates.append(normalized)
    return candidates


def extract_phones(text: str) -> list[str]:
    return [str(item["phone"]) for item in extract_phone_candidates(text)]


def infer_email_type(email: str | None) -> str:
    local = (email.split("@", 1)[0] if email else "").lower()
    if not local:
        return "unknown"
    if local in {"info", "admin", "contact", "office"}:
        return "role_based"
    normalized_local = re.sub(r"[^a-z0-9]+", "", local)
    if any(token in normalized_local for token in ROLE_EMAIL_HINTS):
        return "role_based"
    if re.fullmatch(r"[a-z]{2,}\.[a-z]{2,}", local):
        return "direct"
    if "." in local and not local.startswith(("info.", "admin.")):
        return "unknown"
    if re.fullmatch(r"[a-z]{2,}\d*", local):
        return "direct"
    return "unknown"


def guess_title(snippets: list[str]) -> tuple[str | None, float]:
    for snippet in snippets:
        for hint in TITLE_HINTS:
            match = re.search(rf"\b{re.escape(hint)}\b", snippet or "", flags=re.IGNORECASE)
            if match:
                return normalize_whitespace(match.group(0)), 0.85
    return None, 0.0


def _is_role_label(value: str | None) -> bool:
    normalized = _normalize_key_text(value)
    if not normalized:
        return False
    if normalized in ROLE_LABEL_NAME_REJECTS:
        return True
    return any(_keyword_in_text(normalized, role) for role in ROLE_LABEL_NAME_REJECTS)


def _normalize_contact_name(value: str | float | None) -> str | None:
    candidate = normalize_whitespace(str(value or ""))
    if not candidate:
        return None
    if not _is_name_candidate(candidate):
        return None
    return candidate


def guess_name(context: str, email: str | None = None, prefer_department: bool = False) -> str | None:
    if not context:
        return None

    role_pattern = "|".join(re.escape(word) for word in PERSON_NAME_LEADIN_WORDS)
    explicit = re.search(rf"(?:{role_pattern})\s+([A-Z][a-z]+\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", context, re.IGNORECASE)
    if explicit:
        candidate = normalize_whitespace(explicit.group(1))
        if candidate and _is_name_candidate(candidate):
            return candidate

    if prefer_department or not email:
        return None

    local = email.split("@", 1)[0].lower()
    if "." not in local and "_" not in local and "-" not in local:
        return None

    for match in re.finditer(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b", context):
        candidate = normalize_whitespace(match.group(1))
        if not candidate or not _is_name_candidate(candidate):
            continue
        if _candidate_aligns_with_email(candidate, email):
            return candidate
    return None


def guess_department(text: str) -> str | None:
    department, _ = guess_department_with_score(text)
    return department


def guess_department_with_score(text: str) -> tuple[str | None, float]:
    raw = normalize_whitespace(text)
    if not raw:
        return None, 0.0

    candidate = _clean_department_candidate(raw)
    if not candidate:
        return None, 0.0

    lowered = candidate.lower()
    if lowered in GENERIC_NAV_TEXT:
        return None, 0.0
    if any(token in lowered for token in ("http://", "https://", "www.", ".gov/", "click here")):
        return None, 0.0

    score = 0.0
    for phrase in DEPARTMENT_STRONG_PHRASES:
        if _keyword_in_text(lowered, phrase):
            score += 2.2
    for keyword in DEPARTMENT_ENTITY_KEYWORDS:
        if _keyword_in_text(lowered, keyword):
            score += 1.3
    for keyword in DEPARTMENT_FUNCTION_KEYWORDS:
        if _keyword_in_text(lowered, keyword):
            score += 1.0
    if lowered.startswith("board of "):
        score += 1.2

    compact_role = any(_keyword_in_text(lowered, token) for token in ("assessor", "clerk", "collector", "treasurer", "registrar"))
    threshold = 0.9 if compact_role and len(lowered.split()) <= 4 else 2.4
    if score < threshold:
        return None, score

    normalized = candidate.strip(" -:;,.")
    normalized = re.sub(r"(?i)^(?:email|call|contact)\s+(?:the\s+)?", "", normalized).strip(" -:;,.")
    normalized = re.sub(r"(?i)\s+staff$", "", normalized).strip(" -:;,.")
    lowered_normalized = normalized.lower()
    for role_label in ROLE_ONLY_DEPARTMENT_LABELS:
        if lowered_normalized.startswith(f"{role_label} "):
            normalized = " ".join(part.capitalize() for part in role_label.split())
            break
    if normalized.isupper():
        normalized = normalized.title()
    return normalize_whitespace(normalized), score


def extract_contacts(
    text: str,
    source_url: str,
    page_type: str | None = None,
) -> list[dict[str, str | float | None]]:
    cleaned_text = _prepare_text_for_extraction(text or "")
    lines = [normalize_whitespace(line) or "" for line in cleaned_text.splitlines()]
    lines = [line for line in lines if len(line) >= 3]
    contacts_by_key: dict[tuple[str, ...], dict[str, str | float | None]] = {}
    all_phone_candidates = extract_phone_candidates(cleaned_text)
    staff_mode = is_staff_friendly_page_type(page_type)

    if staff_mode:
        for structured in extract_table_contact_rows(text, source_url):
            _upsert_contact_candidate(contacts_by_key, structured, source_url)
        for structured in extract_structured_contact_blocks(text, source_url):
            _upsert_contact_candidate(contacts_by_key, structured, source_url)

    for idx, line in enumerate(lines):
        nearby_lines = _neighboring_lines(lines, idx, radius=2 if staff_mode else 1)
        nearby_blob = normalize_whitespace(" ".join(nearby_lines)) or line
        nearby_hours = _extract_best_hours(nearby_lines)
        nearby_address = _extract_address_from_lines(nearby_lines)

        line_emails = extract_emails(line)
        line_phones = extract_phone_candidates(line)
        nearby_phones = extract_phone_candidates(nearby_blob)

        line_department, line_dep_score = guess_department_with_score(line)
        block_department, block_dep_score = guess_department_with_score(nearby_blob)
        department = line_department or block_department
        department_score = line_dep_score if line_department else block_dep_score

        title, title_score = guess_title([line] + nearby_lines)
        if not department and title and title.lower() in TITLE_AS_DEPARTMENT_HINTS:
            department = title
            department_score = max(department_score, 1.0)

        emit_department_phone_row = bool((not line_emails) and line_department and line_phones)
        emit_staff_phone_row = bool(staff_mode and line_phones)
        if not line_emails and not emit_department_phone_row and not emit_staff_phone_row:
            continue

        emails_to_emit = line_emails if line_emails else [None]
        for email in emails_to_emit:
            phone = None
            phone_ext = None
            source_context = normalize_whitespace(line)

            if line_phones:
                phone = line_phones[0].get("phone")
                phone_ext = line_phones[0].get("phone_ext")
                source_context = line_phones[0].get("source_context") or source_context
            elif nearby_phones:
                phone = nearby_phones[0].get("phone")
                phone_ext = nearby_phones[0].get("phone_ext")
                source_context = nearby_phones[0].get("source_context") or source_context
            elif len(all_phone_candidates) == 1:
                phone = all_phone_candidates[0].get("phone")
                phone_ext = all_phone_candidates[0].get("phone_ext")
                source_context = all_phone_candidates[0].get("source_context") or source_context

            if not email and not phone:
                continue

            prefer_department = bool(department and department_score >= (2.0 if staff_mode else 1.6))
            name = guess_name(nearby_blob, email=email, prefer_department=prefer_department)
            if not email and department and not staff_mode:
                name = None
            if not name and staff_mode:
                name = _guess_name_from_lines(nearby_lines)
            if not name and staff_mode:
                leading_name = re.match(r"^\s*([A-Z][a-zA-Z'`-]+\s+[A-Z][a-zA-Z'`-]+)\b", nearby_blob or "")
                if leading_name:
                    candidate = normalize_whitespace(leading_name.group(1))
                    if candidate and _is_name_candidate(candidate):
                        name = candidate

            if name and _is_role_label(name):
                if not title:
                    title = normalize_whitespace(name)
                name = None

            nearest_title = _infer_nearest_title(nearby_lines, name=name, anchor_line=line)
            if nearest_title and (not title or _is_role_label(title)):
                title = nearest_title
            if not title and department and _is_role_label(department):
                title = department

            email_type = infer_email_type(email)
            confidence = 0.28
            if email:
                confidence += 0.25
                if email_type != "unknown":
                    confidence += 0.08
            if phone:
                confidence += 0.16
            if department:
                confidence += 0.14
                if email or phone:
                    confidence += 0.06
            if title:
                confidence += 0.08 * max(title_score, 0.5)
            if name:
                confidence += 0.07
            if staff_mode and (name or department):
                confidence += 0.05
            confidence = round(min(max(confidence, 0.2), 0.99), 3)

            _upsert_contact_candidate(
                contacts_by_key,
                {
                    "name": name,
                    "title": title,
                    "department": department,
                    "email": email,
                    "email_type": email_type,
                    "phone": phone,
                    "phone_ext": phone_ext,
                    "address": nearby_address,
                    "hours": nearby_hours,
                    "source_context": source_context,
                    "source_url": source_url,
                    "confidence": confidence,
                },
                source_url,
            )

    return sorted(
        contacts_by_key.values(),
        key=lambda row: (
            -float(row.get("confidence") or 0.0),
            str(row.get("email") or ""),
            str(row.get("name") or ""),
            str(row.get("title") or ""),
        ),
    )


def _upsert_contact_candidate(
    store: dict[tuple[str, ...], dict[str, str | float | None]],
    candidate: dict[str, str | float | None],
    source_url: str,
) -> None:
    normalized = _normalize_contact_candidate(candidate, source_url)
    email = str(normalized.get("email") or "").strip().lower()
    phone = str(normalized.get("phone") or "").strip()
    if not email and not phone:
        return

    key = _contact_candidate_key(normalized, source_url)
    existing = store.get(key)
    if existing is None:
        store[key] = normalized
        return
    store[key] = _merge_contact_candidates(existing, normalized)


def _normalize_contact_candidate(
    candidate: dict[str, str | float | None],
    source_url: str,
) -> dict[str, str | float | None]:
    name = _normalize_contact_name(candidate.get("name"))
    title = normalize_whitespace(str(candidate.get("title") or "")) or None
    department = normalize_whitespace(str(candidate.get("department") or "")) or None
    if name and _is_role_label(name):
        if not title:
            title = name
        name = None
    if title and _looks_like_person_name(title):
        title = None
    if not title and department and _is_role_label(department):
        title = department

    return {
        "name": name,
        "title": title,
        "department": department,
        "email": (str(candidate.get("email") or "").strip().lower() or None),
        "email_type": str(candidate.get("email_type") or "unknown"),
        "phone": (str(candidate.get("phone") or "").strip() or None),
        "phone_ext": (str(candidate.get("phone_ext") or "").strip() or None),
        "address": normalize_whitespace(str(candidate.get("address") or "")) or None,
        "hours": normalize_whitespace(str(candidate.get("hours") or "")) or None,
        "source_context": normalize_whitespace(str(candidate.get("source_context") or "")) or None,
        "source_url": source_url,
        "confidence": float(candidate.get("confidence") or 0.45),
    }


def _contact_candidate_key(
    candidate: dict[str, str | float | None],
    source_url: str,
) -> tuple[str, ...]:
    email = str(candidate.get("email") or "").strip().lower()
    if email:
        return ("email", email)

    name_key = _normalize_key_text(candidate.get("name"))
    title_key = _normalize_key_text(candidate.get("title") or candidate.get("department"))
    if not title_key:
        title_key = _normalize_key_text(candidate.get("phone"))
    return ("row", name_key, title_key, _normalize_key_text(source_url))


def _merge_contact_candidates(
    left: dict[str, str | float | None],
    right: dict[str, str | float | None],
) -> dict[str, str | float | None]:
    left_rich = _contact_candidate_richness(left)
    right_rich = _contact_candidate_richness(right)
    if right_rich > left_rich:
        primary, secondary = right, left
    elif left_rich > right_rich:
        primary, secondary = left, right
    else:
        primary, secondary = (right, left) if float(right.get("confidence") or 0.0) >= float(left.get("confidence") or 0.0) else (left, right)

    merged = dict(primary)
    for field in ("name", "title", "department", "email", "phone", "phone_ext", "address", "hours", "source_context"):
        if not merged.get(field):
            merged[field] = secondary.get(field)
    if merged.get("email") and (merged.get("email_type") in {None, "", "unknown"}):
        merged["email_type"] = secondary.get("email_type") or "unknown"
    merged["confidence"] = round(max(float(left.get("confidence") or 0.0), float(right.get("confidence") or 0.0)), 3)
    return merged


def _contact_candidate_richness(candidate: dict[str, str | float | None]) -> int:
    score = 0
    if candidate.get("name"):
        score += 4
    if candidate.get("title"):
        score += 4
    if candidate.get("department"):
        score += 2
    if candidate.get("email"):
        score += 5
    if candidate.get("phone"):
        score += 3
    if candidate.get("phone_ext"):
        score += 1
    if candidate.get("address"):
        score += 1
    if candidate.get("hours"):
        score += 1
    return score


def _normalize_key_text(value: str | float | None) -> str:
    lowered = str(value or "").strip().lower()
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def extract_locations(text: str, source_url: str) -> list[dict[str, str | None]]:
    cleaned_text = _prepare_text_for_extraction(text or "")
    lines = [normalize_whitespace(line) or "" for line in cleaned_text.splitlines()]
    lines = [line for line in lines if line]

    address = None
    for line in lines:
        if not _is_likely_address_line(line):
            continue
        match = ADDRESS_RE.search(line)
        if not match:
            continue
        address = normalize_address_text(match.group(0))
        if address:
            break

    hours_line = _extract_best_hours(lines)

    if not address and not hours_line:
        return []
    return [{"address": address, "hours": hours_line, "source_url": source_url}]


def classify_service_link(url: str, anchor_text: str | None = None) -> tuple[str | None, float]:
    parsed = urlparse(url or "")
    domain = (parsed.netloc or "").lower()
    path_blob = f"{domain} {parsed.path} {parsed.query}".lower()
    anchor_blob = (anchor_text or "").lower()

    if "axisgis.com" in domain:
        return "gis", 0.95
    if "propertyrecordcards.com" in domain:
        return "property_cards", 0.95
    if "mytaxbill.org" in domain:
        return "tax_payment", 0.95

    best_category = None
    best_score = 0.0
    for category, rules in SERVICE_RULES.items():
        score = 0.0
        for token in rules["anchor"]:
            if _keyword_in_text(anchor_blob, token):
                score += 1.4
        for token in rules["url"]:
            if _keyword_in_text(path_blob, token):
                score += 1.0

        if category == "permits":
            if any(_keyword_in_text(anchor_blob, bad) or _keyword_in_text(path_blob, bad) for bad in PERMIT_NEGATIVE_TOKENS):
                score -= 1.8

        if score > best_score:
            best_score = score
            best_category = category

    if not best_category:
        return None, 0.0

    if best_score < 1.8:
        return None, 0.0

    confidence = min(0.98, 0.5 + (best_score * 0.12))
    return best_category, round(confidence, 3)


def normalize_address_text(value: str | None) -> str | None:
    text = normalize_whitespace(value)
    if not text:
        return None
    text = text.replace("–", "-")
    text = re.sub(r"\s*,\s*", ", ", text)
    text = _normalize_street_suffixes(text)
    text = re.sub(r"\.(?=\s|,|$)", "", text)
    text = re.sub(r"\bconnecticut\b", "CT", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ,;")


def normalize_hours_text(value: str | None) -> str | None:
    text = normalize_whitespace(value)
    if not text:
        return None
    text = text.replace("–", "-")
    text = re.sub(r"(?i)^and\s+", "", text)
    text = re.sub(r"\s*-\s*", " - ", text)
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"\s*;\s*", "; ", text)
    text = re.sub(r"[&]+$", "", text)
    return text.strip(" ,;")


def location_dedupe_key(address: str | None, hours: str | None) -> tuple[str, str]:
    return _canonical_location_text(address), _canonical_location_text(hours)


def _normalize_phone_match(match: re.Match[str]) -> dict[str, str | None] | None:
    area = match.group("area")
    prefix = match.group("prefix")
    line = match.group("line")
    if not area or not prefix or not line:
        return None

    phone = f"{area}{prefix}{line}"
    ext = match.group("ext")
    source_context = normalize_whitespace(match.group("full"))
    return {
        "phone": phone,
        "phone_ext": ext.strip() if ext else None,
        "source_context": source_context,
    }


def _clean_department_candidate(text: str) -> str | None:
    candidate = EMAIL_RE.sub(" ", text)
    candidate = PHONE_RE.sub(" ", candidate)
    candidate = re.sub(r"[|•]+", " ", candidate)
    candidate = re.sub(r"\s+", " ", candidate).strip(" -:;,.")
    if not candidate:
        return None
    if len(candidate) < 3 or len(candidate) > 120:
        return None
    return candidate


def _is_name_candidate(candidate: str) -> bool:
    normalized = normalize_whitespace(candidate)
    if not normalized:
        return False
    lowered = normalized.lower()
    if any(lowered.startswith(prefix) for prefix in NAME_ARTIFACT_PREFIXES):
        return False
    if any(token in lowered for token in ("mailto:", "http://", "https://", "@")):
        return False
    if _is_role_label(lowered):
        return False
    if any(token in lowered for token in NAME_ARTIFACT_TOKENS):
        return False
    if any(_keyword_in_text(lowered, token) for token in NAME_STOPWORDS):
        return False
    if re.search(r"\d", normalized):
        return False
    if lowered in GENERIC_NAV_TEXT:
        return False
    return True


def _candidate_aligns_with_email(candidate: str, email: str) -> bool:
    local = (email.split("@", 1)[0] if email else "").lower().replace("_", ".").replace("-", ".")
    if "." not in local:
        return False
    parts = [part for part in local.split(".") if part]
    if len(parts) < 2:
        return False

    name_parts = [part.lower() for part in candidate.split() if part]
    if len(name_parts) < 2:
        return False
    return name_parts[0].startswith(parts[0]) and name_parts[-1].startswith(parts[-1])


def is_staff_friendly_page_type(page_type: str | None) -> bool:
    return str(page_type or "").strip().lower() in STAFF_FRIENDLY_PAGE_TYPES


def extract_table_contact_rows(text: str, source_url: str) -> list[dict[str, str | float | None]]:
    raw = text or ""
    if "<" not in raw or ">" not in raw or BeautifulSoup is None:
        return []

    soup = BeautifulSoup(raw, "html.parser")
    out: list[dict[str, str | float | None]] = []
    seen_keys: set[tuple[str, str, str, str, str]] = set()

    for table in soup.find_all("table"):
        table_headers = _extract_table_headers(table)
        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) < 2:
                continue
            if row.find_all("th") and not row.find_all("td"):
                continue
            parts = [normalize_whitespace(cell.get_text(" ", strip=True)) or "" for cell in cells]
            parts = [part for part in parts if part]
            if not parts:
                continue

            row_blob = " | ".join(parts)
            mailto_emails: list[str] = []
            for anchor in row.find_all("a", href=True):
                href = str(anchor.get("href") or "")
                if href.lower().startswith("mailto:"):
                    email = href.split(":", 1)[1].split("?", 1)[0].strip().lower()
                    if EMAIL_RE.fullmatch(email):
                        mailto_emails.append(email)
            emails = sorted({*extract_emails(row_blob), *mailto_emails})
            phones = extract_phone_candidates(row_blob)
            if not emails and not phones:
                continue

            mapped = _map_table_cells(table_headers, parts)

            name = mapped.get("name") or _guess_name_from_lines(parts[:3])
            title = mapped.get("title")
            department = mapped.get("department")
            if not title:
                title = _infer_nearest_title(parts[:4], name=name, anchor_line=parts[0]) or guess_title(parts[:4])[0]
            if not department:
                department, dep_score = guess_department_with_score(" ".join(parts[:5]))
                if not title and department and dep_score >= 1.0:
                    title = department

            email = (mapped.get("email") or (emails[0].lower() if emails else None))
            phone = (str(mapped.get("phone") or "").strip() or None)
            phone_ext = (str(mapped.get("phone_ext") or "").strip() or None)
            if not phone:
                phone = str(phones[0].get("phone")) if phones and phones[0].get("phone") else None
            if not phone_ext:
                phone_ext = str(phones[0].get("phone_ext")) if phones and phones[0].get("phone_ext") else None
            address = _extract_address_from_lines(parts)
            hours = _extract_best_hours(parts)
            source_context = str(phones[0].get("source_context")) if phones and phones[0].get("source_context") else row_blob[:180]
            email_type = infer_email_type(email)

            dedupe_key = (
                (email or "").lower(),
                phone or "",
                (name or "").strip().lower(),
                (title or "").strip().lower(),
                (department or "").strip().lower(),
            )
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)

            confidence = 0.58
            if email:
                confidence += 0.14
            if phone:
                confidence += 0.12
            if name:
                confidence += 0.08
            if title:
                confidence += 0.06
            if department:
                confidence += 0.06
            confidence = round(min(confidence, 0.97), 3)

            out.append(
                {
                    "name": name,
                    "title": title,
                    "department": department,
                    "email": email,
                    "email_type": email_type,
                    "phone": phone,
                    "phone_ext": phone_ext,
                    "address": address,
                    "hours": hours,
                    "source_context": source_context,
                    "source_url": source_url,
                    "confidence": confidence,
                }
            )

    return out


def extract_structured_contact_blocks(text: str, source_url: str) -> list[dict[str, str | float | None]]:
    raw = text or ""
    if "<" not in raw or ">" not in raw or BeautifulSoup is None:
        return []

    soup = BeautifulSoup(raw, "html.parser")
    blocks = soup.find_all(["tr", "li", "article", "section", "div", "p"])
    out: list[dict[str, str | float | None]] = []
    seen_block_keys: set[tuple[str, str, str, str, str]] = set()

    for block in blocks:
        snippet = normalize_whitespace(block.get_text("\n", strip=True))
        if not snippet or len(snippet) < 12:
            continue
        mailto_emails: list[str] = []
        for anchor in block.find_all("a", href=True):
            href = str(anchor.get("href") or "")
            if href.lower().startswith("mailto:"):
                email = href.split(":", 1)[1].split("?", 1)[0].strip().lower()
                if EMAIL_RE.fullmatch(email):
                    mailto_emails.append(email)
        emails = sorted({*extract_emails(snippet), *mailto_emails})
        phones = extract_phone_candidates(snippet)
        if not emails and not phones:
            continue

        lines = [normalize_whitespace(line) or "" for line in snippet.splitlines()]
        lines = [line for line in lines if line and line.lower() not in GENERIC_NAV_TEXT]
        if not lines:
            continue

        name = _guess_name_from_lines(lines)
        title = _guess_title_from_lines(lines) or guess_title(lines[:4])[0]
        department, dep_score = guess_department_with_score(" ".join(lines[:5]))
        if not title and department and dep_score >= 1.0:
            title = department

        phone = str(phones[0].get("phone")) if phones and phones[0].get("phone") else None
        phone_ext = str(phones[0].get("phone_ext")) if phones and phones[0].get("phone_ext") else None
        source_context = str(phones[0].get("source_context")) if phones and phones[0].get("source_context") else snippet[:180]
        email = emails[0].lower() if emails else None
        email_type = infer_email_type(email)
        address = _extract_address_from_lines(lines)
        hours = _extract_best_hours(lines)

        block_key = (
            (email or "").lower(),
            phone or "",
            (name or "").strip().lower(),
            (title or "").strip().lower(),
            (department or "").strip().lower(),
        )
        if block_key in seen_block_keys:
            continue
        seen_block_keys.add(block_key)

        confidence = 0.52
        if email:
            confidence += 0.14
        if phone:
            confidence += 0.12
        if name:
            confidence += 0.08
        if title:
            confidence += 0.06
        if department:
            confidence += 0.06
        confidence = round(min(confidence, 0.95), 3)

        out.append(
            {
                "name": name,
                "title": title,
                "department": department,
                "email": email,
                "email_type": email_type,
                "phone": phone,
                "phone_ext": phone_ext,
                "address": address,
                "hours": hours,
                "source_context": source_context,
                "source_url": source_url,
                "confidence": confidence,
            }
        )

    return out


def _guess_name_from_lines(lines: list[str]) -> str | None:
    for line in lines[:3]:
        candidate = normalize_whitespace(line)
        if not candidate:
            continue
        if len(candidate) > 60 or re.search(r"\d", candidate):
            continue
        if candidate.lower() in GENERIC_NAV_TEXT:
            continue
        if _looks_like_person_name(candidate):
            normalized = _normalize_contact_name(candidate)
            if normalized:
                return normalized
    return None


def _guess_title_from_lines(lines: list[str]) -> str | None:
    for line in lines[:4]:
        candidate = normalize_whitespace(line)
        if not candidate:
            continue
        if len(candidate) > 80:
            continue
        lowered = candidate.lower()
        if lowered in GENERIC_NAV_TEXT:
            continue
        if _looks_like_person_name(candidate):
            continue
        if _looks_like_title_label(candidate) or guess_department(candidate):
            return candidate
    return None


def _infer_nearest_title(
    lines: list[str],
    name: str | None,
    anchor_line: str | None = None,
) -> str | None:
    if not lines:
        return None
    normalized_lines = [normalize_whitespace(line) or "" for line in lines]
    anchor_idx = 0
    if anchor_line:
        anchor_norm = normalize_whitespace(anchor_line) or ""
        if anchor_norm in normalized_lines:
            anchor_idx = normalized_lines.index(anchor_norm)

    name_idx = None
    if name:
        normalized_name = normalize_whitespace(name) or ""
        for idx, line in enumerate(normalized_lines):
            if line == normalized_name:
                name_idx = idx
                break

    ordered: list[tuple[int, int]] = []
    for idx, line in enumerate(normalized_lines):
        if not line:
            continue
        if name and line == (normalize_whitespace(name) or ""):
            continue
        if not _looks_like_title_label(line):
            continue
        distance_anchor = abs(idx - anchor_idx)
        distance_name = abs(idx - name_idx) if name_idx is not None else distance_anchor
        ordered.append((distance_name * 10 + distance_anchor, idx))

    if not ordered:
        return None
    ordered.sort(key=lambda item: item[0])
    return normalized_lines[ordered[0][1]]


def _looks_like_title_label(value: str) -> bool:
    candidate = normalize_whitespace(value)
    if not candidate:
        return False
    lowered = candidate.lower()
    if lowered in GENERIC_NAV_TEXT:
        return False
    if any(token in lowered for token in ("mailto:", "@", "http://", "https://")):
        return False
    if PHONE_RE.search(candidate):
        return False
    if _looks_like_person_name(candidate):
        return False
    if _is_role_label(candidate):
        return True
    if guess_department(candidate):
        return True
    return any(_keyword_in_text(lowered, token) for token in TITLE_NEAR_NAME_HINTS)


def _extract_table_headers(table) -> list[str]:
    if table is None:
        return []
    for row in table.find_all("tr"):
        headers = row.find_all("th")
        if not headers:
            continue
        out = [normalize_whitespace(cell.get_text(" ", strip=True) or "") or "" for cell in headers]
        out = [item for item in out if item]
        if out:
            return out
    return []


def _map_table_cells(headers: list[str], values: list[str]) -> dict[str, str | None]:
    if not headers:
        return {"name": None, "title": None, "department": None, "email": None, "phone": None, "phone_ext": None}
    out: dict[str, str | None] = {"name": None, "title": None, "department": None, "email": None, "phone": None, "phone_ext": None}
    mapped_headers = [(_normalize_keyword_blob(header), idx) for idx, header in enumerate(headers)]
    for normalized_header, idx in mapped_headers:
        if idx >= len(values):
            continue
        value = normalize_whitespace(values[idx]) or None
        if not value:
            continue
        if any(token in normalized_header for token in ("name", "employee")):
            out["name"] = out["name"] or value
        elif any(token in normalized_header for token in ("title", "position", "role")):
            out["title"] = out["title"] or value
        elif any(token in normalized_header for token in ("department", "office", "division")):
            out["department"] = out["department"] or value
        elif "email" in normalized_header:
            emails = extract_emails(value)
            if emails:
                out["email"] = out["email"] or emails[0]
        elif any(token in normalized_header for token in ("phone", "telephone", "tel")):
            phones = extract_phone_candidates(value)
            if phones:
                out["phone"] = out["phone"] or str(phones[0].get("phone") or "")
                out["phone_ext"] = out["phone_ext"] or str(phones[0].get("phone_ext") or "")
    return out


def _looks_like_person_name(value: str) -> bool:
    candidate = normalize_whitespace(value)
    if not candidate:
        return False
    if not re.fullmatch(r"[A-Z][a-zA-Z'`-]+(?:\s+[A-Z][a-zA-Z'`-]+){1,2}", candidate):
        return False
    return _is_name_candidate(candidate)


def _prepare_text_for_extraction(text: str) -> str:
    raw = text or ""
    if "<" not in raw or ">" not in raw:
        return raw

    if BeautifulSoup is None:
        def mailto_replacer(match: re.Match[str]) -> str:
            email = (match.group("email") or "").strip().lower()
            anchor_text = normalize_whitespace(match.group("label") or "") or ""
            if not EMAIL_RE.fullmatch(email):
                return anchor_text
            return f"{anchor_text} {email}".strip()

        raw_with_mailto = re.sub(
            r"(?is)<a\b[^>]*href=[\"']mailto:(?P<email>[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})(?:\?[^\"'>]*)?[\"'][^>]*>(?P<label>.*?)</a>",
            mailto_replacer,
            raw,
        )
        without_assets = re.sub(r"(?is)<(script|style|noscript)\b[^>]*>.*?</\1>", " ", raw_with_mailto)
        return re.sub(r"(?s)<[^>]+>", "\n", without_assets)

    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "")
        if not href.lower().startswith("mailto:"):
            continue
        email = href.split(":", 1)[1].split("?", 1)[0].strip()
        if EMAIL_RE.fullmatch(email):
            anchor.append(f" {email}")
    return soup.get_text("\n")


def _neighboring_lines(lines: list[str], idx: int, radius: int = 1) -> list[str]:
    start = max(0, idx - radius)
    end = min(len(lines), idx + radius + 1)
    return lines[start:end]


def _canonical_location_text(value: str | None) -> str:
    if not value:
        return ""
    lowered = value.lower()
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def _keyword_in_text(blob: str, keyword: str) -> bool:
    normalized_blob = _normalize_keyword_blob(blob)
    normalized_keyword = _normalize_keyword_blob(keyword)
    if not normalized_blob or not normalized_keyword:
        return False
    if " " in normalized_keyword:
        return normalized_keyword in normalized_blob
    return re.search(rf"\b{re.escape(normalized_keyword)}\b", normalized_blob) is not None


def _normalize_keyword_blob(value: str | None) -> str:
    lowered = (value or "").lower()
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def _extract_address_from_lines(lines: list[str]) -> str | None:
    for line in lines:
        normalized = normalize_whitespace(line)
        if not normalized:
            continue
        match = ADDRESS_RE.search(normalized)
        if not match:
            continue
        address = normalize_address_text(match.group(0))
        if address:
            return address
    return None


def _is_likely_address_line(line: str) -> bool:
    lowered = line.lower()
    if len(line) < 8 or len(line) > 120:
        return False
    if any(token in lowered for token in ("ext.", "extension", "hours", "board", "committee", "report", "plan")):
        return False
    if not ADDRESS_RE.search(line):
        return False
    if DATE_LIKE_RE.search(line):
        return False
    return True


def _is_likely_hours_line(line: str) -> bool:
    lowered = line.lower()
    if len(line) > 160:
        return False
    if any(token in lowered for token in ("closed", "appointment", "please note", "holiday schedule")):
        return False
    if "hours" in lowered and TIME_RE.search(line):
        return True
    if any(token in lowered for token in DAY_TOKENS) and len(TIME_RE.findall(line)) >= 2:
        return True
    return False


def _extract_best_hours(lines: list[str]) -> str | None:
    candidates: list[str] = []
    for idx, line in enumerate(lines):
        lowered = line.lower()
        if "hours" in lowered:
            chunk = [line]
            for offset in range(1, 4):
                next_idx = idx + offset
                if next_idx >= len(lines):
                    break
                nxt = lines[next_idx]
                nxt_lower = nxt.lower()
                if len(nxt) > 160:
                    break
                if TIME_RE.search(nxt) or any(token in nxt_lower for token in DAY_TOKENS):
                    chunk.append(nxt)
                    continue
                break
            candidates.append(" ".join(chunk))
        if any(token in lowered for token in DAY_TOKENS) and TIME_RE.search(line):
            if lowered.startswith("and ") and idx > 0:
                prev = lines[idx - 1]
                prev_lower = prev.lower()
                if TIME_RE.search(prev) or any(token in prev_lower for token in DAY_TOKENS):
                    candidates.append(f"{prev} {line}")
            candidates.append(line)

    ranked: list[tuple[int, str]] = []
    for candidate in candidates:
        normalized = normalize_hours_text(candidate)
        if not normalized:
            continue
        score = 0
        score += len(TIME_RE.findall(normalized)) * 2
        score += sum(1 for token in DAY_TOKENS if token in normalized.lower())
        if "hours" in normalized.lower():
            score += 2
        if normalized.lower().startswith("and "):
            score -= 2
        if len(normalized) < 14:
            score -= 1
        ranked.append((score, normalized))

    if not ranked:
        return None
    ranked.sort(key=lambda item: (item[0], len(item[1])), reverse=True)
    best_score, best_candidate = ranked[0]
    if best_score <= 0:
        return None
    return best_candidate


def _normalize_street_suffixes(text: str) -> str:
    suffix_rules = (
        (r"\bave\.?\b", "Avenue"),
        (r"\bst\.?\b", "Street"),
        (r"\brd\.?\b", "Road"),
        (r"\bdr\.?\b", "Drive"),
        (r"\bln\.?\b", "Lane"),
        (r"\bblvd\.?\b", "Boulevard"),
    )
    out = text
    for pattern, replacement in suffix_rules:
        out = re.sub(pattern, replacement, out, flags=re.IGNORECASE)
    return out
