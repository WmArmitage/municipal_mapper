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
    "assessor",
    "clerk",
    "tax",
    "taxcollector",
    "zoning",
    "planning",
    "building",
    "permit",
    "admin",
    "info",
    "hr",
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
    if any(token in local for token in ROLE_EMAIL_HINTS):
        return "department"
    if "." in local and not local.startswith(("info.", "admin.")):
        return "direct"
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


def extract_contacts(text: str, source_url: str) -> list[dict[str, str | float | None]]:
    cleaned_text = _prepare_text_for_extraction(text or "")
    lines = [normalize_whitespace(line) or "" for line in cleaned_text.splitlines()]
    lines = [line for line in lines if len(line) >= 3]
    contacts: list[dict[str, str | float | None]] = []
    seen_keys: set[tuple[str, str, str]] = set()
    all_phone_candidates = extract_phone_candidates(cleaned_text)

    for idx, line in enumerate(lines):
        nearby_lines = _neighboring_lines(lines, idx, radius=1)
        nearby_blob = normalize_whitespace(" ".join(nearby_lines)) or line

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
        if not line_emails and not emit_department_phone_row:
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

            prefer_department = bool(department and department_score >= 1.6)
            name = guess_name(nearby_blob, email=email, prefer_department=prefer_department)
            if not email and department:
                name = None

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
            confidence = round(min(max(confidence, 0.2), 0.99), 3)

            dedupe_key = ((email or "").lower(), source_url, phone or "")
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)

            contacts.append(
                {
                    "name": name,
                    "title": title,
                    "department": department,
                    "email": email,
                    "email_type": email_type,
                    "phone": phone,
                    "phone_ext": phone_ext,
                    "source_context": source_context,
                    "source_url": source_url,
                    "confidence": confidence,
                }
            )

    return contacts


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

    hours_line = None
    for line in lines:
        if _is_likely_hours_line(line):
            hours_line = normalize_hours_text(line)
            break

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
    text = re.sub(r"\bconnecticut\b", "CT", text, flags=re.IGNORECASE)
    return text.strip(" ,;")


def normalize_hours_text(value: str | None) -> str | None:
    text = normalize_whitespace(value)
    if not text:
        return None
    text = text.replace("–", "-")
    text = re.sub(r"(?i)^and\s+", "", text)
    text = re.sub(r"\s*-\s*", " - ", text)
    text = re.sub(r"\s*,\s*", ", ", text)
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
    lowered = candidate.lower()
    if any(_keyword_in_text(lowered, token) for token in NAME_STOPWORDS):
        return False
    if re.search(r"\d", candidate):
        return False
    if candidate.lower() in GENERIC_NAV_TEXT:
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


def _prepare_text_for_extraction(text: str) -> str:
    raw = text or ""
    if "<" not in raw or ">" not in raw:
        return raw

    if BeautifulSoup is None:
        raw_with_mailto = re.sub(r"(?i)mailto:([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})", r" \1 ", raw)
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
    if " " in keyword:
        return keyword in blob
    return re.search(rf"\b{re.escape(keyword)}\b", blob) is not None


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
