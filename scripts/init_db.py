from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db import apply_schema, get_connection, load_municipalities_from_csv


def ensure_directories() -> None:
    for rel_path in ("data/raw", "data/processed", "data/exports", "database"):
        (ROOT / rel_path).mkdir(parents=True, exist_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize or refresh SQLite schema and municipalities seed data.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete and recreate database/master.sqlite before loading seed data.",
    )
    parser.add_argument(
        "--db",
        default=str(ROOT / "database" / "master.sqlite"),
        help="Target SQLite DB path (default: database/master.sqlite).",
    )
    parser.add_argument(
        "--seed-csv",
        default=str(ROOT / "config" / "municipalities_seed.csv"),
        help="Municipality seed CSV path.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ensure_directories()
    db_path = Path(args.db)
    schema_path = ROOT / "database" / "schema.sql"
    seed_path = Path(args.seed_csv)

    if args.reset and db_path.exists():
        db_path.unlink()

    conn = get_connection(db_path)
    try:
        apply_schema(conn, schema_path)
        refreshed = load_municipalities_from_csv(conn, seed_path)
    finally:
        conn.close()

    action = "Reset and initialized" if args.reset else "Initialized/refreshed"
    print(f"{action} SQLite DB at: {db_path}")
    print(f"Municipality rows loaded/upserted from seed: {refreshed}")


if __name__ == "__main__":
    main()
