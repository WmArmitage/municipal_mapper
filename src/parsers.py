from __future__ import annotations

import re
from typing import Iterable

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover - exercised only in minimal environments
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
    r"\b\d{1,6}\s+[A-Z0-9][A-Z0-9\s.\-#]{2,}\b(?:Street|St|Road|Rd|Avenue|Ave|Lane|Ln|Drive|Dr|Boulevard|Blvd|Court|Ct|Way)\b[^\n,]*(?:,\s*[A-Za-z.\s]+,\s*CT(?:\s+\d{5}(?:-\d{4})?)?)?",
    re.IGNORECASE,
)

DAY_TOKENS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "mon-fri")
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
    "planning and zoning commission",
    "public works department",
    "human resources",
    "tax collector",
    "town clerk",
    "board of selectmen",
    "building department",
)
DEPARTMENT_REJECT_EXACT = {
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
TITLE_HINTS = [
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
]
DEPARTMENT_HINTS = [
    "assessor",
    "clerk",
    "finance",
    "tax",
    "building",
    "zoning",
    "land use",
    "planning",
    "human resources",
    "police",
    "fire",
    "public works",
    "parks",
    "recreation",
]
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
    "will",
    "try",
    "email",
    "phone",
    "call",
    "here",
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
ROLE_ONLY_DEPARTMENT_LABELS = (
    "assessor",
    "tax collector",
    "town clerk",
    "treasurer",
    "registrar",
    "registrar of voters",
    "human resources",
    "public works department",
    "building department",
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

SERVICE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "gis": ("gis", "mapping", "parcel viewer", "axisgis", "geographic information"),
    "property_cards": ("property card", "property record", "record card", "vision appraisal"),
    "tax_payment": ("tax payment", "pay tax", "tax bill", "tax collector", "mytaxbill"),
    "jobs": ("jobs", "employment", "career", "human resources"),
    "permits": ("permit", "permits", "zoning", "building department", "land use"),
    "agendas_minutes": ("agenda", "minutes", "meeting archive", "meeting minutes"),
}


def extract_emails(text: str) -> list[str]:
    return sorted({match.group(1).strip().lower() for match in EMAIL_RE.finditer(text or "")})


def extract_phones(text: str) -> list[str]:
    phones: list[str] = []
    seen: set[str] = set()
    for match in PHONE_RE.finditer(text or ""):
        phone_data = _normalize_phone_match(match)
        if not phone_data:
            continue
        phone = str(phone_data["phone"])
        if phone in seen:
            continue
        seen.add(phone)
        phones.append(phone)
    return phones


def extract_phone_candidates(text: str) -> list[dict[str, str | None]]:
    """Return normalized phone/ext plus the original matched snippet."""
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


def infer_email_type(email: str) -> str:
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


def guess_title(snippets: Iterable[str]) -> tuple[str | None, float]:
    for snippet in snippets:
        lower = (snippet or "").lower()
        for hint in TITLE_HINTS:
            if hint in lower:
                return normalize_whitespace(hint.title()), 0.8
    return None, 0.0


def guess_name(context: str, email: str | None = None, prefer_department: bool = False) -> str | None:
    if not context:
        return None

    # Highest-confidence path for department-heavy lines: role + person pattern.
    role_pattern = "|".join(re.escape(word) for word in PERSON_NAME_LEADIN_WORDS)
    explicit = re.search(rf"(?:{role_pattern})\s+([A-Z][a-z]+\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", context, re.IGNORECASE)
    if explicit:
        candidate = normalize_whitespace(explicit.group(1))
        if candidate and _is_name_candidate(candidate):
            return candidate

    if prefer_department:
        return None

    for match in re.finditer(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b", context):
        candidate = normalize_whitespace(match.group(1))
        if not candidate or not _is_name_candidate(candidate):
            continue
        if email and not _candidate_aligns_with_email(candidate, email):
            # In email-driven parsing, names that do not align with email local part
            # are often section labels; skip them to reduce noise.
            continue
        return candidate
    return None


def _neighboring_lines(lines: list[str], idx: int, radius: int = 2) -> list[str]:
    start = max(0, idx - radius)
    end = min(len(lines), idx + radius + 1)
    return lines[start:end]


def extract_contacts(text: str, source_url: str) -> list[dict[str, str | float | None]]:
    cleaned_text = _prepare_contact_text(text or "")
    lines = [normalize_whitespace(line) or "" for line in cleaned_text.splitlines()]
    lines = [line for line in lines if line]
    all_phone_candidates = extract_phone_candidates(cleaned_text)
    contacts: list[dict[str, str | float | None]] = []
    seen_rows: set[tuple[str, str, str, str]] = set()

    for idx, line in enumerate(lines):
        neighbors = _neighboring_lines(lines, idx)
        nearby_blob = normalize_whitespace(" ".join(neighbors)) or line

        line_emails = extract_emails(line)
        block_emails = extract_emails(nearby_blob)
        line_phone_candidates = extract_phone_candidates(line)
        nearby_phone_candidates = extract_phone_candidates(nearby_blob)

        line_department, line_department_score = guess_department_with_score(line)
        block_department, block_department_score = guess_department_with_score(nearby_blob)
        department = line_department or block_department
        department_score = line_department_score if line_department else block_department_score
        title, title_conf = guess_title(neighbors)
        if not department and title and title.lower() in TITLE_AS_DEPARTMENT_HINTS:
            department = title
            department_score = max(department_score, 1.0)

        should_process = bool(line_emails)
        if not should_process and line_phone_candidates and line_department:
            should_process = True
        if not should_process and line_department and block_emails:
            should_process = True
        if not should_process:
            continue

        emails_to_emit = line_emails if line_emails else [None]
        if not line_emails and line_department and block_emails:
            emails_to_emit = [block_emails[0]]

        for email in emails_to_emit:
            phone = None
            phone_ext = None
            source_context = normalize_whitespace(line)

            if line_phone_candidates:
                phone = line_phone_candidates[0].get("phone")
                phone_ext = line_phone_candidates[0].get("phone_ext")
                source_context = line_phone_candidates[0].get("source_context") or source_context
            elif nearby_phone_candidates:
                phone = nearby_phone_candidates[0].get("phone")
                phone_ext = nearby_phone_candidates[0].get("phone_ext")
                source_context = nearby_phone_candidates[0].get("source_context") or source_context
            elif len(all_phone_candidates) == 1:
                phone = all_phone_candidates[0].get("phone")
                phone_ext = all_phone_candidates[0].get("phone_ext")
                source_context = all_phone_candidates[0].get("source_context") or source_context

            # Conservative emission:
            # - email-only rows are allowed
            # - department + phone rows are allowed
            # - noisy phone-only rows are rejected
            if not email and not department:
                continue
            if not email and not phone:
                continue

            prefer_department = bool(department and department_score >= 2.5)
            name = guess_name(nearby_blob, email=email, prefer_department=prefer_department)
            email_type = infer_email_type(email) if email else "unknown"

            confidence = 0.3
            if email:
                confidence += 0.22
            if email and email_type != "unknown":
                confidence += 0.08
            if phone:
                confidence += 0.15
            if title:
                confidence += 0.12 * max(title_conf, 0.5)
            if name:
                confidence += 0.1
            if department:
                confidence += 0.12
                if email or phone:
                    confidence += 0.08
            elif department_score > 0:
                confidence -= 0.05

            confidence = round(min(max(confidence, 0.2), 0.99), 3)
            dedupe_key = (
                (email or "").lower(),
                phone or "",
                (department or "").lower(),
                source_url,
            )
            if dedupe_key in seen_rows:
                continue
            seen_rows.add(dedupe_key)

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
    cleaned_text = text or ""
    address_match = ADDRESS_RE.search(cleaned_text)
    address = normalize_whitespace(address_match.group(0)) if address_match else None

    hours_line = None
    for raw_line in cleaned_text.splitlines():
        line = normalize_whitespace(raw_line)
        if not line:
            continue
        lower = line.lower()
        if "hours" in lower or any(token in lower for token in DAY_TOKENS):
            hours_line = line
            break

    if not address and not hours_line:
        return []

    return [{"address": address, "hours": hours_line, "source_url": source_url}]


def classify_service_link(url: str, anchor_text: str | None = None) -> tuple[str | None, float]:
    blob = f"{url or ''} {anchor_text or ''}".lower()
    best_category = None
    best_score = 0.0
    for category, keywords in SERVICE_KEYWORDS.items():
        score = 0.0
        for keyword in keywords:
            if _keyword_in_text(blob, keyword):
                score += 1.0
        if score > best_score:
            best_score = score
            best_category = category
    if not best_category:
        return None, 0.0
    # Convert raw keyword hits into a normalized confidence bound.
    confidence = min(0.5 + (best_score * 0.15), 0.98)
    return best_category, round(confidence, 3)


def _keyword_in_text(blob: str, keyword: str) -> bool:
    if " " in keyword:
        return keyword in blob
    return re.search(rf"\b{re.escape(keyword)}\b", blob) is not None


def _normalize_phone_match(match: re.Match[str]) -> dict[str, str | None] | None:
    area = match.group("area")
    prefix = match.group("prefix")
    line = match.group("line")
    if not area or not prefix or not line:
        return None

    phone = f"{area}{prefix}{line}"
    ext = match.group("ext")
    normalized_ext = ext.strip() if ext else None
    source_context = normalize_whitespace(match.group("full"))

    return {
        "phone": phone,
        "phone_ext": normalized_ext,
        "source_context": source_context,
    }


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
    lower = candidate.lower()
    if lower in DEPARTMENT_REJECT_EXACT:
        return None, 0.0
    if any(token in lower for token in ("http://", "https://", "www.", ".gov/", "click here")):
        return None, 0.0

    score = 0.0
    for phrase in DEPARTMENT_STRONG_PHRASES:
        if phrase in lower:
            score += 3.0
    for keyword in DEPARTMENT_ENTITY_KEYWORDS:
        if _keyword_in_text(lower, keyword):
            score += 1.8
    for keyword in DEPARTMENT_FUNCTION_KEYWORDS:
        if _keyword_in_text(lower, keyword):
            score += 1.2
    if lower.startswith("board of "):
        score += 1.5
    if " and " in lower and score > 0:
        score += 0.25

    # Allow compact department labels like "Assessor" or "Town Clerk".
    compact_allowed = any(
        _keyword_in_text(lower, token) for token in ("assessor", "clerk", "collector", "treasurer", "registrar")
    ) and len(lower.split()) <= 6
    threshold = 1.0 if compact_allowed else 2.8
    if score < threshold:
        return None, score

    normalized = candidate.strip(" -:;,.")
    if not normalized:
        return None, score
    lowered_normalized = normalized.lower()
    for role_label in ROLE_ONLY_DEPARTMENT_LABELS:
        if lowered_normalized.startswith(f"{role_label} "):
            normalized = " ".join(part.capitalize() for part in role_label.split())
            break
    if normalized.isupper():
        normalized = normalized.title()
    return normalized, score


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
    lower = candidate.lower()
    if any(token in lower for token in NAME_STOPWORDS):
        return False
    # Reject all-uppercase abbreviations and mixed non-name tokens.
    if re.search(r"\d", candidate):
        return False
    return True


def _candidate_aligns_with_email(candidate: str, email: str) -> bool:
    local = (email.split("@", 1)[0] if email else "").lower().replace("_", ".")
    if not local or "." not in local:
        return False
    name_tokens = [part.lower() for part in candidate.split() if part]
    if len(name_tokens) < 2:
        return False
    return all(token in local for token in name_tokens[:2])


def _prepare_contact_text(text: str) -> str:
    """Convert HTML-heavy content into cleaner visible text for contact parsing."""
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
        if not EMAIL_RE.fullmatch(email):
            continue
        anchor.append(f" {email}")
    return soup.get_text("\n")
