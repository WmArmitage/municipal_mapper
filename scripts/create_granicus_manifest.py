from __future__ import annotations

import argparse
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_COLUMNS = (
    "municipality_id",
    "town_name",
    "county",
    "batch_id",
    "priority",
    "status",
    "notes",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a manifest slice for municipalities whose platform is Granicus."
    )
    parser.add_argument(
        "--seed-csv",
        default=str(ROOT / "config" / "municipalities_seed.csv"),
        help="Path to municipality seed CSV.",
    )
    parser.add_argument(
        "--out",
        default=str(ROOT / "data" / "manifests" / "granicus_manifest.csv"),
        help="Output manifest CSV path.",
    )
    parser.add_argument(
        "--batch-id",
        default="batch_granicus_vendor_probe",
        help="Batch ID to assign in the output manifest.",
    )
    parser.add_argument(
        "--status",
        default="pending",
        help="Status value to assign in the output manifest (default: pending).",
    )
    parser.add_argument(
        "--priority",
        default="1",
        help="Priority value to assign in the output manifest (default: 1).",
    )
    return parser.parse_args()


def create_manifest_rows(
    seed_csv: str | Path,
    batch_id: str,
    status: str,
    priority: str,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with Path(seed_csv).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            municipality_id = (row.get("municipality_id") or "").strip()
            if not municipality_id:
                continue
            platform = (row.get("platform") or "").strip().lower()
            if platform != "granicus":
                continue
            rows.append(
                {
                    "municipality_id": municipality_id,
                    "town_name": (row.get("name") or "").strip(),
                    "county": (row.get("county") or "").strip(),
                    "batch_id": str(batch_id).strip() or "batch_granicus_vendor_probe",
                    "priority": str(priority).strip() or "1",
                    "status": str(status).strip() or "pending",
                    "notes": "vendor_target=Granicus",
                }
            )
    rows.sort(key=lambda item: item["municipality_id"])
    return rows


def write_manifest(rows: list[dict[str, str]], out_path: str | Path) -> None:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(MANIFEST_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> None:
    args = parse_args()
    rows = create_manifest_rows(
        seed_csv=args.seed_csv,
        batch_id=args.batch_id,
        status=args.status,
        priority=args.priority,
    )
    write_manifest(rows, args.out)
    print(f"Created Granicus manifest rows: {len(rows)}")
    print(f"Output: {Path(args.out).resolve()}")
    print(f"Batch ID: {args.batch_id}")


if __name__ == "__main__":
    main()
