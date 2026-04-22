from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize one batch's QA outputs grouped by vendor/platform.")
    parser.add_argument("--batch-id", required=True, help="Batch ID to summarize.")
    parser.add_argument(
        "--manifest",
        default=str(ROOT / "data" / "manifests" / "non_civicplus_manifest.csv"),
        help="Manifest used for the batch.",
    )
    parser.add_argument(
        "--seed-csv",
        default=str(ROOT / "config" / "municipalities_seed.csv"),
        help="Seed CSV containing municipality_id + platform.",
    )
    parser.add_argument(
        "--outputs-root",
        default=str(ROOT / "outputs" / "batches"),
        help="Batch output root.",
    )
    parser.add_argument(
        "--out-summary",
        default=None,
        help="Optional output CSV path for vendor summary (default: outputs/batches/<batch_id>/qa_vendor_summary.csv).",
    )
    parser.add_argument(
        "--out-town-review",
        default=None,
        help=(
            "Optional output CSV path for town-level diagnostics "
            "(default: outputs/batches/<batch_id>/qa_town_diagnostic_review.csv)."
        ),
    )
    return parser.parse_args()


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [{k: (v or "").strip() for k, v in row.items()} for row in csv.DictReader(handle)]


def to_int(value: str | None) -> int:
    text = (value or "").strip()
    if not text:
        return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def load_batch_manifest_scope(manifest_path: Path, batch_id: str) -> list[dict[str, str]]:
    rows = read_csv_rows(manifest_path)
    wanted = batch_id.strip().lower()
    selected = [row for row in rows if (row.get("batch_id") or "").strip().lower() == wanted]
    if not selected:
        raise SystemExit(f"No manifest rows found for batch_id={batch_id} in {manifest_path}")
    return selected


def load_platform_map(seed_csv: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in read_csv_rows(seed_csv):
        municipality_id = (row.get("municipality_id") or "").strip()
        if not municipality_id:
            continue
        out[municipality_id] = (row.get("platform") or "").strip()
    return out


def write_csv(path: Path, rows: list[dict[str, str | int]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def join_unique(values: list[str]) -> str:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        clean = value.strip()
        if not clean:
            continue
        if clean in seen:
            continue
        seen.add(clean)
        ordered.append(clean)
    return "; ".join(ordered)


def normalize_vendor_name(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        return "(blank)"

    lowered = value.lower()
    if lowered.startswith("quasar internet solutions"):
        return "Quasar Internet Solutions"

    canonical_map = {
        "apptegy": "Apptegy",
        "aptuitiv": "Aptuitiv",
        "awr web design": "AWR Web Design",
        "catalis": "Catalis",
        "catalisgov": "Catalisgov",
        "civiclift": "CivicLift",
        "corebt": "CoreBT",
        "egovlink": "egovlink",
        "evogov": "EvoGov",
        "finalsite": "finalsite",
        "granicus": "Granicus",
        "ifsight": "ifsight",
        "purpledog": "PurpleDog",
        "quasar": "Quasar",
        "revize": "Revize",
        "town web": "TownWeb",
        "townweb": "TownWeb",
        "web solutions": "Web Solutions",
        "wordpress": "Wordpress",
    }
    return canonical_map.get(lowered, value)


def main() -> None:
    args = parse_args()

    batch_dir = Path(args.outputs_root) / args.batch_id
    out_summary = Path(args.out_summary) if args.out_summary else (batch_dir / "qa_vendor_summary.csv")
    out_town_review = (
        Path(args.out_town_review) if args.out_town_review else (batch_dir / "qa_town_diagnostic_review.csv")
    )

    manifest_rows = load_batch_manifest_scope(Path(args.manifest), args.batch_id)
    platform_map = load_platform_map(Path(args.seed_csv))

    summary_rows = read_csv_rows(batch_dir / "qa_batch_summary.csv")
    diagnostic_rows = read_csv_rows(batch_dir / "qa_crawl_diagnostics.csv")
    suspicious_rows = read_csv_rows(batch_dir / "qa_suspicious_winners.csv")
    missing_rows = read_csv_rows(batch_dir / "qa_missing_key_roles.csv")
    blocked_rows = read_csv_rows(batch_dir / "qa_blocked_towns.csv")
    manual_review_rows = read_csv_rows(batch_dir / "qa_manual_review.csv")

    summary_by_id = {row["municipality_id"]: row for row in summary_rows if row.get("municipality_id")}
    diagnostics_by_id = {row["municipality_id"]: row for row in diagnostic_rows if row.get("municipality_id")}
    blocked_reason_by_id = {
        row["municipality_id"]: (row.get("blocked_reason") or "")
        for row in blocked_rows
        if row.get("municipality_id")
    }

    suspicious_by_id: dict[str, int] = defaultdict(int)
    for row in suspicious_rows:
        municipality_id = (row.get("municipality_id") or "").strip()
        if municipality_id:
            suspicious_by_id[municipality_id] += 1

    missing_group_by_id: dict[str, int] = {}
    for row in missing_rows:
        municipality_id = (row.get("municipality_id") or "").strip()
        if municipality_id:
            missing_group_by_id[municipality_id] = to_int(row.get("missing_group_count"))

    review_interpretations_by_id: dict[str, list[str]] = defaultdict(list)
    for row in manual_review_rows:
        municipality_id = (row.get("municipality_id") or "").strip()
        coverage = (row.get("coverage_interpretation") or "").strip()
        if municipality_id and coverage:
            review_interpretations_by_id[municipality_id].append(coverage)

    vendor_bucket: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "municipalities_total": 0,
            "municipalities_with_any_contacts": 0,
            "municipalities_zero_yield": 0,
            "municipalities_blocked_or_forbidden": 0,
            "municipalities_with_suspicious_winners": 0,
            "municipalities_with_missing_role_groups": 0,
        }
    )

    town_review_rows: list[dict[str, str | int]] = []
    for manifest_row in manifest_rows:
        municipality_id = manifest_row["municipality_id"]
        town_name = manifest_row["town_name"]
        vendor_raw = (platform_map.get(municipality_id) or "").strip()
        vendor = normalize_vendor_name(vendor_raw)

        counts = summary_by_id.get(municipality_id, {})
        raw_contacts = to_int(counts.get("raw_contacts"))
        clean_contacts = to_int(counts.get("clean_contacts"))
        role_winners = to_int(counts.get("role_winners"))
        missing_group_count = int(missing_group_by_id.get(municipality_id, 0))
        suspicious_count = int(suspicious_by_id.get(municipality_id, 0))

        diagnostics = diagnostics_by_id.get(municipality_id, {})
        diagnostic_class = (diagnostics.get("diagnostic_class") or "").strip() or "missing_diagnostic"
        blocked_reason = (blocked_reason_by_id.get(municipality_id) or "").strip()
        coverage_interpretation = join_unique(review_interpretations_by_id.get(municipality_id, []))

        has_any_contacts = raw_contacts > 0 or clean_contacts > 0
        zero_yield = raw_contacts == 0 and clean_contacts == 0 and role_winners == 0
        blocked_or_forbidden = diagnostic_class == "blocked_or_forbidden"

        stats = vendor_bucket[vendor]
        stats["municipalities_total"] += 1
        if has_any_contacts:
            stats["municipalities_with_any_contacts"] += 1
        if zero_yield:
            stats["municipalities_zero_yield"] += 1
        if blocked_or_forbidden:
            stats["municipalities_blocked_or_forbidden"] += 1
        if suspicious_count > 0:
            stats["municipalities_with_suspicious_winners"] += 1
        if missing_group_count > 0:
            stats["municipalities_with_missing_role_groups"] += 1

        town_review_rows.append(
            {
                "municipality_id": municipality_id,
                "town_name": town_name,
                "vendor_platform": vendor,
                "diagnostic_class": diagnostic_class,
                "blocked_reason": blocked_reason,
                "raw_contacts": raw_contacts,
                "clean_contacts": clean_contacts,
                "role_winners": role_winners,
                "missing_group_count": missing_group_count,
                "coverage_interpretation": coverage_interpretation,
            }
        )

    vendor_rows: list[dict[str, str | int]] = []
    for vendor in sorted(vendor_bucket):
        row = {"vendor_platform": vendor, **vendor_bucket[vendor]}
        vendor_rows.append(row)

    write_csv(
        out_summary,
        vendor_rows,
        [
            "vendor_platform",
            "municipalities_total",
            "municipalities_with_any_contacts",
            "municipalities_zero_yield",
            "municipalities_blocked_or_forbidden",
            "municipalities_with_suspicious_winners",
            "municipalities_with_missing_role_groups",
        ],
    )
    write_csv(
        out_town_review,
        town_review_rows,
        [
            "municipality_id",
            "town_name",
            "vendor_platform",
            "diagnostic_class",
            "blocked_reason",
            "raw_contacts",
            "clean_contacts",
            "role_winners",
            "missing_group_count",
            "coverage_interpretation",
        ],
    )

    print(f"Vendor summary rows: {len(vendor_rows)} -> {out_summary}")
    print(f"Town review rows: {len(town_review_rows)} -> {out_town_review}")
    print("vendor_platform,municipalities_total,municipalities_with_any_contacts,municipalities_zero_yield,municipalities_blocked_or_forbidden,municipalities_with_suspicious_winners,municipalities_with_missing_role_groups")
    for row in vendor_rows:
        print(
            ",".join(
                [
                    str(row["vendor_platform"]),
                    str(row["municipalities_total"]),
                    str(row["municipalities_with_any_contacts"]),
                    str(row["municipalities_zero_yield"]),
                    str(row["municipalities_blocked_or_forbidden"]),
                    str(row["municipalities_with_suspicious_winners"]),
                    str(row["municipalities_with_missing_role_groups"]),
                ]
            )
        )


if __name__ == "__main__":
    main()
