from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter, sleep
from urllib.parse import urlparse

import requests

from src.normalize import ensure_url_has_scheme, normalize_url

DEFAULT_TIMEOUT = 20
DEFAULT_RETRIES = 2
RETRY_BACKOFF_SECONDS = (0.4, 0.9)
RETRY_STATUS_CODES = {403, 429, 500, 502, 503, 504}

SUPPORTED_CONTENT_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "application/xml",
    "text/xml",
    "text/plain",
}

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
    "Connection": "keep-alive",
}

DIAGNOSTIC_HEADER_KEYS = (
    "server",
    "date",
    "content-type",
    "content-length",
    "cf-ray",
    "cf-cache-status",
    "x-cache",
    "x-served-by",
    "x-frame-options",
    "x-robots-tag",
    "retry-after",
    "set-cookie",
    "via",
)


@dataclass(slots=True)
class FetchResult:
    request_url: str
    final_url: str | None
    status_code: int | None
    content_type: str | None
    text: str | None
    error: str | None
    elapsed_ms: int
    response_headers: dict[str, str]

    @property
    def ok(self) -> bool:
        return self.error is None and self.status_code is not None and 200 <= self.status_code < 400


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    return session


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
    referer: str | None = None,
    retries: int = DEFAULT_RETRIES,
) -> FetchResult:
    target = normalize_url(ensure_url_has_scheme(url))
    if not target:
        return FetchResult(url, None, None, None, None, "invalid_url", 0, {})

    client = session or create_session()
    headers = _build_headers(referer=referer)

    attempts = max(0, retries) + 1
    start_total = perf_counter()
    last_result: FetchResult | None = None

    for attempt_idx in range(attempts):
        start = perf_counter()
        try:
            response = client.get(
                target,
                headers=headers,
                timeout=timeout,
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            elapsed_ms = int((perf_counter() - start_total) * 1000)
            last_result = FetchResult(
                request_url=target,
                final_url=None,
                status_code=None,
                content_type=None,
                text=None,
                error=f"request_error:{exc}",
                elapsed_ms=elapsed_ms,
                response_headers={},
            )
            if attempt_idx < attempts - 1:
                _sleep_backoff(attempt_idx)
                continue
            return last_result

        content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower() or None
        final_url = normalize_url(response.url) or response.url
        diagnostic_headers = _select_response_headers(response.headers)
        elapsed_ms = int((perf_counter() - start_total) * 1000)

        if response.status_code >= 400:
            last_result = FetchResult(
                request_url=target,
                final_url=final_url,
                status_code=response.status_code,
                content_type=content_type,
                text=None,
                error="http_error",
                elapsed_ms=elapsed_ms,
                response_headers=diagnostic_headers,
            )
            if response.status_code in RETRY_STATUS_CODES and attempt_idx < attempts - 1:
                _sleep_backoff(attempt_idx)
                continue
            return last_result

        if content_type and content_type not in SUPPORTED_CONTENT_TYPES:
            return FetchResult(
                request_url=target,
                final_url=final_url,
                status_code=response.status_code,
                content_type=content_type,
                text=None,
                error="unsupported_content_type",
                elapsed_ms=elapsed_ms,
                response_headers=diagnostic_headers,
            )

        return FetchResult(
            request_url=target,
            final_url=final_url,
            status_code=response.status_code,
            content_type=content_type,
            text=response.text,
            error=None,
            elapsed_ms=elapsed_ms,
            response_headers=diagnostic_headers,
        )

    # Defensive fallback (should not be reached).
    if last_result:
        return last_result
    return FetchResult(target, None, None, None, None, "unknown_error", int((perf_counter() - start_total) * 1000), {})


def same_registered_domain(url_a: str, url_b: str) -> bool:
    a = urlparse(url_a).netloc.lower().removeprefix("www.")
    b = urlparse(url_b).netloc.lower().removeprefix("www.")
    return bool(a and b and a == b)


def _build_headers(referer: str | None) -> dict[str, str]:
    headers = dict(DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer
    return headers


def _sleep_backoff(attempt_idx: int) -> None:
    backoff = RETRY_BACKOFF_SECONDS[min(attempt_idx, len(RETRY_BACKOFF_SECONDS) - 1)]
    sleep(backoff)


def _select_response_headers(headers: requests.structures.CaseInsensitiveDict[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in DIAGNOSTIC_HEADER_KEYS:
        value = headers.get(key)
        if value:
            out[key.lower()] = value
    return out

