from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.batch_manifest import load_manifest_rows, load_seed_platform_map
from src.db import get_connection


CATEGORY_ORDER: tuple[str, ...] = (
    "directory",
    "finance",
    "clerk",
    "assessor",
    "tax",
    "building",
    "planning",
    "departments_root",
)

HIGH_VALUE_CATEGORIES = frozenset(
    {
        "directory",
        "finance",
        "clerk",
        "assessor",
        "tax",
        "building",
        "planning",
    }
)

GENERIC_NOISE_PATHS = frozenset(
    {
        "/",
        "/home",
        "/index",
        "/contact-us",
    }
)

NOISY_PATH_TOKENS = (
    "news",
    "calendar",
    "events",
    "bid",
    "alert",
)

USEFUL_OTHER_URL_TOKENS = (
    "directory",
    "department",
    "departments",
    "government",
    "finance",
    "clerk",
    "assessor",
    "tax",
    "building",
    "planning",
    "zoning",
    "land-use",
    "staff",
)

INVENTORY_FIELDS: tuple[str, ...] = (
    "municipality_id",
    "source_url",
    "normalized_path",
    "canonical_path",
    "category",
    "role_normalized",
    "role_family",
    "department_normalized",
    "page_type",
)

CANDIDATE_FIELDS: tuple[str, ...] = (
    "category",
    "canonical_path",
    "score",
    "municipality_count",
    "row_count",
    "winner_backed_count",
    "is_clean_slug",
    "is_numeric_slug",
    "example_paths",
    "example_municipalities",
    "recommendation",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover data-driven deep-path probe expansion candidates from successful town source URLs."
    )
    parser.add_argument("--db", default=str(ROOT / "database" / "master.sqlite"), help="SQLite DB path.")
    parser.add_argument(
        "--batch-id",
        default=None,
        help="Optional manifest batch_id scope (e.g. batch_4). If omitted, all municipalities are analyzed.",
    )
    parser.add_argument(
        "--manifest",
        default=str(ROOT / "data" / "manifests" / "civicplus_manifest.csv"),
        help="Manifest CSV path.",
    )
    parser.add_argument(
        "--platform",
        default=None,
        help="Optional platform filter (e.g. CivicPlus) based on municipalities_seed.csv.",
    )
    parser.add_argument(
        "--seed-csv",
        default=str(ROOT / "config" / "municipalities_seed.csv"),
        help="Seed CSV path with municipality_id + platform.",
    )
    parser.add_argument(
        "--outputs-root",
        default=str(ROOT / "outputs" / "batches"),
        help="Batch output root when --batch-id is provided.",
    )
    parser.add_argument(
        "--analysis-root",
        default=str(ROOT / "outputs" / "analysis" / "deep_path_patterns"),
        help="Output folder when no --batch-id is provided.",
    )
    return parser.parse_args()


def write_csv(path: Path, rows: list[dict], fieldnames: tuple[str, ...]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames))
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fieldnames})
    return len(rows)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False)
        handle.write("\n")


def placeholders(size: int) -> str:
    return ",".join("?" for _ in range(size))


def normalize_text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def normalize_key(value: object) -> str:
    return normalize_text(value).lower()


def chunked(values: list[str], size: int = 800) -> list[list[str]]:
    return [values[i : i + size] for i in range(0, len(values), size)]


def object_exists(conn, name: str, object_type: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = ? AND name = ? LIMIT 1",
        (object_type, name),
    ).fetchone()
    return row is not None


def table_columns(conn, table_name: str) -> set[str]:
    if not object_exists(conn, table_name, "table"):
        return set()
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row["name"]).strip().lower() for row in rows}


def select_scope_municipality_ids(
    conn,
    batch_id: str | None,
    manifest_path: str | Path,
    platform: str | None,
    seed_csv_path: str | Path,
) -> list[str]:
    if not batch_id:
        rows = conn.execute(
            """
            SELECT municipality_id
            FROM municipalities
            ORDER BY municipality_id
            """
        ).fetchall()
        return [str(row["municipality_id"]) for row in rows if str(row["municipality_id"] or "").strip()]

    manifest_rows = load_manifest_rows(manifest_path)
    selected_ids = [
        row["municipality_id"]
        for row in manifest_rows
        if row["batch_id"].strip().lower() == batch_id.strip().lower()
    ]
    if not selected_ids:
        raise SystemExit(f"No municipalities found for batch_id={batch_id}")

    if platform:
        platform_map = load_seed_platform_map(seed_csv_path)
        wanted = platform.strip().lower()
        selected_ids = [
            municipality_id
            for municipality_id in selected_ids
            if (platform_map.get(municipality_id) or "").strip().lower() == wanted
        ]
        if not selected_ids:
            raise SystemExit(f"No municipalities left in scope after platform filter={platform}")

    rows = conn.execute(
        f"""
        SELECT municipality_id
        FROM municipalities
        WHERE municipality_id IN ({placeholders(len(selected_ids))})
        ORDER BY municipality_id
        """,
        tuple(selected_ids),
    ).fetchall()
    return [str(row["municipality_id"]) for row in rows if str(row["municipality_id"] or "").strip()]


def fetch_winner_municipality_ids(conn, scope_ids: list[str]) -> set[str]:
    if not scope_ids:
        return set()

    winners: set[str] = set()
    for scope_chunk in chunked(scope_ids):
        params = tuple(scope_chunk)
        where_sql = f"municipality_id IN ({placeholders(len(scope_chunk))})"

        if object_exists(conn, "vw_best_role_per_town", "view"):
            rows = conn.execute(
                f"""
                SELECT DISTINCT municipality_id
                FROM vw_best_role_per_town
                WHERE {where_sql}
                """,
                params,
            ).fetchall()
            winners.update(str(row["municipality_id"]) for row in rows if str(row["municipality_id"] or "").strip())
            continue

        if object_exists(conn, "vw_role_directory", "view"):
            rows = conn.execute(
                f"""
                SELECT DISTINCT municipality_id
                FROM vw_role_directory
                WHERE {where_sql}
                """,
                params,
            ).fetchall()
            winners.update(str(row["municipality_id"]) for row in rows if str(row["municipality_id"] or "").strip())
            continue

        contacts_cols = table_columns(conn, "contacts")
        role_col_exists = "role_normalized" in contacts_cols
        role_filter_sql = (
            "AND NULLIF(TRIM(COALESCE(role_normalized, '')), '') IS NOT NULL"
            if role_col_exists
            else ""
        )
        rows = conn.execute(
            f"""
            SELECT DISTINCT municipality_id
            FROM contacts
            WHERE {where_sql}
              {role_filter_sql}
            """,
            params,
        ).fetchall()
        winners.update(str(row["municipality_id"]) for row in rows if str(row["municipality_id"] or "").strip())
    return winners


def fetch_successful_contact_rows(conn, winner_ids: set[str]) -> list[dict]:
    if not winner_ids:
        return []

    contacts_cols = table_columns(conn, "contacts")
    has_role_normalized = "role_normalized" in contacts_cols
    has_role_family = "role_family" in contacts_cols
    has_department_normalized = "department_normalized" in contacts_cols
    has_page_type = "page_type" in contacts_cols
    has_is_likely_noise = "is_likely_noise" in contacts_cols

    role_normalized_expr = "c.role_normalized" if has_role_normalized else "'' AS role_normalized"
    role_family_expr = "c.role_family" if has_role_family else "'' AS role_family"
    department_normalized_expr = (
        "c.department_normalized" if has_department_normalized else "'' AS department_normalized"
    )
    page_type_expr = "c.page_type" if has_page_type else "'' AS page_type"

    useful_other_predicate = " OR ".join(
        f"LOWER(COALESCE(c.source_url, '')) LIKE '%{token}%'"
        for token in USEFUL_OTHER_URL_TOKENS
    )
    if has_page_type:
        page_type_filter_sql = f"""
          AND (
            LOWER(TRIM(COALESCE(c.page_type, ''))) IN ('staff_directory', 'department_page')
            OR (
              LOWER(TRIM(COALESCE(c.page_type, ''))) = 'other'
              AND ({useful_other_predicate})
            )
          )
        """
    else:
        page_type_filter_sql = f"AND ({useful_other_predicate})"

    noise_filter_sql = "AND COALESCE(c.is_likely_noise, 0) = 0" if has_is_likely_noise else ""

    rows_out: list[dict] = []
    winner_id_list = sorted(winner_ids)
    for winner_chunk in chunked(winner_id_list):
        rows = conn.execute(
            f"""
            SELECT
              c.municipality_id,
              c.title,
              {role_normalized_expr},
              {role_family_expr},
              {department_normalized_expr},
              {page_type_expr},
              c.source_url
            FROM contacts c
            WHERE c.municipality_id IN ({placeholders(len(winner_chunk))})
              AND NULLIF(TRIM(COALESCE(c.source_url, '')), '') IS NOT NULL
              {page_type_filter_sql}
              {noise_filter_sql}
            """,
            tuple(winner_chunk),
        ).fetchall()
        rows_out.extend(dict(row) for row in rows)
    return rows_out


def normalize_source_path(url: str) -> str:
    """
    Convert a full source URL into a normalized reusable path.
    Keep only path component.
    Strip domain/query/hash.
    Preserve useful slug structure.
    """
    raw = str(url or "").strip()
    if not raw:
        return ""

    parsed_input = raw
    if "://" not in raw:
        if raw.startswith("/"):
            parsed_input = f"https://placeholder.local{raw}"
        else:
            parsed_input = f"https://placeholder.local/{raw}"
    parsed = urlparse(parsed_input)
    path = unquote(parsed.path or "").strip()
    if not path:
        path = "/"
    if not path.startswith("/"):
        path = "/" + path
    path = re.sub(r"/{2,}", "/", path)
    if len(path) > 1:
        path = path.rstrip("/")
    return path


def canonicalize_path_for_grouping(path: str) -> str:
    """
    Lowercase, trim trailing slash, preserve slug tokens.
    """
    normalized = normalize_source_path(path)
    canonical = normalized.lower().strip()
    canonical = re.sub(r"/{2,}", "/", canonical)
    if len(canonical) > 1:
        canonical = canonical.rstrip("/")
    return canonical or "/"


def infer_path_category(
    role_normalized: str,
    role_family: str,
    department_normalized: str,
    title: str,
    path: str,
) -> str:
    """
    Return one of:
    - directory
    - finance
    - clerk
    - assessor
    - tax
    - building
    - planning
    - departments_root
    - other
    """
    role = normalize_key(role_normalized)
    family = normalize_key(role_family)
    department = normalize_key(department_normalized)
    title_norm = normalize_key(title)
    path_norm = canonicalize_path_for_grouping(path)
    blob = " ".join(part for part in (role, family, department, title_norm, path_norm) if part)

    if "directory" in path_norm:
        return "directory"

    if (
        role in {"finance director", "treasurer"}
        or family == "finance"
        or any(token in blob for token in ("finance", "treasurer", "comptroller"))
    ):
        return "finance"

    if role in {"town clerk", "city clerk"} or family == "town_clerk" or "clerk" in blob:
        return "clerk"

    if role == "assessor" or family == "assessor" or "assessor" in blob:
        return "assessor"

    if role == "tax collector" or family == "tax_collector" or "tax-collector" in blob or " tax " in f" {blob} ":
        return "tax"

    if (
        role == "building official"
        or family == "building"
        or any(token in blob for token in ("building", "inspectional", "inspection", "code enforcement"))
    ):
        return "building"

    if (
        role == "planner"
        or family == "planning_zoning"
        or any(token in blob for token in ("planning", "zoning", "land-use", "land use"))
    ):
        return "planning"

    if any(token in path_norm for token in ("/departments", "/government")):
        return "departments_root"

    return "other"


def is_numeric_slug(path: str) -> int:
    return 1 if re.search(r"/\d+(?:/|$)", path or "") else 0


def is_clean_slug(path: str) -> int:
    canonical = canonicalize_path_for_grouping(path)
    if canonical in {"", "/"}:
        return 0
    if is_numeric_slug(canonical):
        return 0
    if not re.fullmatch(r"/[a-z0-9][a-z0-9\-\/\.]*", canonical):
        return 0
    return 1


def extract_domain(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"https://{raw.lstrip('/')}")
    return str(parsed.netloc or "").strip().lower()


def build_municipality_path_inventory(rows: list[dict]) -> list[dict]:
    inventory_rows: list[dict] = []
    for row in rows:
        source_url = normalize_text(row.get("source_url"))
        normalized_path = normalize_source_path(source_url)
        if not normalized_path:
            continue
        canonical_path = canonicalize_path_for_grouping(normalized_path)
        category = infer_path_category(
            role_normalized=normalize_text(row.get("role_normalized")),
            role_family=normalize_text(row.get("role_family")),
            department_normalized=normalize_text(row.get("department_normalized")),
            title=normalize_text(row.get("title")),
            path=canonical_path,
        )
        inventory_rows.append(
            {
                "municipality_id": normalize_text(row.get("municipality_id")),
                "source_url": source_url,
                "normalized_path": normalized_path,
                "canonical_path": canonical_path,
                "category": category,
                "role_normalized": normalize_text(row.get("role_normalized")),
                "role_family": normalize_text(row.get("role_family")),
                "department_normalized": normalize_text(row.get("department_normalized")),
                "page_type": normalize_text(row.get("page_type")),
            }
        )

    inventory_rows.sort(
        key=lambda row: (
            str(row["municipality_id"]),
            str(row["category"]),
            str(row["canonical_path"]),
            str(row["source_url"]),
        )
    )
    return inventory_rows


def score_candidate_row(
    *,
    canonical_path: str,
    category: str,
    municipality_count: int,
    winner_backed_count: int,
    preferred_page_type_rows: int,
) -> int:
    score = 0
    if municipality_count >= 2:
        score += 3
    if winner_backed_count > 0:
        score += 3
    if category in HIGH_VALUE_CATEGORIES:
        score += 2
    if preferred_page_type_rows > 0:
        score += 1
    if canonical_path in GENERIC_NOISE_PATHS:
        score -= 2
    if any(token in canonical_path for token in NOISY_PATH_TOKENS):
        score -= 2
    return score


def candidate_recommendation(
    *,
    canonical_path: str,
    score: int,
) -> str:
    if canonical_path in GENERIC_NOISE_PATHS:
        return "too_generic"
    if score >= 7:
        return "add_to_probe_list"
    if score <= 1:
        return "too_generic"
    return "review_manually"


def build_candidate_rows(inventory_rows: list[dict], winner_ids: set[str]) -> list[dict]:
    aggregate: dict[tuple[str, str], dict] = {}
    for row in inventory_rows:
        category = str(row.get("category") or "other")
        canonical_path = str(row.get("canonical_path") or "/")
        key = (category, canonical_path)
        state = aggregate.get(key)
        if state is None:
            state = {
                "category": category,
                "canonical_path": canonical_path,
                "municipality_ids": set(),
                "winner_backed_ids": set(),
                "row_count": 0,
                "preferred_page_type_rows": 0,
                "domains": set(),
                "example_paths": [],
                "example_municipalities": [],
            }
            aggregate[key] = state

        municipality_id = str(row.get("municipality_id") or "")
        source_url = str(row.get("source_url") or "")
        normalized_path = str(row.get("normalized_path") or "")
        page_type = normalize_key(row.get("page_type"))

        state["row_count"] += 1
        if municipality_id:
            state["municipality_ids"].add(municipality_id)
            if municipality_id in winner_ids:
                state["winner_backed_ids"].add(municipality_id)
            if municipality_id not in state["example_municipalities"] and len(state["example_municipalities"]) < 8:
                state["example_municipalities"].append(municipality_id)
        if page_type in {"staff_directory", "department_page"}:
            state["preferred_page_type_rows"] += 1
        domain = extract_domain(source_url)
        if domain:
            state["domains"].add(domain)
        if normalized_path and normalized_path not in state["example_paths"] and len(state["example_paths"]) < 5:
            state["example_paths"].append(normalized_path)

    out: list[dict] = []
    for state in aggregate.values():
        category = str(state["category"])
        canonical_path = str(state["canonical_path"])
        municipality_count = len(state["municipality_ids"])
        winner_backed_count = len(state["winner_backed_ids"])
        row_count = int(state["row_count"])
        preferred_page_type_rows = int(state["preferred_page_type_rows"])
        score = score_candidate_row(
            canonical_path=canonical_path,
            category=category,
            municipality_count=municipality_count,
            winner_backed_count=winner_backed_count,
            preferred_page_type_rows=preferred_page_type_rows,
        )
        recommendation = candidate_recommendation(canonical_path=canonical_path, score=score)

        out.append(
            {
                "category": category,
                "canonical_path": canonical_path,
                "score": score,
                "municipality_count": municipality_count,
                "row_count": row_count,
                "winner_backed_count": winner_backed_count,
                "is_clean_slug": is_clean_slug(canonical_path),
                "is_numeric_slug": is_numeric_slug(canonical_path),
                "example_paths": "; ".join(state["example_paths"]),
                "example_municipalities": "; ".join(state["example_municipalities"]),
                "recommendation": recommendation,
            }
        )

    category_order_index = {category: idx for idx, category in enumerate(CATEGORY_ORDER)}
    out.sort(
        key=lambda row: (
            category_order_index.get(str(row["category"]), 999),
            -int(row["score"]),
            -int(row["municipality_count"]),
            -int(row["row_count"]),
            str(row["canonical_path"]),
        )
    )
    return out


def recommend_probe_paths(candidate_rows: list[dict]) -> dict[str, list[str]]:
    """
    Build a suggested expansion dictionary by category.
    Only include:
    - high score
    - low noise
    - useful municipal coverage
    """
    out: dict[str, list[str]] = {category: [] for category in CATEGORY_ORDER}
    grouped: dict[str, list[dict]] = defaultdict(list)

    for row in candidate_rows:
        category = str(row.get("category") or "")
        if category not in out:
            continue
        score = int(row.get("score") or 0)
        municipality_count = int(row.get("municipality_count") or 0)
        recommendation = str(row.get("recommendation") or "")
        numeric_slug = int(row.get("is_numeric_slug") or 0)
        if recommendation != "add_to_probe_list":
            continue
        if score < 6:
            continue
        if municipality_count < 2:
            continue
        if numeric_slug == 1 and municipality_count < 3:
            continue
        grouped[category].append(row)

    for category in CATEGORY_ORDER:
        rows = grouped.get(category, [])
        rows.sort(
            key=lambda row: (
                0 if int(row.get("is_clean_slug") or 0) == 1 else 1,
                -int(row.get("score") or 0),
                -int(row.get("municipality_count") or 0),
                -int(row.get("row_count") or 0),
                str(row.get("canonical_path") or ""),
            )
        )
        seen: set[str] = set()
        selected: list[str] = []
        for row in rows:
            path = str(row.get("canonical_path") or "")
            if not path or path in seen:
                continue
            selected.append(path)
            seen.add(path)
            if len(selected) >= 10:
                break
        out[category] = selected
    return out


def main() -> None:
    args = parse_args()
    conn = get_connection(args.db)
    try:
        scope_ids = select_scope_municipality_ids(
            conn=conn,
            batch_id=args.batch_id,
            manifest_path=args.manifest,
            platform=args.platform,
            seed_csv_path=args.seed_csv,
        )
        winner_ids = fetch_winner_municipality_ids(conn, scope_ids)
        source_rows = fetch_successful_contact_rows(conn, winner_ids)
    finally:
        conn.close()

    inventory_rows = build_municipality_path_inventory(source_rows)
    candidate_rows = build_candidate_rows(inventory_rows, winner_ids)
    suggestion_payload = recommend_probe_paths(candidate_rows)

    out_dir = Path(args.outputs_root) / args.batch_id if args.batch_id else Path(args.analysis_root)
    out_dir.mkdir(parents=True, exist_ok=True)

    inventory_path = out_dir / "municipality_path_inventory.csv"
    candidate_path = out_dir / "candidate_deep_path_patterns.csv"
    suggestion_path = out_dir / "suggested_deep_probe_expansion.json"

    inventory_count = write_csv(inventory_path, inventory_rows, INVENTORY_FIELDS)
    candidate_count = write_csv(candidate_path, candidate_rows, CANDIDATE_FIELDS)
    write_json(suggestion_path, suggestion_payload)

    print("Deep-path pattern discovery complete")
    print(f"Municipalities in scope: {len(scope_ids)}")
    print(f"Winner-backed municipalities: {len(winner_ids)}")
    print(f"municipality_path_inventory.csv: {inventory_count} rows -> {inventory_path}")
    print(f"candidate_deep_path_patterns.csv: {candidate_count} rows -> {candidate_path}")
    print(f"suggested_deep_probe_expansion.json -> {suggestion_path}")


if __name__ == "__main__":
    main()
