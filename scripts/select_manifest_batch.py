from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.batch_manifest import ensure_batch_qa_scaffold, load_selected_manifest_rows
from src.batch_manifest import filter_rows_by_platform, load_seed_platform_map


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="List municipalities to process from a batch manifest.")
    parser.add_argument("--batch-id", required=True, help="Batch ID to select, e.g. batch_1")
    parser.add_argument(
        "--manifest",
        default=str(ROOT / "data" / "manifests" / "civicplus_manifest.csv"),
        help="Path to manifest CSV.",
    )
    parser.add_argument("--status", default="pending", help="Manifest status filter (default: pending).")
    parser.add_argument(
        "--platform",
        default=None,
        help="Optional platform filter from seed CSV, e.g. CivicPlus.",
    )
    parser.add_argument(
        "--seed-csv",
        default=str(ROOT / "config" / "municipalities_seed.csv"),
        help="Seed CSV containing municipality_id + platform columns.",
    )
    parser.add_argument(
        "--outputs-root",
        default=str(ROOT / "outputs" / "batches"),
        help="Base batch output folder.",
    )
    parser.add_argument(
        "--init-qa-scaffold",
        action="store_true",
        help="Create outputs/batches/<batch_id>/ QA placeholder files.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    selected = load_selected_manifest_rows(
        manifest_path=args.manifest,
        batch_id=args.batch_id,
        status=args.status,
    )
    excluded_by_platform: list[str] = []
    if args.platform:
        platform_map = load_seed_platform_map(args.seed_csv)
        selected, excluded_by_platform = filter_rows_by_platform(selected, platform_map, args.platform)

    label = f"Batch {args.batch_id} ({args.status}) municipalities"
    if args.platform:
        label += f" [platform={args.platform}]"
    print(f"{label}: {len(selected)}")
    if excluded_by_platform:
        print("Excluded by platform filter:")
        for municipality_id in excluded_by_platform:
            print(municipality_id)
    for row in selected:
        print(
            ",".join(
                [
                    row["municipality_id"],
                    row["town_name"],
                    row["county"],
                    f"priority={row['priority']}",
                ]
            )
        )

    if args.init_qa_scaffold:
        scaffold_paths = ensure_batch_qa_scaffold(args.outputs_root, args.batch_id)
        print("QA scaffold:")
        for filename, path in scaffold_paths.items():
            print(f"{filename}: {path}")


if __name__ == "__main__":
    main()
