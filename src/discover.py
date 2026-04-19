from __future__ import annotations

from collections import defaultdict
import re
from typing import Iterable
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from src.normalize import normalize_url, normalize_whitespace

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


def extract_links_from_html(html: str, base_url: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html or "", "html.parser")
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
    soup = BeautifulSoup(xml_text or "", "xml")
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
    return score, reasons


def keyword_in_text(blob: str, keyword: str) -> bool:
    if " " in keyword:
        return keyword in blob
    return re.search(rf"\b{re.escape(keyword)}\b", blob) is not None


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
            "score": round(score, 3),
            "reasons": ",".join(sorted(set(reasons))),
            "source_url": source_url,
        }
        if prior is None or float(payload["score"]) > float(prior["score"]):
            dedup[url] = payload

    ranked = sorted(dedup.values(), key=lambda item: float(item["score"]), reverse=True)
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
