from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db import get_connection, get_municipality
from src.granicus import run_granicus_strategy_for_municipality


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Granicus-only candidate/fetch/classification diagnostics for one municipality or URL."
    )
    parser.add_argument("municipality_id", nargs="?", help="Municipality ID from DB, e.g. ct_stamford")
    parser.add_argument("--url", default=None, help="Homepage URL override (if municipality_id not provided).")
    parser.add_argument("--db", default=str(ROOT / "database" / "master.sqlite"))
    parser.add_argument("--did-max", type=int, default=25)
    parser.add_argument("--max-total-candidates", type=int, default=140)
    parser.add_argument("--max-generated-candidates", type=int, default=220)
    return parser.parse_args()


def resolve_homepage(args: argparse.Namespace) -> tuple[str, str]:
    if args.url:
        return str(args.url), "url_override"
    if not args.municipality_id:
        raise SystemExit("Provide municipality_id or --url.")

    conn = get_connection(args.db)
    try:
        municipality = get_municipality(conn, args.municipality_id)
    finally:
        conn.close()
    if not municipality:
        raise SystemExit(f"Municipality not found: {args.municipality_id}")
    homepage = str(municipality.get("website_url") or "").strip()
    if not homepage:
        raise SystemExit(f"Municipality has no website_url: {args.municipality_id}")
    return homepage, args.municipality_id


def main() -> None:
    args = parse_args()
    homepage, label = resolve_homepage(args)
    result = run_granicus_strategy_for_municipality(
        municipality_homepage=homepage,
        did_max=args.did_max,
        max_total_candidates=args.max_total_candidates,
        max_generated_candidates=args.max_generated_candidates,
    )
    payload = {
        "target": label,
        "homepage": homepage,
        "candidate_urls_generated_count": result.get("candidate_urls_generated_count"),
        "candidate_urls_attempted_count": result.get("candidate_urls_attempted_count"),
        "http_responses_received_count": result.get("http_responses_received_count"),
        "pages_fetched_with_body_count": result.get("pages_fetched_with_body_count"),
        "pages_classified_blocked_count": result.get("pages_classified_blocked_count"),
        "pages_classified_js_shell_count": result.get("pages_classified_js_shell_count"),
        "pages_classified_parseable_directory_count": result.get("pages_classified_parseable_directory_count"),
        "outcome_counts": result.get("outcome_counts"),
        "blocked_urls": result.get("blocked_urls"),
        "js_shell_urls": result.get("js_shell_urls"),
        "matched_directory_urls": result.get("matched_directory_urls"),
        "contacts_total": result.get("contacts_total"),
        "attempted_rows": result.get("attempted_rows"),
    }
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
