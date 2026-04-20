from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db import get_connection
from src.batch_manifest import load_manifest_rows, load_seed_platform_map

ROLE_GROUPS: dict[str, tuple[str, ...]] = {
    "finance": ("Finance Director", "Treasurer"),
    "clerk": ("Town Clerk",),
    "building": ("Building Official",),
    "planning": ("Planner", "Land Use", "Zoning Enforcement Officer", "ZEO"),
    "assessment": ("Assessor",),
    "tax": ("Tax Collector",),
}

ROLE_GROUP_FAMILY_MATCH: dict[str, str] = {
    "finance": "finance",
    "clerk": "town_clerk",
    "building": "building",
    "planning": "planning_zoning",
    "assessment": "assessor",
    "tax": "tax_collector",
}

ROLE_GROUP_DEPARTMENT_MATCH: dict[str, tuple[str, ...]] = {
    "finance": ("finance",),
    "clerk": ("town clerk", "clerk"),
    "building": ("building",),
    "planning": ("planning & zoning", "planning", "land use", "zoning"),
    "assessment": ("assessor",),
    "tax": ("tax collector", "tax"),
}

ROLE_GROUP_SIGNAL_TOKENS: dict[str, tuple[str, ...]] = {
    "finance": (
        "finance",
        "financial",
        "chief financial officer",
        "cfo",
        "comptroller",
        "director of administrative services",
        "finance administrator",
        "business manager",
        "treasurer",
    ),
    "clerk": ("clerk", "town clerk", "city clerk", "borough clerk"),
    "building": (
        "building",
        "inspection",
        "inspector",
        "code enforcement",
        "building official",
    ),
    "planning": ("planning", "planner", "land use", "zoning", "zoning enforcement", "zeo"),
    "assessment": ("assessor", "assessment", "appraiser"),
    "tax": ("tax collector", "collector of taxes", "revenue collector", "tax office"),
}

CANDIDATE_FIELDS = (
    "role_group_target",
    "proposed_role_normalized",
    "raw_title",
    "raw_department",
    "raw_source_context_sample",
    "municipality_count",
    "contact_count",
    "missing_group_town_count",
    "normalized_match_count",
    "score",
    "example_municipalities",
    "recommendation",
)

INVENTORY_FIELDS = (
    "municipality_id",
    "town_name",
    "missing_role_groups",
    "title",
    "department",
    "source_context",
    "role_family",
    "role_normalized",
    "department_normalized",
    "count_rows",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover likely missing role-title synonyms from existing contact data.")
    parser.add_argument("--db", default=str(ROOT / "database" / "master.sqlite"), help="SQLite DB path.")
    parser.add_argument(
        "--batch-id",
        default=None,
        help="Optional manifest batch_id scope (e.g. batch_3). If omitted, all municipalities are analyzed.",
    )
    parser.add_argument(
        "--manifest",
        default=str(ROOT / "data" / "manifests" / "civicplus_manifest.csv"),
        help="Manifest CSV path.",
    )
    parser.add_argument(
        "--platform",
        default=None,
        help="Optional platform filter (e.g. CivicPlus) using municipalities_seed.csv.",
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
        default=str(ROOT / "outputs" / "analysis" / "role_synonyms"),
        help="Output folder when no --batch-id is provided.",
    )
    return parser.parse_args()


def write_csv(path: Path, rows: list[dict], fieldnames: tuple[str, ...]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames))
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})
    return len(rows)


def placeholders(size: int) -> str:
    return ",".join("?" for _ in range(size))


def normalize_text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def normalize_key(value: object) -> str:
    return normalize_text(value).lower()


def keyword_in_text(text: str, token: str) -> bool:
    return token in text


def select_scope_municipalities(
    conn,
    batch_id: str | None,
    manifest_path: str | Path,
    platform: str | None,
    seed_csv_path: str | Path,
) -> list[dict[str, str]]:
    if not batch_id:
        rows = conn.execute(
            """
            SELECT municipality_id, name AS town_name
            FROM municipalities
            ORDER BY municipality_id
            """
        ).fetchall()
        return [{"municipality_id": str(row["municipality_id"]), "town_name": str(row["town_name"] or "")} for row in rows]

    manifest_rows = load_manifest_rows(manifest_path)
    selected = [
        row for row in manifest_rows
        if row["batch_id"].strip().lower() == batch_id.strip().lower()
    ]
    if not selected:
        raise SystemExit(f"No municipalities found in manifest for batch_id={batch_id}")

    selected_ids = [row["municipality_id"] for row in selected]
    selected_id_set = set(selected_ids)
    if platform:
        platform_map = load_seed_platform_map(seed_csv_path)
        wanted = platform.strip().lower()
        selected_ids = [
            municipality_id
            for municipality_id in selected_ids
            if (platform_map.get(municipality_id) or "").strip().lower() == wanted
        ]
        selected_id_set = set(selected_ids)
        if not selected_ids:
            raise SystemExit(f"No municipalities left in scope after platform filter={platform}")

    rows = conn.execute(
        f"""
        SELECT municipality_id, name AS town_name
        FROM municipalities
        WHERE municipality_id IN ({placeholders(len(selected_ids))})
        ORDER BY municipality_id
        """,
        tuple(selected_ids),
    ).fetchall()
    out = [{"municipality_id": str(row["municipality_id"]), "town_name": str(row["town_name"] or "")} for row in rows]
    missing = sorted(selected_id_set - {row["municipality_id"] for row in out})
    if missing:
        print("Warning: municipalities in manifest but missing from DB:", ", ".join(missing))
    return out


def ensure_required_views(conn) -> None:
    checks = (
        ("vw_best_role_per_town", "view"),
        ("contacts", "table"),
    )
    for name, object_type in checks:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = ? AND name = ? LIMIT 1",
            (object_type, name),
        ).fetchone()
        if row is None:
            raise SystemExit(f"Required {object_type} not found: {name}")


def fetch_role_winners(conn, municipality_ids: list[str]) -> list[dict]:
    if not municipality_ids:
        return []
    rows = conn.execute(
        f"""
        SELECT municipality_id, role_normalized
        FROM vw_best_role_per_town
        WHERE municipality_id IN ({placeholders(len(municipality_ids))})
        """,
        tuple(municipality_ids),
    ).fetchall()
    return [dict(row) for row in rows]


def compute_missing_groups(
    municipalities: list[dict[str, str]],
    winners: list[dict],
) -> dict[str, set[str]]:
    present_roles_by_town: dict[str, set[str]] = defaultdict(set)
    for row in winners:
        municipality_id = str(row.get("municipality_id") or "")
        role = normalize_text(row.get("role_normalized"))
        if municipality_id and role:
            present_roles_by_town[municipality_id].add(role)

    missing_groups: dict[str, set[str]] = {}
    for municipality in municipalities:
        municipality_id = municipality["municipality_id"]
        present = present_roles_by_town.get(municipality_id, set())
        missing: set[str] = set()
        for role_group, role_values in ROLE_GROUPS.items():
            if not any(role in present for role in role_values):
                missing.add(role_group)
        missing_groups[municipality_id] = missing
    return missing_groups


def fetch_contacts_for_scope(conn, municipality_ids: list[str]) -> list[dict]:
    if not municipality_ids:
        return []
    rows = conn.execute(
        f"""
        SELECT
            c.municipality_id,
            m.name AS town_name,
            c.title,
            c.department,
            c.source_context,
            c.role_family,
            c.role_normalized,
            c.department_normalized,
            COALESCE(c.is_likely_noise, 0) AS is_likely_noise
        FROM contacts c
        JOIN municipalities m
          ON m.municipality_id = c.municipality_id
        WHERE c.municipality_id IN ({placeholders(len(municipality_ids))})
        """,
        tuple(municipality_ids),
    ).fetchall()
    return [dict(row) for row in rows]


def build_role_gap_inventory(
    contacts_rows: list[dict],
    missing_groups_by_town: dict[str, set[str]],
) -> list[dict[str, str | int]]:
    aggregate: dict[tuple[str, ...], int] = defaultdict(int)
    for row in contacts_rows:
        municipality_id = str(row.get("municipality_id") or "")
        missing_groups = missing_groups_by_town.get(municipality_id, set())
        if not missing_groups:
            continue

        title = normalize_text(row.get("title"))
        department = normalize_text(row.get("department"))
        source_context = normalize_text(row.get("source_context"))
        if not title and not department:
            continue
        key = (
            municipality_id,
            normalize_text(row.get("town_name")),
            "; ".join(sorted(missing_groups)),
            title,
            department,
            source_context,
            normalize_text(row.get("role_family")),
            normalize_text(row.get("role_normalized")),
            normalize_text(row.get("department_normalized")),
        )
        aggregate[key] += 1

    out: list[dict[str, str | int]] = []
    for key, count in aggregate.items():
        (
            municipality_id,
            town_name,
            missing_groups,
            title,
            department,
            source_context,
            role_family,
            role_normalized,
            department_normalized,
        ) = key
        out.append(
            {
                "municipality_id": municipality_id,
                "town_name": town_name,
                "missing_role_groups": missing_groups,
                "title": title,
                "department": department,
                "source_context": source_context,
                "role_family": role_family,
                "role_normalized": role_normalized,
                "department_normalized": department_normalized,
                "count_rows": count,
            }
        )
    out.sort(key=lambda row: (str(row["municipality_id"]), -int(row["count_rows"]), str(row["title"])))
    return out


def target_group_signal_match(group: str, row: dict) -> bool:
    family = normalize_key(row.get("role_family"))
    department_normalized = normalize_key(row.get("department_normalized"))
    title = normalize_key(row.get("title"))
    department = normalize_key(row.get("department"))
    source_context = normalize_key(row.get("source_context"))
    blob = " ".join(part for part in (title, department, source_context) if part).strip()

    if family == ROLE_GROUP_FAMILY_MATCH[group]:
        return True
    if any(department_normalized == token for token in ROLE_GROUP_DEPARTMENT_MATCH[group]):
        return True
    if any(keyword_in_text(blob, token) for token in ROLE_GROUP_SIGNAL_TOKENS[group]):
        return True
    return False


def recommendation_for_candidate(group: str, blob: str, has_noise: bool) -> str:
    if has_noise or "board" in blob or "page" in blob:
        return "likely_noise"
    if group == "finance":
        if "treasurer" in blob:
            return "map_to_treasurer"
        if any(token in blob for token in (
            "finance",
            "financial",
            "chief financial officer",
            "cfo",
            "comptroller",
            "administrative services",
            "business manager",
            "finance administrator",
        )):
            return "map_to_finance_director"
    if group == "clerk" and "clerk" in blob:
        return "map_to_town_clerk"
    if group == "building" and any(token in blob for token in ("building", "inspector", "inspection", "code enforcement")):
        return "map_to_building_official"
    if group == "planning" and any(token in blob for token in ("planning", "planner", "land use", "zoning", "zeo")):
        return "map_to_planner"
    return "review_manually"


def proposed_role_from_recommendation(group: str, recommendation: str) -> str:
    if recommendation == "map_to_treasurer":
        return "Treasurer"
    if group == "finance":
        return "Finance Director"
    if group == "clerk":
        return "Town Clerk"
    if group == "building":
        return "Building Official"
    if group == "planning":
        return "Planner"
    if group == "assessment":
        return "Assessor"
    if group == "tax":
        return "Tax Collector"
    return "Unknown"


def score_candidate(
    *,
    role_family_match_count: int,
    department_match_count: int,
    municipality_count: int,
    missing_group_town_count: int,
    title_blob: str,
    noise_count: int,
    normalized_match_count: int,
    contact_count: int,
) -> int:
    score = 0
    if role_family_match_count > 0:
        score += 3
    if department_match_count > 0:
        score += 3
    if municipality_count >= 2:
        score += 2
    if missing_group_town_count >= 1:
        score += 2
    if "board" in title_blob:
        score -= 3
    if "page" in title_blob:
        score -= 3
    if noise_count > 0:
        score -= 2
    if contact_count > 0 and normalized_match_count == contact_count:
        score -= 4
    return score


def build_candidate_synonyms(
    contacts_rows: list[dict],
    missing_groups_by_town: dict[str, set[str]],
) -> list[dict[str, str | int]]:
    aggregate: dict[tuple[str, str, str], dict[str, object]] = {}

    for row in contacts_rows:
        municipality_id = str(row.get("municipality_id") or "")
        missing_groups = missing_groups_by_town.get(municipality_id, set())
        if not missing_groups:
            continue

        title = normalize_text(row.get("title"))
        department = normalize_text(row.get("department"))
        if not title and not department:
            continue

        role_normalized = normalize_text(row.get("role_normalized"))
        role_family = normalize_key(row.get("role_family"))
        department_normalized = normalize_key(row.get("department_normalized"))
        source_context = normalize_text(row.get("source_context"))
        is_likely_noise = int(row.get("is_likely_noise") or 0)

        for group in sorted(missing_groups):
            if group not in ROLE_GROUPS:
                continue
            if not target_group_signal_match(group, row):
                continue

            title_key = normalize_key(title)
            department_key = normalize_key(department)
            candidate_key = (group, title_key, department_key)
            candidate = aggregate.get(candidate_key)
            if candidate is None:
                candidate = {
                    "role_group_target": group,
                    "raw_title": title,
                    "raw_department": department,
                    "raw_source_context_sample": source_context,
                    "municipalities": set(),
                    "contact_count": 0,
                    "missing_group_towns": set(),
                    "normalized_match_count": 0,
                    "role_family_match_count": 0,
                    "department_match_count": 0,
                    "noise_count": 0,
                }
                aggregate[candidate_key] = candidate

            candidate["municipalities"].add(municipality_id)
            candidate["missing_group_towns"].add(municipality_id)
            candidate["contact_count"] = int(candidate["contact_count"]) + 1
            if role_normalized in ROLE_GROUPS[group]:
                candidate["normalized_match_count"] = int(candidate["normalized_match_count"]) + 1
            if role_family == ROLE_GROUP_FAMILY_MATCH[group]:
                candidate["role_family_match_count"] = int(candidate["role_family_match_count"]) + 1
            if department_normalized in ROLE_GROUP_DEPARTMENT_MATCH[group]:
                candidate["department_match_count"] = int(candidate["department_match_count"]) + 1
            if is_likely_noise > 0:
                candidate["noise_count"] = int(candidate["noise_count"]) + 1
            if not str(candidate.get("raw_source_context_sample") or "") and source_context:
                candidate["raw_source_context_sample"] = source_context

    out: list[dict[str, str | int]] = []
    for candidate in aggregate.values():
        group = str(candidate["role_group_target"])
        raw_title = str(candidate["raw_title"] or "")
        raw_department = str(candidate["raw_department"] or "")
        source_context_sample = str(candidate["raw_source_context_sample"] or "")
        municipality_ids = sorted(str(municipality_id) for municipality_id in candidate["municipalities"])
        municipality_count = len(municipality_ids)
        contact_count = int(candidate["contact_count"])
        missing_group_town_count = len(candidate["missing_group_towns"])
        normalized_match_count = int(candidate["normalized_match_count"])
        role_family_match_count = int(candidate["role_family_match_count"])
        department_match_count = int(candidate["department_match_count"])
        noise_count = int(candidate["noise_count"])
        title_blob = normalize_key(f"{raw_title} {raw_department}")
        recommendation = recommendation_for_candidate(group, title_blob, has_noise=noise_count > 0)
        proposed_role_normalized = proposed_role_from_recommendation(group, recommendation)
        score = score_candidate(
            role_family_match_count=role_family_match_count,
            department_match_count=department_match_count,
            municipality_count=municipality_count,
            missing_group_town_count=missing_group_town_count,
            title_blob=title_blob,
            noise_count=noise_count,
            normalized_match_count=normalized_match_count,
            contact_count=contact_count,
        )

        out.append(
            {
                "role_group_target": group,
                "proposed_role_normalized": proposed_role_normalized,
                "raw_title": raw_title,
                "raw_department": raw_department,
                "raw_source_context_sample": source_context_sample,
                "municipality_count": municipality_count,
                "contact_count": contact_count,
                "missing_group_town_count": missing_group_town_count,
                "normalized_match_count": normalized_match_count,
                "score": score,
                "example_municipalities": "; ".join(municipality_ids[:8]),
                "recommendation": recommendation,
            }
        )

    out.sort(
        key=lambda row: (
            -int(row["score"]),
            -int(row["municipality_count"]),
            -int(row["contact_count"]),
            str(row["role_group_target"]),
            str(row["raw_title"]),
        )
    )
    return out


def main() -> None:
    args = parse_args()
    conn = get_connection(args.db)
    try:
        ensure_required_views(conn)
        municipalities = select_scope_municipalities(
            conn=conn,
            batch_id=args.batch_id,
            manifest_path=args.manifest,
            platform=args.platform,
            seed_csv_path=args.seed_csv,
        )
        municipality_ids = [row["municipality_id"] for row in municipalities]
        winners = fetch_role_winners(conn, municipality_ids)
        missing_groups_by_town = compute_missing_groups(municipalities, winners)
        contacts_rows = fetch_contacts_for_scope(conn, municipality_ids)
    finally:
        conn.close()

    inventory_rows = build_role_gap_inventory(contacts_rows, missing_groups_by_town)
    candidate_rows = build_candidate_synonyms(contacts_rows, missing_groups_by_town)

    if args.batch_id:
        out_dir = Path(args.outputs_root) / args.batch_id
    else:
        out_dir = Path(args.analysis_root)
    out_dir.mkdir(parents=True, exist_ok=True)

    candidate_path = out_dir / "candidate_role_synonyms.csv"
    inventory_path = out_dir / "role_gap_title_inventory.csv"
    candidate_count = write_csv(candidate_path, candidate_rows, CANDIDATE_FIELDS)
    inventory_count = write_csv(inventory_path, inventory_rows, INVENTORY_FIELDS)

    print("Role synonym discovery complete")
    print(f"Municipalities in scope: {len(municipality_ids)}")
    print(f"candidate_role_synonyms.csv: {candidate_count} rows -> {candidate_path}")
    print(f"role_gap_title_inventory.csv: {inventory_count} rows -> {inventory_path}")


if __name__ == "__main__":
    main()
