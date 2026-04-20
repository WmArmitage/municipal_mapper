import sqlite3
from pathlib import Path

# Script lives in /database/, so use local paths
BASE_DIR = Path(__file__).resolve().parent

DB_PATH = BASE_DIR / "master.sqlite"
MIGRATION_PATH = BASE_DIR / "migrations" / "20260419_contact_service_enrichment.sql"

def run_migration():
    print(f"Using DB: {DB_PATH}")
    print(f"Using migration: {MIGRATION_PATH}")

    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found at {DB_PATH}")

    if not MIGRATION_PATH.exists():
        raise FileNotFoundError(f"Migration file not found at {MIGRATION_PATH}")

    with sqlite3.connect(DB_PATH) as conn:
        sql = MIGRATION_PATH.read_text(encoding="utf-8")

        # Skip entity_type since you already added it
        sql = sql.replace(
            "ALTER TABLE contacts ADD COLUMN entity_type TEXT;",
            "-- skipped (already exists)"
        )

        conn.executescript(sql)

    print("Migration executed successfully.")

if __name__ == "__main__":
    run_migration()