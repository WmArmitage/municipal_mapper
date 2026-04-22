from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import re
from urllib.parse import urljoin, urlparse, urlunparse

_SCIENTIFIC_NOTATION_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?[eE][+-]?\d+$")
_TRAILING_ZERO_DECIMAL_RE = re.compile(r"^[+-]?\d+\.0+$")


def make_id(prefix: str, *parts: str, length: int = 20) -> str:
    """Build a stable deterministic ID from ordered text parts."""
    joined = "||".join((part or "").strip().lower() for part in parts)
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}_{digest}"


def normalize_url(url: str, base_url: str | None = None) -> str | None:
    if not url:
        return None

    candidate = url.strip()
    if base_url:
        candidate = urljoin(base_url, candidate)

    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"}:
        return None
    if not parsed.netloc:
        return None

    # Normalize host casing and trim fragment/query noise for dedupe stability.
    cleaned = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        fragment="",
    )
    normalized = urlunparse(cleaned)
    if normalized.endswith("/"):
        return normalized
    return normalized


def ensure_url_has_scheme(url: str) -> str:
    value = (url or "").strip()
    if value.startswith(("http://", "https://")):
        return value
    return f"https://{value}"


def get_domain(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host or None


def normalize_whitespace(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"\s+", " ", value).strip()
    return cleaned or None


def safe_phone_str(value: object | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered in {"nan", "none", "null", "inf", "+inf", "-inf"}:
        return ""
    if _SCIENTIFIC_NOTATION_RE.fullmatch(text) or _TRAILING_ZERO_DECIMAL_RE.fullmatch(text):
        return _numeric_text_to_plain(text) or ""
    return text


def _numeric_text_to_plain(text: str) -> str:
    try:
        decimal_value = Decimal(text)
    except InvalidOperation:
        return text
    if not decimal_value.is_finite():
        return ""
    plain = format(decimal_value, "f")
    if "." in plain:
        plain = plain.rstrip("0").rstrip(".")
    return plain
