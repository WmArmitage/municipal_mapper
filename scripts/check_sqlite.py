import sqlite3

conn = sqlite3.connect("database/revize_trace.sqlite")
cur = conn.cursor()

print("\n=== TABLES + VIEWS ===")
for row in cur.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY type,name"):
    print(row[0])

print("\n=== CHECK TARGET VIEWS ===")
for name in ["vw_contacts_clean", "vw_best_role_per_town"]:
    exists = cur.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name=?",
        (name,)
    ).fetchone()[0]
    print(f"{name}: {'EXISTS' if exists else 'MISSING'}")

print("\n=== RAW TABLE COUNTS ===")
for table in ["contacts", "contact_candidates"]:
    try:
        count = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"{table}: {count}")
    except Exception as e:
        print(f"{table}: ERROR -> {e}")

conn.close()