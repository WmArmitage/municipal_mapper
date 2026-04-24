from __future__ import annotations

import argparse
import sqlite3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Quick SQLite object checker.")
    parser.add_argument("--db", default="database/revize_trace.sqlite", help="SQLite DB path.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    conn = sqlite3.connect(args.db)
    cur = conn.cursor()
    try:
        print("\n=== TABLES + VIEWS ===")
        for row in cur.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY type,name"):
            print(row[0])

        print("\n=== CHECK TARGET VIEWS ===")
        for name in [
            "vw_contacts_clean",
            "vw_role_candidates_scored",
            "vw_unresolved_roles",
            "vw_best_role_per_town",
        ]:
            exists = cur.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE name=?",
                (name,),
            ).fetchone()[0]
            print(f"{name}: {'EXISTS' if exists else 'MISSING'}")

        print("\n=== RAW TABLE COUNTS ===")
        contacts_count = cur.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
        print(f"contacts: {contacts_count}")

        contact_candidates_exists = cur.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='contact_candidates'"
        ).fetchone()[0]
        print(f"contact_candidates: {'EXISTS' if contact_candidates_exists else 'MISSING (optional/legacy)'}")
        print("note: SQLite pipeline stages directly into contacts; contact_candidates is not required.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
