from __future__ import annotations

import csv
from pathlib import Path

MANIFEST_COLUMNS = (
    "municipality_id",
    "town_name",
    "county",
    "batch_id",
    "priority",
    "status",
    "notes",
)

QA_PLACEHOLDER_FILENAMES = (
    "qa_batch_summary.csv",
    "qa_role_winners.csv",
    "qa_suspicious_winners.csv",
    "qa_missing_key_roles.csv",
    "qa_manual_review.csv",
    "qa_crawl_diagnostics.csv",
)


def load_manifest_rows(manifest_path: str | Path) -> list[dict[str, str]]:
    path = Path(manifest_path)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        actual_columns = tuple(reader.fieldnames or ())
        if actual_columns != MANIFEST_COLUMNS:
            raise ValueError(
                f"Manifest columns must be exactly {MANIFEST_COLUMNS}; got {actual_columns}"
            )

        rows: list[dict[str, str]] = []
        for row in reader:
            clean = {column: (row.get(column) or "").strip() for column in MANIFEST_COLUMNS}
            if not clean["municipality_id"]:
                continue
            rows.append(clean)
    return rows


def select_batch_rows(
    rows: list[dict[str, str]],
    batch_id: str,
    status: str = "pending",
) -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    batch_norm = batch_id.strip().lower()
    status_norm = status.strip().lower()
    for row in rows:
        if row["batch_id"].strip().lower() != batch_norm:
            continue
        if row["status"].strip().lower() != status_norm:
            continue
        selected.append(row)
    return selected


def load_selected_manifest_rows(
    manifest_path: str | Path,
    batch_id: str,
    status: str = "pending",
) -> list[dict[str, str]]:
    rows = load_manifest_rows(manifest_path)
    return select_batch_rows(rows, batch_id=batch_id, status=status)


def load_seed_platform_map(seed_csv_path: str | Path) -> dict[str, str]:
    path = Path(seed_csv_path)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if "municipality_id" not in (reader.fieldnames or []):
            raise ValueError(f"Seed CSV missing municipality_id column: {path}")
        if "platform" not in (reader.fieldnames or []):
            raise ValueError(f"Seed CSV missing platform column: {path}")

        out: dict[str, str] = {}
        for row in reader:
            municipality_id = (row.get("municipality_id") or "").strip()
            if not municipality_id:
                continue
            out[municipality_id] = (row.get("platform") or "").strip()
    return out


def filter_rows_by_platform(
    rows: list[dict[str, str]],
    platform_by_municipality: dict[str, str],
    platform: str,
) -> tuple[list[dict[str, str]], list[str]]:
    wanted = platform.strip().lower()
    kept: list[dict[str, str]] = []
    excluded: list[str] = []
    for row in rows:
        municipality_id = row["municipality_id"]
        actual = (platform_by_municipality.get(municipality_id) or "").strip().lower()
        if actual == wanted:
            kept.append(row)
            continue
        excluded.append(municipality_id)
    return kept, excluded


def ensure_batch_qa_scaffold(
    outputs_root: str | Path,
    batch_id: str,
) -> dict[str, Path]:
    batch_dir = Path(outputs_root) / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)

    out: dict[str, Path] = {}
    for filename in QA_PLACEHOLDER_FILENAMES:
        path = batch_dir / filename
        if not path.exists():
            path.write_text("", encoding="utf-8")
        out[filename] = path
    return out
