from __future__ import annotations

import re
from typing import Iterable

from src.normalize import normalize_whitespace

EMAIL_RE = re.compile(r"\b([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})\b", re.IGNORECASE)
PHONE_RE = re.compile(
    r"(?:(?:\+?1[\s.\-]?)?\(?\b([2-9][0-9]{2})\)?[\s.\-]?([0-9]{3})[\s.\-]?([0-9]{4})(?:\s*(?:x|ext\.?)\s*\d{1,6})?)"
)
ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+[A-Z0-9][A-Z0-9\s.\-#]{2,}\b(?:Street|St|Road|Rd|Avenue|Ave|Lane|Ln|Drive|Dr|Boulevard|Blvd|Court|Ct|Way)\b[^\n,]*(?:,\s*[A-Za-z.\s]+,\s*CT(?:\s+\d{5}(?:-\d{4})?)?)?",
    re.IGNORECASE,
)

DAY_TOKENS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "mon-fri")
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
    phones: set[str] = set()
    for match in PHONE_RE.finditer(text or ""):
        phones.add(f"({match.group(1)}) {match.group(2)}-{match.group(3)}")
    return sorted(phones)


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


def guess_name(context: str) -> str | None:
    if not context:
        return None
    # Conservative heuristic: title-case two-token names on same line as email.
    for match in re.finditer(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b", context):
        candidate = normalize_whitespace(match.group(1))
        if not candidate:
            continue
        lower = candidate.lower()
        if any(token in lower for token in ("town", "department", "office", "hall", "street", "avenue")):
            continue
        return candidate
    return None


def _neighboring_lines(lines: list[str], idx: int) -> list[str]:
    out = []
    if idx > 0:
        out.append(lines[idx - 1])
    out.append(lines[idx])
    if idx + 1 < len(lines):
        out.append(lines[idx + 1])
    return out


def extract_contacts(text: str, source_url: str) -> list[dict[str, str | float | None]]:
    cleaned_text = text or ""
    lines = [normalize_whitespace(line) or "" for line in cleaned_text.splitlines()]
    lines = [line for line in lines if line]
    all_phones = extract_phones(cleaned_text)
    contacts: list[dict[str, str | float | None]] = []
    seen_emails: set[str] = set()

    for idx, line in enumerate(lines):
        for match in EMAIL_RE.finditer(line):
            email = match.group(1).strip().lower()
            if email in seen_emails:
                continue
            seen_emails.add(email)

            neighbors = _neighboring_lines(lines, idx)
            title, title_conf = guess_title(neighbors)
            phone = None
            nearby_blob = " ".join(neighbors)
            nearby_phones = extract_phones(nearby_blob)
            if nearby_phones:
                phone = nearby_phones[0]
            elif all_phones:
                phone = all_phones[0]

            department = None
            lower_blob = nearby_blob.lower()
            for hint in DEPARTMENT_HINTS:
                if hint in lower_blob:
                    department = hint.title()
                    break

            name = guess_name(line)
            email_type = infer_email_type(email)
            confidence = 0.45
            if email_type != "unknown":
                confidence += 0.15
            if phone:
                confidence += 0.1
            if title:
                confidence += 0.15 * max(title_conf, 0.5)
            if name:
                confidence += 0.1
            if department:
                confidence += 0.05
            confidence = round(min(confidence, 0.99), 3)

            contacts.append(
                {
                    "name": name,
                    "title": title,
                    "department": department,
                    "email": email,
                    "email_type": email_type,
                    "phone": phone,
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
