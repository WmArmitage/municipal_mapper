from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from urllib.parse import urlparse

import requests

from src.normalize import ensure_url_has_scheme, normalize_url

USER_AGENT = (
    "municipal-mapper/0.1 (+https://github.com/local/municipal_mapper; "
    "contact: local-dev)"
)
DEFAULT_TIMEOUT = 20
SUPPORTED_CONTENT_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "application/xml",
    "text/xml",
    "text/plain",
}


@dataclass(slots=True)
class FetchResult:
    request_url: str
    final_url: str | None
    status_code: int | None
    content_type: str | None
    text: str | None
    error: str | None
    elapsed_ms: int

    @property
    def ok(self) -> bool:
        return self.error is None and self.status_code is not None and 200 <= self.status_code < 400


def candidate_sitemap_urls(website_url: str) -> list[str]:
    root = ensure_url_has_scheme(website_url).rstrip("/")
    paths = ("/sitemap", "/sitemap.xml", "/site-map")
    urls: list[str] = []
    for path in paths:
        candidate = normalize_url(f"{root}{path}")
        if candidate and candidate not in urls:
            urls.append(candidate)
    return urls


def fetch_url(
    url: str,
    timeout: int = DEFAULT_TIMEOUT,
    session: requests.Session | None = None,
) -> FetchResult:
    target = normalize_url(ensure_url_has_scheme(url))
    if not target:
        return FetchResult(url, None, None, None, None, "invalid_url", 0)

    client = session or requests.Session()
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xml,text/xml,text/plain,*/*;q=0.1",
    }

    start = perf_counter()
    try:
        response = client.get(target, headers=headers, timeout=timeout, allow_redirects=True)
    except requests.RequestException as exc:
        elapsed_ms = int((perf_counter() - start) * 1000)
        return FetchResult(target, None, None, None, None, f"request_error:{exc}", elapsed_ms)

    elapsed_ms = int((perf_counter() - start) * 1000)
    content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower() or None
    final_url = normalize_url(response.url) or response.url

    if response.status_code >= 400:
        return FetchResult(target, final_url, response.status_code, content_type, None, "http_error", elapsed_ms)

    if content_type and content_type not in SUPPORTED_CONTENT_TYPES:
        return FetchResult(
            target,
            final_url,
            response.status_code,
            content_type,
            None,
            "unsupported_content_type",
            elapsed_ms,
        )

    # requests handles response encoding detection internally.
    text = response.text
    return FetchResult(target, final_url, response.status_code, content_type, text, None, elapsed_ms)


def same_registered_domain(url_a: str, url_b: str) -> bool:
    a = urlparse(url_a).netloc.lower().removeprefix("www.")
    b = urlparse(url_b).netloc.lower().removeprefix("www.")
    return bool(a and b and a == b)

