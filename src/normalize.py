from __future__ import annotations

import hashlib
import re
from urllib.parse import urljoin, urlparse, urlunparse


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

