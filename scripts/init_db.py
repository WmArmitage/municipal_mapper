from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db import apply_schema, get_connection, load_municipalities_from_csv


def ensure_directories() -> None:
    for rel_path in ("data/raw", "data/processed", "data/exports", "database"):
        (ROOT / rel_path).mkdir(parents=True, exist_ok=True)


def main() -> None:
    ensure_directories()
    db_path = ROOT / "database" / "master.sqlite"
    schema_path = ROOT / "database" / "schema.sql"
    seed_path = ROOT / "config" / "municipalities_seed.csv"

    if db_path.exists():
        db_path.unlink()

    conn = get_connection(db_path)
    try:
        apply_schema(conn, schema_path)
        inserted = load_municipalities_from_csv(conn, seed_path)
    finally:
        conn.close()

    print(f"Initialized SQLite DB at: {db_path}")
    print(f"Loaded municipalities: {inserted}")


if __name__ == "__main__":
    main()
