from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db import get_connection
from src.batch_manifest import (
    ensure_batch_qa_scaffold,
    load_manifest_rows,
    load_seed_platform_map,
)

ROLE_GROUPS: dict[str, tuple[str, ...]] = {
    "executive": (
        "First Selectman",
        "Mayor",
        "Town Manager",
        "Town Administrator",
    ),
    "assessment": ("Assessor",),
    "tax": ("Tax Collector",),
    "clerk": ("Town Clerk",),
    "building": ("Building Official",),
    "planning": (
        "Planner",
        "Land Use",
        "Zoning Enforcement Officer",
        "ZEO",
    ),
    "finance": (
        "Finance Director",
        "Treasurer",
    ),
}

ROLE_WINNER_FIELDS = (
    "contact_id",
    "municipality_id",
    "entity_type",
    "name",
    "title",
    "role_normalized",
    "role_family",
    "department",
    "department_normalized",
    "email",
    "email_type",
    "phone",
    "phone_ext",
    "address",
    "hours",
    "page_type",
    "source_url",
    "display_confidence",
    "is_likely_noise",
    "rn",
)

SUSPICIOUS_FIELDS = (
    "municipality_id",
    "role_normalized",
    "name",
    "email",
    "phone",
    "department",
    "page_type",
    "source_url",
    "suspicious_reason",
)

MISSING_KEY_ROLE_FIELDS = (
    "municipality_id",
    "town_name",
    "missing_role_groups",
    "missing_group_count",
    "present_role_groups",
    "present_group_roles",
)

MANUAL_REVIEW_FIELDS = (
    "municipality_id",
    "town_name",
    "review_type",
    "role_normalized",
    "name",
    "email",
    "phone",
    "department",
    "page_type",
    "source_url",
    "missing_role_groups",
    "missing_group_count",
    "suspicious_reason",
    "coverage_interpretation",
)

SUMMARY_FIELDS = (
    "municipality_id",
    "town_name",
    "county",
    "raw_contacts",
    "clean_contacts",
    "role_winners",
    "service_links",
)

CRAWL_DIAGNOSTIC_FIELDS = (
    "municipality_id",
    "batch_id",
    "seed_url_attempted",
    "final_url_fetched",
    "fallback_used",
    "http_status",
    "redirect_count",
    "content_type",
    "page_title",
    "response_text_length",
    "extracted_link_count",
    "candidate_service_link_count",
    "candidate_directory_link_count",
    "contact_rows_extracted",
    "detected_block_signal",
    "detected_js_shell_signal",
    "diagnostic_class",
)

BLOCKED_TOWN_FIELDS = (
    "municipality_id",
    "town_name",
    "batch_id",
    "seed_url_attempted",
    "final_url_fetched",
    "fallback_used",
    "http_status",
    "redirect_count",
    "content_type",
    "page_title",
    "response_text_length",
    "extracted_link_count",
    "candidate_service_link_count",
    "candidate_directory_link_count",
    "contact_rows_extracted",
    "detected_block_signal",
    "detected_js_shell_signal",
    "diagnostic_class",
    "blocked_reason",
)

COVERAGE_SUMMARY_FIELDS = (
    "batch_id",
    "municipalities_total",
    "municipalities_blocked",
    "municipalities_unblocked",
    "municipalities_with_zero_yield_unblocked",
    "municipalities_with_any_missing_groups_unblocked",
    "municipalities_fully_covered_unblocked",
)

CRAWL_FAILURE_DIAGNOSTIC_CLASSES = {
    "blocked_or_forbidden",
    "probable_js_shell",
    "discovery_failure",
    "low_extraction",
}
JS_SHELL_LINK_NEAR_ZERO_THRESHOLD = 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export batch QA CSVs for one manifest batch.")
    parser.add_argument("--batch-id", required=True, help="Batch ID to export (e.g. batch_1).")
    parser.add_argument(
        "--manifest",
        default=str(ROOT / "data" / "manifests" / "civicplus_manifest.csv"),
        help="Manifest CSV path.",
    )
    parser.add_argument(
        "--platform",
        default=None,
        help="Optional platform filter (e.g. CivicPlus) based on seed CSV platform column.",
    )
    parser.add_argument(
        "--seed-csv",
        default=str(ROOT / "config" / "municipalities_seed.csv"),
        help="Seed CSV path containing municipality_id + platform.",
    )
    parser.add_argument("--db", default=str(ROOT / "database" / "master.sqlite"), help="SQLite DB path.")
    parser.add_argument(
        "--outputs-root",
        default=str(ROOT / "outputs" / "batches"),
        help="Batch output base folder.",
    )
    return parser.parse_args()


def write_csv(path: Path, rows: list[dict], fieldnames: tuple[str, ...] | list[str]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames))
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})
    return len(rows)


def object_exists(conn, name: str, object_type: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = ? AND name = ? LIMIT 1",
        (object_type, name),
    ).fetchone()
    return row is not None


def placeholders(size: int) -> str:
    return ",".join("?" for _ in range(size))


def fetch_count_map(conn, object_name: str, municipality_ids: list[str]) -> dict[str, int]:
    if not municipality_ids:
        return {}
    sql = f"""
        SELECT municipality_id, COUNT(*) AS cnt
        FROM {object_name}
        WHERE municipality_id IN ({placeholders(len(municipality_ids))})
        GROUP BY municipality_id
    """
    rows = conn.execute(sql, tuple(municipality_ids)).fetchall()
    return {str(row["municipality_id"]): int(row["cnt"]) for row in rows}


def fetch_role_winners(conn, municipality_ids: list[str]) -> list[dict]:
    if not municipality_ids or not object_exists(conn, "vw_best_role_per_town", "view"):
        return []
    sql = f"""
        SELECT *
        FROM vw_best_role_per_town
        WHERE municipality_id IN ({placeholders(len(municipality_ids))})
        ORDER BY municipality_id, role_normalized
    """
    rows = conn.execute(sql, tuple(municipality_ids)).fetchall()
    return [dict(row) for row in rows]


def fetch_suspicious_winners(conn, municipality_ids: list[str]) -> list[dict]:
    if not municipality_ids or not object_exists(conn, "vw_best_role_per_town", "view"):
        return []
    sql = f"""
        SELECT
          municipality_id,
          role_normalized,
          name,
          email,
          phone,
          department,
          page_type,
          source_url,
          CASE
            WHEN (email IS NULL OR TRIM(email) = '') AND (phone IS NULL OR TRIM(phone) = '') THEN 'missing_email_and_phone'
            WHEN role_normalized = 'Assessor'
                 AND (
                     LOWER(COALESCE(source_url, '')) LIKE '%tax%'
                     OR LOWER(COALESCE(email, '')) LIKE '%tax%'
                 )
            THEN 'assessor_tax_mismatch'
            WHEN role_normalized = 'Tax Collector'
                 AND (
                     LOWER(COALESCE(source_url, '')) LIKE '%clerk%'
                     OR LOWER(COALESCE(source_url, '')) LIKE '%planning%'
                 )
            THEN 'tax_collector_role_page_mismatch'
            WHEN role_normalized = 'Town Clerk'
                 AND LOWER(COALESCE(source_url, '')) LIKE '%planning%'
            THEN 'town_clerk_planning_mismatch'
            WHEN role_normalized = 'First Selectman'
                 AND (
                     LOWER(COALESCE(source_url, '')) LIKE '%finance%'
                     OR LOWER(COALESCE(source_url, '')) LIKE '%building%'
                 )
            THEN 'first_selectman_role_page_mismatch'
            WHEN role_normalized IN ('First Selectman', 'Mayor', 'Town Manager')
                 AND (
                     LOWER(COALESCE(name, '')) LIKE '%assistant%'
                     OR LOWER(COALESCE(title, '')) LIKE '%assistant%'
                     OR LOWER(COALESCE(department, '')) LIKE '%assistant%'
                     OR LOWER(COALESCE(name, '')) LIKE '%admin assistant%'
                     OR LOWER(COALESCE(title, '')) LIKE '%admin assistant%'
                     OR LOWER(COALESCE(name, '')) LIKE '%administrative%'
                     OR LOWER(COALESCE(title, '')) LIKE '%administrative%'
                     OR LOWER(COALESCE(name, '')) LIKE '%executive assistant%'
                     OR LOWER(COALESCE(title, '')) LIKE '%executive assistant%'
                 )
            THEN 'chief_role_assistant_contamination'
            ELSE 'review'
          END AS suspicious_reason
        FROM vw_best_role_per_town
        WHERE municipality_id IN ({placeholders(len(municipality_ids))})
          AND (
            ((email IS NULL OR TRIM(email) = '') AND (phone IS NULL OR TRIM(phone) = ''))
            OR (
                role_normalized = 'Assessor'
                AND (
                    LOWER(COALESCE(source_url, '')) LIKE '%tax%'
                    OR LOWER(COALESCE(email, '')) LIKE '%tax%'
                )
            )
            OR (
                role_normalized = 'Tax Collector'
                AND (
                    LOWER(COALESCE(source_url, '')) LIKE '%clerk%'
                    OR LOWER(COALESCE(source_url, '')) LIKE '%planning%'
                )
            )
            OR (
                role_normalized = 'Town Clerk'
                AND LOWER(COALESCE(source_url, '')) LIKE '%planning%'
            )
            OR (
                role_normalized = 'First Selectman'
                AND (
                    LOWER(COALESCE(source_url, '')) LIKE '%finance%'
                    OR LOWER(COALESCE(source_url, '')) LIKE '%building%'
                )
            )
            OR (
                role_normalized IN ('First Selectman', 'Mayor', 'Town Manager')
                AND (
                    LOWER(COALESCE(name, '')) LIKE '%assistant%'
                    OR LOWER(COALESCE(title, '')) LIKE '%assistant%'
                    OR LOWER(COALESCE(department, '')) LIKE '%assistant%'
                    OR LOWER(COALESCE(name, '')) LIKE '%admin assistant%'
                    OR LOWER(COALESCE(title, '')) LIKE '%admin assistant%'
                    OR LOWER(COALESCE(name, '')) LIKE '%administrative%'
                    OR LOWER(COALESCE(title, '')) LIKE '%administrative%'
                    OR LOWER(COALESCE(name, '')) LIKE '%executive assistant%'
                    OR LOWER(COALESCE(title, '')) LIKE '%executive assistant%'
                )
            )
          )
        ORDER BY municipality_id, role_normalized
    """
    rows = conn.execute(sql, tuple(municipality_ids)).fetchall()
    return [dict(row) for row in rows]


def _default_crawl_diagnostic_row(municipality_id: str, batch_id: str) -> dict[str, str | int]:
    return {
        "municipality_id": municipality_id,
        "batch_id": batch_id,
        "seed_url_attempted": "",
        "final_url_fetched": "",
        "fallback_used": 0,
        "http_status": "",
        "redirect_count": 0,
        "content_type": "",
        "page_title": "",
        "response_text_length": 0,
        "extracted_link_count": 0,
        "candidate_service_link_count": 0,
        "candidate_directory_link_count": 0,
        "contact_rows_extracted": 0,
        "detected_block_signal": 0,
        "detected_js_shell_signal": 0,
        "diagnostic_class": "ok",
    }


def _coerce_int(value: object, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip()
    if not text:
        return default
    try:
        return int(float(text))
    except ValueError:
        return default


def fetch_crawl_diagnostics(
    conn,
    municipality_ids: list[str],
    batch_id: str,
) -> list[dict[str, str | int]]:
    rows_by_id = {
        municipality_id: _default_crawl_diagnostic_row(municipality_id, batch_id)
        for municipality_id in municipality_ids
    }
    if not municipality_ids or not object_exists(conn, "signals", "table"):
        return list(rows_by_id.values())

    sql = f"""
        SELECT municipality_id, value
        FROM signals
        WHERE signal_type = 'crawl_diagnostics'
          AND municipality_id IN ({placeholders(len(municipality_ids))})
        ORDER BY rowid DESC
    """
    records = conn.execute(sql, tuple(municipality_ids)).fetchall()
    parsed_once: set[str] = set()
    for record in records:
        municipality_id = str(record["municipality_id"] or "")
        if not municipality_id or municipality_id in parsed_once:
            continue
        parsed_once.add(municipality_id)
        raw_value = str(record["value"] or "")
        if not raw_value.strip():
            continue
        try:
            payload = json.loads(raw_value)
        except json.JSONDecodeError:
            continue

        current = rows_by_id.get(municipality_id)
        if current is None:
            continue
        current["seed_url_attempted"] = str(payload.get("seed_url_attempted") or "")
        current["final_url_fetched"] = str(payload.get("final_url_fetched") or "")
        current["fallback_used"] = 1 if _coerce_int(payload.get("fallback_used")) > 0 else 0
        status_value = payload.get("http_status")
        current["http_status"] = _coerce_int(status_value) if status_value not in (None, "") else ""
        current["redirect_count"] = _coerce_int(payload.get("redirect_count"))
        current["content_type"] = str(payload.get("content_type") or "")
        current["page_title"] = str(payload.get("page_title") or "")
        current["response_text_length"] = _coerce_int(payload.get("response_text_length"))
        current["extracted_link_count"] = _coerce_int(payload.get("extracted_link_count"))
        current["candidate_service_link_count"] = _coerce_int(payload.get("candidate_service_link_count"))
        current["candidate_directory_link_count"] = _coerce_int(payload.get("candidate_directory_link_count"))
        current["contact_rows_extracted"] = _coerce_int(payload.get("contact_rows_extracted"))
        current["detected_block_signal"] = 1 if _coerce_int(payload.get("detected_block_signal")) > 0 else 0
        current["detected_js_shell_signal"] = 1 if _coerce_int(payload.get("detected_js_shell_signal")) > 0 else 0
        current["diagnostic_class"] = str(payload.get("diagnostic_class") or "ok")

    return [rows_by_id[municipality_id] for municipality_id in municipality_ids]


def derive_blocked_reason(diagnostic_row: dict[str, str | int]) -> str:
    status = _coerce_int(diagnostic_row.get("http_status"), default=-1)
    if status in {401, 403}:
        return "http_forbidden"
    if status == 429:
        return "rate_limited"
    if status == 503:
        return "service_unavailable_or_protected"
    if _coerce_int(diagnostic_row.get("detected_block_signal")) == 1:
        return "block_signal_detected"
    return "blocked_or_forbidden"


def is_effectively_blocked_diagnostic(diagnostic_row: dict[str, str | int]) -> bool:
    status_code = _coerce_int(diagnostic_row.get("http_status"), default=-1)
    contact_rows_extracted = _coerce_int(diagnostic_row.get("contact_rows_extracted"))
    candidate_service_link_count = _coerce_int(diagnostic_row.get("candidate_service_link_count"))
    extracted_link_count = _coerce_int(diagnostic_row.get("extracted_link_count"))
    detected_block_signal = _coerce_int(diagnostic_row.get("detected_block_signal"))

    # Hard HTTP block statuses beat all.
    if status_code in {401, 403, 429, 503}:
        return True

    # Successful extraction beats all soft heuristics.
    if contact_rows_extracted > 0:
        return False
    if status_code == 200 and extracted_link_count > 0 and candidate_service_link_count > 0:
        return False

    # Soft block only for low-success / zero-yield cases.
    if (
        status_code == 200
        and detected_block_signal == 1
        and contact_rows_extracted == 0
        and candidate_service_link_count == 0
        and extracted_link_count <= JS_SHELL_LINK_NEAR_ZERO_THRESHOLD
    ):
        return True
    return False


def build_blocked_towns_rows(
    municipalities: list[dict[str, str]],
    crawl_diagnostics: list[dict[str, str | int]],
) -> list[dict[str, str | int]]:
    town_name_by_id = {row["municipality_id"]: row["town_name"] for row in municipalities}
    out: list[dict[str, str | int]] = []
    for diagnostic in crawl_diagnostics:
        if not is_effectively_blocked_diagnostic(diagnostic):
            continue
        municipality_id = str(diagnostic.get("municipality_id") or "")
        out.append(
            {
                "municipality_id": municipality_id,
                "town_name": town_name_by_id.get(municipality_id, ""),
                "batch_id": str(diagnostic.get("batch_id") or ""),
                "seed_url_attempted": str(diagnostic.get("seed_url_attempted") or ""),
                "final_url_fetched": str(diagnostic.get("final_url_fetched") or ""),
                "fallback_used": 1 if _coerce_int(diagnostic.get("fallback_used")) > 0 else 0,
                "http_status": diagnostic.get("http_status") if diagnostic.get("http_status") not in ("", None) else "",
                "redirect_count": _coerce_int(diagnostic.get("redirect_count")),
                "content_type": str(diagnostic.get("content_type") or ""),
                "page_title": str(diagnostic.get("page_title") or ""),
                "response_text_length": _coerce_int(diagnostic.get("response_text_length")),
                "extracted_link_count": _coerce_int(diagnostic.get("extracted_link_count")),
                "candidate_service_link_count": _coerce_int(diagnostic.get("candidate_service_link_count")),
                "candidate_directory_link_count": _coerce_int(diagnostic.get("candidate_directory_link_count")),
                "contact_rows_extracted": _coerce_int(diagnostic.get("contact_rows_extracted")),
                "detected_block_signal": 1 if _coerce_int(diagnostic.get("detected_block_signal")) > 0 else 0,
                "detected_js_shell_signal": 1 if _coerce_int(diagnostic.get("detected_js_shell_signal")) > 0 else 0,
                "diagnostic_class": "blocked_or_forbidden",
                "blocked_reason": derive_blocked_reason(diagnostic),
            }
        )
    out.sort(key=lambda row: str(row.get("municipality_id") or ""))
    return out


def build_coverage_summary_row(
    batch_id: str,
    municipalities: list[dict[str, str]],
    raw_counts: dict[str, int],
    clean_counts: dict[str, int],
    winner_counts: dict[str, int],
    missing_role_rows: list[dict[str, str | int]],
    crawl_diagnostics: list[dict[str, str | int]],
) -> dict[str, int | str]:
    municipality_ids = {row["municipality_id"] for row in municipalities}
    blocked_ids = {
        str(row.get("municipality_id") or "")
        for row in crawl_diagnostics
        if is_effectively_blocked_diagnostic(row)
    } & municipality_ids
    unblocked_ids = municipality_ids - blocked_ids

    municipalities_with_zero_yield_unblocked = sum(
        1
        for municipality_id in unblocked_ids
        if raw_counts.get(municipality_id, 0) == 0
        and clean_counts.get(municipality_id, 0) == 0
        and winner_counts.get(municipality_id, 0) == 0
    )
    missing_unblocked_ids = {
        str(row.get("municipality_id") or "")
        for row in missing_role_rows
        if str(row.get("municipality_id") or "") in unblocked_ids
    }
    municipalities_with_any_missing_groups_unblocked = len(missing_unblocked_ids)
    municipalities_unblocked = len(unblocked_ids)
    municipalities_fully_covered_unblocked = max(
        0,
        municipalities_unblocked - municipalities_with_any_missing_groups_unblocked,
    )

    return {
        "batch_id": batch_id,
        "municipalities_total": len(municipality_ids),
        "municipalities_blocked": len(blocked_ids),
        "municipalities_unblocked": municipalities_unblocked,
        "municipalities_with_zero_yield_unblocked": municipalities_with_zero_yield_unblocked,
        "municipalities_with_any_missing_groups_unblocked": municipalities_with_any_missing_groups_unblocked,
        "municipalities_fully_covered_unblocked": municipalities_fully_covered_unblocked,
    }


def build_missing_key_roles(
    municipalities: list[dict[str, str]],
    winners: list[dict],
) -> list[dict[str, str | int]]:
    role_map: dict[str, set[str]] = {}
    for row in winners:
        municipality_id = str(row.get("municipality_id") or "")
        role = str(row.get("role_normalized") or "")
        if not municipality_id or not role:
            continue
        role_map.setdefault(municipality_id, set()).add(role)

    out: list[dict[str, str | int]] = []
    for municipality in municipalities:
        municipality_id = municipality["municipality_id"]
        present = role_map.get(municipality_id, set())
        present_groups: list[str] = []
        missing_groups: list[str] = []
        present_group_roles: list[str] = []
        for group_name, group_roles in ROLE_GROUPS.items():
            matched_roles = [role for role in group_roles if role in present]
            if matched_roles:
                present_groups.append(group_name)
                present_group_roles.append(f"{group_name}: {', '.join(matched_roles)}")
            else:
                missing_groups.append(group_name)
        if not missing_groups:
            continue
        out.append(
            {
                "municipality_id": municipality_id,
                "town_name": municipality["town_name"],
                "missing_role_groups": "; ".join(missing_groups),
                "missing_group_count": len(missing_groups),
                "present_role_groups": "; ".join(present_groups),
                "present_group_roles": "; ".join(present_group_roles),
            }
        )
    return out


def build_manual_review_rows(
    municipalities: list[dict[str, str]],
    suspicious_rows: list[dict],
    missing_role_rows: list[dict[str, str | int]],
    raw_counts: dict[str, int],
    clean_counts: dict[str, int],
    winner_counts: dict[str, int],
    crawl_diagnostics: list[dict[str, str | int]],
) -> list[dict[str, str | int]]:
    town_name_by_id = {row["municipality_id"]: row["town_name"] for row in municipalities}
    crawl_diag_by_id = {
        str(row.get("municipality_id") or ""): row
        for row in crawl_diagnostics
    }
    out: list[dict[str, str | int]] = []
    zero_yield_diag_municipalities: set[str] = set()

    for row in suspicious_rows:
        municipality_id = str(row.get("municipality_id") or "")
        out.append(
            {
                "municipality_id": municipality_id,
                "town_name": town_name_by_id.get(municipality_id, ""),
                "review_type": "suspicious_winner",
                "role_normalized": row.get("role_normalized"),
                "name": row.get("name"),
                "email": row.get("email"),
                "phone": row.get("phone"),
                "department": row.get("department"),
                "page_type": row.get("page_type"),
                "source_url": row.get("source_url"),
                "missing_role_groups": "",
                "missing_group_count": "",
                "suspicious_reason": row.get("suspicious_reason"),
                "coverage_interpretation": "suspicious_winner",
            }
        )

    for municipality in municipalities:
        municipality_id = municipality["municipality_id"]
        if raw_counts.get(municipality_id, 0) != 0:
            continue
        if clean_counts.get(municipality_id, 0) != 0:
            continue
        if winner_counts.get(municipality_id, 0) != 0:
            continue

        diagnostic = crawl_diag_by_id.get(municipality_id, {})
        diagnostic_class = str(diagnostic.get("diagnostic_class") or "")
        if diagnostic_class not in CRAWL_FAILURE_DIAGNOSTIC_CLASSES:
            continue

        zero_yield_diag_municipalities.add(municipality_id)
        coverage_interpretation = (
            "blocked_town"
            if diagnostic_class == "blocked_or_forbidden"
            else "crawl_diagnostic_nonblocked_failure"
        )
        out.append(
            {
                "municipality_id": municipality_id,
                "town_name": town_name_by_id.get(municipality_id, ""),
                "review_type": "crawl_diagnostic_zero_yield",
                "role_normalized": "",
                "name": "",
                "email": "",
                "phone": "",
                "department": "",
                "page_type": "",
                "source_url": str(diagnostic.get("final_url_fetched") or diagnostic.get("seed_url_attempted") or ""),
                "missing_role_groups": "",
                "missing_group_count": "",
                "suspicious_reason": f"crawl_diagnostic:{diagnostic_class}",
                "coverage_interpretation": coverage_interpretation,
            }
        )

    for row in missing_role_rows:
        missing_group_count = int(row.get("missing_group_count") or 0)
        if missing_group_count < 2:
            continue
        municipality_id = str(row.get("municipality_id") or "")
        if municipality_id in zero_yield_diag_municipalities:
            continue
        out.append(
            {
                "municipality_id": municipality_id,
                "town_name": row["town_name"],
                "review_type": "missing_role_groups_2plus",
                "role_normalized": "",
                "name": "",
                "email": "",
                "phone": "",
                "department": "",
                "page_type": "",
                "source_url": "",
                "missing_role_groups": row["missing_role_groups"],
                "missing_group_count": missing_group_count,
                "suspicious_reason": "",
                "coverage_interpretation": "normal_coverage_gap",
            }
        )

    out.sort(key=lambda r: (str(r["municipality_id"]), str(r["review_type"]), str(r["role_normalized"])))
    return out


def select_batch_municipalities(
    manifest_path: str | Path,
    batch_id: str,
    platform: str | None,
    seed_csv_path: str | Path,
) -> list[dict[str, str]]:
    rows = load_manifest_rows(manifest_path)
    selected = [row for row in rows if row["batch_id"].strip().lower() == batch_id.strip().lower()]
    if not selected:
        raise SystemExit(f"No rows found in manifest for batch_id={batch_id}")

    if platform:
        platform_map = load_seed_platform_map(seed_csv_path)
        wanted = platform.strip().lower()
        selected = [
            row
            for row in selected
            if (platform_map.get(row["municipality_id"]) or "").strip().lower() == wanted
        ]
        if not selected:
            raise SystemExit(f"No rows left after platform filter: {platform}")
    return selected


def main() -> None:
    args = parse_args()
    municipalities = select_batch_municipalities(
        manifest_path=args.manifest,
        batch_id=args.batch_id,
        platform=args.platform,
        seed_csv_path=args.seed_csv,
    )
    municipality_ids = [row["municipality_id"] for row in municipalities]

    batch_paths = ensure_batch_qa_scaffold(args.outputs_root, args.batch_id)
    batch_dir = Path(args.outputs_root) / args.batch_id

    conn = get_connection(args.db)
    try:
        raw_counts = fetch_count_map(conn, "contacts", municipality_ids)
        clean_counts = (
            fetch_count_map(conn, "vw_contacts_clean", municipality_ids)
            if object_exists(conn, "vw_contacts_clean", "view")
            else {}
        )
        winner_counts = (
            fetch_count_map(conn, "vw_best_role_per_town", municipality_ids)
            if object_exists(conn, "vw_best_role_per_town", "view")
            else {}
        )
        service_counts = fetch_count_map(conn, "service_links", municipality_ids)

        role_winners = fetch_role_winners(conn, municipality_ids)
        suspicious_winners = fetch_suspicious_winners(conn, municipality_ids)
        crawl_diagnostics = fetch_crawl_diagnostics(conn, municipality_ids, args.batch_id)
        missing_key_roles = build_missing_key_roles(municipalities, role_winners)
        blocked_towns = build_blocked_towns_rows(municipalities, crawl_diagnostics)
        coverage_summary_rows = [
            build_coverage_summary_row(
                batch_id=args.batch_id,
                municipalities=municipalities,
                raw_counts=raw_counts,
                clean_counts=clean_counts,
                winner_counts=winner_counts,
                missing_role_rows=missing_key_roles,
                crawl_diagnostics=crawl_diagnostics,
            )
        ]
        manual_review_rows = build_manual_review_rows(
            municipalities=municipalities,
            suspicious_rows=suspicious_winners,
            missing_role_rows=missing_key_roles,
            raw_counts=raw_counts,
            clean_counts=clean_counts,
            winner_counts=winner_counts,
            crawl_diagnostics=crawl_diagnostics,
        )
    finally:
        conn.close()

    summary_rows: list[dict[str, str | int]] = []
    for row in municipalities:
        municipality_id = row["municipality_id"]
        summary_rows.append(
            {
                "municipality_id": municipality_id,
                "town_name": row["town_name"],
                "county": row["county"],
                "raw_contacts": raw_counts.get(municipality_id, 0),
                "clean_contacts": clean_counts.get(municipality_id, 0),
                "role_winners": winner_counts.get(municipality_id, 0),
                "service_links": service_counts.get(municipality_id, 0),
            }
        )

    counts = {
        "qa_batch_summary.csv": write_csv(
            batch_dir / "qa_batch_summary.csv",
            summary_rows,
            SUMMARY_FIELDS,
        ),
        "qa_role_winners.csv": write_csv(
            batch_dir / "qa_role_winners.csv",
            role_winners,
            ROLE_WINNER_FIELDS,
        ),
        "qa_suspicious_winners.csv": write_csv(
            batch_dir / "qa_suspicious_winners.csv",
            suspicious_winners,
            SUSPICIOUS_FIELDS,
        ),
        "qa_missing_key_roles.csv": write_csv(
            batch_dir / "qa_missing_key_roles.csv",
            missing_key_roles,
            MISSING_KEY_ROLE_FIELDS,
        ),
        "qa_manual_review.csv": write_csv(
            batch_dir / "qa_manual_review.csv",
            manual_review_rows,
            MANUAL_REVIEW_FIELDS,
        ),
        "qa_crawl_diagnostics.csv": write_csv(
            batch_dir / "qa_crawl_diagnostics.csv",
            crawl_diagnostics,
            CRAWL_DIAGNOSTIC_FIELDS,
        ),
        "qa_blocked_towns.csv": write_csv(
            batch_dir / "qa_blocked_towns.csv",
            blocked_towns,
            BLOCKED_TOWN_FIELDS,
        ),
        "qa_coverage_summary.csv": write_csv(
            batch_dir / "qa_coverage_summary.csv",
            coverage_summary_rows,
            COVERAGE_SUMMARY_FIELDS,
        ),
    }

    print(f"Batch QA export complete for {args.batch_id}")
    print(f"Municipalities in scope: {len(municipalities)}")
    for filename, count in counts.items():
        print(f"{filename}: {count} rows -> {batch_paths[filename]}")


if __name__ == "__main__":
    main()
