from __future__ import annotations

from collections import defaultdict
import re
from typing import Iterable
from urllib.parse import urlparse

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover - fallback for minimal environments
    BeautifulSoup = None

from src.normalize import get_domain, normalize_url, normalize_whitespace

HIGH_VALUE_KEYWORDS: dict[str, float] = {
    "contact": 2.5,
    "directory": 2.5,
    "staff": 2.0,
    "department": 1.5,
    "assessor": 2.5,
    "property card": 3.0,
    "property record": 3.0,
    "gis": 3.0,
    "map": 1.0,
    "tax": 2.5,
    "pay": 1.0,
    "collector": 1.5,
    "job": 2.5,
    "employment": 2.5,
    "career": 2.0,
    "permit": 2.5,
    "zoning": 2.0,
    "land use": 2.0,
    "building": 1.5,
    "agenda": 2.0,
    "minutes": 2.0,
    "meeting": 1.5,
    # Contact-oriented discovery keywords.
    "town clerk": 3.1,
    "clerk": 2.2,
    "first selectman": 3.2,
    "board of selectmen": 3.2,
    "mayor": 2.8,
    "town manager": 2.8,
    "town administrator": 2.8,
    "administration": 2.2,
    "contact us": 2.8,
    "departments": 2.4,
    "department": 2.3,
    "staff directory": 3.0,
    "staff": 2.2,
    "official": 2.1,
    "officials": 2.1,
    "tax collector": 2.6,
    "animal control": 2.5,
    "public works": 2.5,
    "finance": 2.3,
    "planning": 2.3,
    "land use": 2.2,
    "wetlands": 2.3,
    "police": 2.0,
    "fire": 2.0,
    "registrar": 2.2,
    "human resources": 2.3,
}

BROAD_FALLBACK_KEYWORDS: dict[str, float] = {
    "services": 0.9,
    "government": 0.9,
    "board": 0.9,
    "commission": 0.9,
    "committee": 0.8,
    "office": 0.8,
    "forms": 0.8,
    "documents": 0.7,
    "administration": 0.7,
    "town hall": 0.7,
    "clerk": 0.7,
    "finance": 0.7,
}

OFFICIAL_PAGE_KEYWORDS = (
    "first selectman",
    "board of selectmen",
    "selectman",
    "mayor",
    "town manager",
    "town administrator",
    "administration",
    "official",
    "officials",
)

DIRECTORY_PAGE_KEYWORDS = (
    "staff directory",
    "directory",
    "staff",
)

DIRECTORY_CATEGORY_PARENT_TYPES = {"directory_page", "directory_category_page"}

CONTACT_PAGE_KEYWORDS = (
    "contact us",
    "contact",
)

DEPARTMENT_PAGE_KEYWORDS = (
    "department",
    "departments",
    "town clerk",
    "clerk",
    "animal control",
    "public works",
    "finance",
    "assessor",
    "tax collector",
    "planning",
    "zoning",
    "land use",
    "wetlands",
    "building",
    "police",
    "fire",
    "registrar",
    "human resources",
)

SERVICE_PAGE_KEYWORDS = (
    "gis",
    "property card",
    "property record",
    "record card",
    "tax payment",
    "pay taxes",
    "jobs",
    "employment",
    "careers",
    "permit",
    "permitting",
)

DIRECTORY_CATEGORY_HINT_KEYWORDS = tuple(sorted(set(DEPARTMENT_PAGE_KEYWORDS + OFFICIAL_PAGE_KEYWORDS)))

CONTACT_ORIENTED_PAGE_TYPES = {
    "official_page",
    "department_page",
    "directory_page",
    "directory_category_page",
    "contact_page",
}
DRILLABLE_PAGE_TYPES = CONTACT_ORIENTED_PAGE_TYPES
NON_HTML_LINK_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".svg", ".css", ".js", ".ico", ".zip", ".pdf", ".doc", ".docx")


def extract_links_from_html(html: str, base_url: str) -> list[dict[str, str]]:
    if BeautifulSoup is None:
        return _extract_links_html_fallback(html or "", base_url)
    try:
        soup = BeautifulSoup(html or "", "html.parser")
    except Exception:
        return _extract_links_html_fallback(html or "", base_url)
    links: list[dict[str, str]] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "").strip()
        normalized = normalize_url(href, base_url=base_url)
        if not normalized:
            continue
        label = normalize_whitespace(anchor.get_text(" ", strip=True)) or ""
        links.append({"url": normalized, "label": label})
    return links


def extract_links_from_sitemap_xml(xml_text: str) -> list[dict[str, str]]:
    if BeautifulSoup is None:
        return _extract_links_xml_fallback(xml_text or "")
    try:
        soup = BeautifulSoup(xml_text or "", "xml")
    except Exception:
        return _extract_links_xml_fallback(xml_text or "")
    links: list[dict[str, str]] = []
    for loc in soup.find_all("loc"):
        url = normalize_url(loc.get_text(" ", strip=True))
        if url:
            links.append({"url": url, "label": ""})
    return links


def score_link(url: str, label: str, broad_mode: bool = False) -> tuple[float, list[str]]:
    blob = f"{label} {url}".lower()
    score = 0.0
    reasons: list[str] = []
    for keyword, weight in HIGH_VALUE_KEYWORDS.items():
        if keyword_in_text(blob, keyword):
            score += weight
            reasons.append(keyword)
    if broad_mode:
        for keyword, weight in BROAD_FALLBACK_KEYWORDS.items():
            if keyword_in_text(blob, keyword):
                score += weight
                reasons.append(f"broad:{keyword}")
    # Slightly de-prioritize known document links unless strongly keyword-matched.
    if any(url.lower().endswith(ext) for ext in (".pdf", ".doc", ".docx", ".xls", ".xlsx")):
        score -= 0.5
    if "/departments" in url.lower():
        score += 0.75
        reasons.append("departments_path")
    page_type = classify_page_type(url, label)
    if page_type in CONTACT_ORIENTED_PAGE_TYPES:
        score += 1.2
        reasons.append(f"page_type:{page_type}")
    if page_type == "service_page":
        score += 0.8
        reasons.append("page_type:service_page")
    return score, reasons


def keyword_in_text(blob: str, keyword: str) -> bool:
    normalized_blob = _normalize_keyword_blob(blob)
    normalized_keyword = _normalize_keyword_blob(keyword)
    if not normalized_blob or not normalized_keyword:
        return False
    if " " in normalized_keyword:
        return normalized_keyword in normalized_blob
    return re.search(rf"\b{re.escape(normalized_keyword)}\b", normalized_blob) is not None


def select_high_value_links(
    links: Iterable[dict[str, str]],
    min_score: float = 2.5,
    max_links: int = 35,
    broad_mode: bool = False,
) -> list[dict[str, str | float]]:
    dedup: dict[str, dict[str, str | float]] = {}
    for link in links:
        url = link.get("url") or ""
        if not url:
            continue
        path = urlparse(url).path.lower()
        if path in {"/sitemap", "/sitemap.xml", "/site-map"}:
            continue
        label = link.get("label") or ""
        source_url = link.get("source_url") or ""
        score, reasons = score_link(url, label, broad_mode=broad_mode)
        if score < min_score:
            continue
        prior = dedup.get(url)
        payload = {
            "url": url,
            "label": label,
            "page_type": classify_page_type(url, label),
            "score": round(score, 3),
            "reasons": ",".join(sorted(set(reasons))),
            "source_url": source_url,
        }
        if prior is None or float(payload["score"]) > float(prior["score"]):
            dedup[url] = payload

    ranked = sorted(
        dedup.values(),
        key=lambda item: (-float(item["score"]), str(item.get("url") or "")),
    )
    return ranked[:max_links]


def summarize_link_categories(links: Iterable[dict[str, str | float]]) -> dict[str, int]:
    buckets = defaultdict(int)
    for link in links:
        reasons = str(link.get("reasons") or "")
        for token in reasons.split(","):
            token = token.strip()
            if token:
                buckets[token] += 1
    return dict(sorted(buckets.items(), key=lambda kv: kv[1], reverse=True))


def classify_page_type(
    url: str,
    label: str | None = None,
    parent_page_type: str | None = None,
) -> str:
    blob = f"{label or ''} {url}".lower()
    parent = str(parent_page_type or "").strip().lower()
    if parent in DIRECTORY_CATEGORY_PARENT_TYPES:
        if any(keyword_in_text(blob, token) for token in DIRECTORY_CATEGORY_HINT_KEYWORDS):
            return "directory_category_page"
        if _is_directory_category_path(url):
            return "directory_category_page"
    if any(keyword_in_text(blob, token) for token in DIRECTORY_PAGE_KEYWORDS):
        return "directory_page"
    if any(keyword_in_text(blob, token) for token in CONTACT_PAGE_KEYWORDS):
        return "contact_page"
    if any(keyword_in_text(blob, token) for token in OFFICIAL_PAGE_KEYWORDS):
        return "official_page"
    if any(keyword_in_text(blob, token) for token in DEPARTMENT_PAGE_KEYWORDS):
        return "department_page"
    if any(keyword_in_text(blob, token) for token in SERVICE_PAGE_KEYWORDS):
        return "service_page"
    return "candidate"


def is_contact_oriented_page_type(page_type: str | None) -> bool:
    return str(page_type or "").strip().lower() in CONTACT_ORIENTED_PAGE_TYPES


def is_drillable_page_type(page_type: str | None) -> bool:
    return str(page_type or "").strip().lower() in DRILLABLE_PAGE_TYPES


def select_contact_child_links(
    links: Iterable[dict[str, str]],
    municipality_domain: str,
    parent_page_type: str | None = None,
    max_links: int = 8,
    min_score: float = 1.2,
) -> list[dict[str, str | float]]:
    dedup: dict[str, dict[str, str | float]] = {}
    muni = (municipality_domain or "").strip().lower()
    normalized_parent = str(parent_page_type or "").strip().lower()
    for link in links:
        url = str(link.get("url") or "")
        if not url:
            continue
        if not _is_internal_url(url, muni):
            continue
        if url.lower().endswith(NON_HTML_LINK_SUFFIXES):
            continue

        label = str(link.get("label") or "")
        page_type = classify_page_type(url, label, parent_page_type=normalized_parent)
        score, reasons = score_link(url, label, broad_mode=True)
        if normalized_parent in DIRECTORY_CATEGORY_PARENT_TYPES and page_type == "directory_category_page":
            score += 1.8
            reasons.append("directory_category_child")
        if page_type in CONTACT_ORIENTED_PAGE_TYPES:
            score += 1.6
            reasons.append(f"contact_child:{page_type}")
        if score < min_score and page_type == "candidate":
            continue

        prior = dedup.get(url)
        payload = {
            "url": url,
            "label": label,
            "page_type": page_type,
            "score": round(score, 3),
            "reasons": ",".join(sorted(set(reasons))),
            "source_url": str(link.get("source_url") or ""),
        }
        if prior is None or float(payload["score"]) > float(prior["score"]):
            dedup[url] = payload

    ranked = sorted(
        dedup.values(),
        key=lambda item: (-float(item["score"]), str(item.get("url") or "")),
    )
    return ranked[:max_links]


def _is_internal_url(url: str, municipality_domain: str) -> bool:
    domain = (get_domain(url) or "").lower()
    if not domain or not municipality_domain:
        return False
    return domain == municipality_domain or domain.endswith(f".{municipality_domain}")


def _is_directory_category_path(url: str) -> bool:
    path = (urlparse(url).path or "").lower()
    if not path:
        return False
    if any(token in path for token in ("/departments/", "/department/", "/directory/", "/government/")):
        return True
    return any(
        token in path
        for token in (
            "town-clerk",
            "animal-control",
            "public-works",
            "tax-collector",
            "assessor",
            "planning",
            "zoning",
            "wetlands",
            "building",
            "police",
            "fire",
            "registrar",
        )
    )


def _normalize_keyword_blob(value: str) -> str:
    lowered = (value or "").lower()
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def _extract_links_html_fallback(html: str, base_url: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for href in re.findall(r'(?i)<a[^>]+href=["\']([^"\']+)["\']', html):
        normalized = normalize_url(href, base_url=base_url)
        if not normalized:
            continue
        links.append({"url": normalized, "label": ""})
    return links


def _extract_links_xml_fallback(xml_text: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for loc in re.findall(r"(?is)<loc>\s*([^<]+)\s*</loc>", xml_text):
        normalized = normalize_url(loc.strip())
        if not normalized:
            continue
        links.append({"url": normalized, "label": ""})
    return links
