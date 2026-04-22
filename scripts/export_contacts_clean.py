from __future__ import annotations

import argparse
import csv
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.normalize import safe_phone_str

OUTPUT_COLUMNS = [
    "town_name",
    "role_group",
    "role_title",
    "name",
    "email",
    "phone",
    "department",
    "source_url",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export customer-facing municipal contacts CSV.")
    parser.add_argument(
        "--source",
        default=str(ROOT / "data" / "exports" / "contacts.csv"),
        help="Path to source contacts CSV.",
    )
    parser.add_argument(
        "--output",
        default=str(ROOT / "data" / "exports" / "contacts_clean.csv"),
        help="Path to cleaned contacts CSV output.",
    )
    return parser.parse_args()


def clean_text(value: object | None) -> str:
    return str(value or "").strip()


def to_title_case(text: str) -> str:
    if not text:
        return ""
    return " ".join(part.capitalize() for part in text.split())


def municipality_id_to_town_name(municipality_id: object | None) -> str:
    town = clean_text(municipality_id).lower()
    if town.startswith("ct_"):
        town = town[3:]
    town = town.replace("_", " ")
    return to_title_case(town)


def is_likely_noise_row(row: dict[str, str], has_noise_column: bool) -> bool:
    if not has_noise_column:
        return False
    value = clean_text(row.get("is_likely_noise", "")).lower()
    return value in {"1", "true", "yes", "y"}


def is_record_rank_one(row: dict[str, str], has_rank_column: bool) -> bool:
    if not has_rank_column:
        return True
    value = clean_text(row.get("record_rank", ""))
    if not value:
        return False
    try:
        return Decimal(value) == 1
    except InvalidOperation:
        return value == "1"


def role_title_for_display(row: dict[str, str]) -> str:
    role_title = clean_text(row.get("role_normalized"))
    if not role_title:
        role_title = clean_text(row.get("title"))
    role_title = role_title.replace("_", " ")
    return to_title_case(role_title)


def build_output_row(row: dict[str, str]) -> dict[str, str]:
    email = clean_text(row.get("email"))
    phone = str(safe_phone_str(row.get("phone")))
    return {
        "town_name": municipality_id_to_town_name(row.get("municipality_id")),
        "role_group": clean_text(row.get("role_family")),
        "role_title": role_title_for_display(row),
        "name": clean_text(row.get("name")),
        "email": email,
        "phone": phone,
        "department": clean_text(row.get("department")),
        "source_url": clean_text(row.get("source_url")),
    }


def main() -> None:
    args = parse_args()
    source_path = Path(args.source)
    output_path = Path(args.output)

    if not source_path.exists():
        raise FileNotFoundError(f"Source file not found: {source_path}")

    rows_out: list[dict[str, str]] = []
    towns: set[str] = set()

    with source_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or [])
        has_noise_column = "is_likely_noise" in fieldnames
        has_rank_column = "record_rank" in fieldnames
        has_coverage_status = "coverage_status" in fieldnames
        output_columns = OUTPUT_COLUMNS + (["coverage_status"] if has_coverage_status else [])

        for row in reader:
            if is_likely_noise_row(row, has_noise_column):
                continue
            if not is_record_rank_one(row, has_rank_column):
                continue

            out_row = build_output_row(row)
            if not out_row["email"] and not out_row["phone"]:
                continue

            if has_coverage_status:
                out_row["coverage_status"] = clean_text(row.get("coverage_status"))

            rows_out.append(out_row)
            if out_row["town_name"]:
                towns.add(out_row["town_name"])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=output_columns)
        writer.writeheader()
        writer.writerows(rows_out)

    print(f"rows written: {len(rows_out)}")
    print(f"towns represented: {len(towns)}")
    print(f"output path: {output_path.resolve()}")


if __name__ == "__main__":
    main()
