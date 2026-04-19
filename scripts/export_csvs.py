from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db import get_connection


def export_table(conn, table_name: str, out_path: Path) -> int:
    rows = conn.execute(f"SELECT * FROM {table_name}").fetchall()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        if not rows:
            handle.write("")
            return 0
        fieldnames = rows[0].keys()
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row))
    return len(rows)


def main() -> None:
    db_path = ROOT / "database" / "master.sqlite"
    exports = ROOT / "data" / "exports"
    conn = get_connection(db_path)
    try:
        counts = {
            "contacts": export_table(conn, "contacts", exports / "contacts.csv"),
            "service_links": export_table(conn, "service_links", exports / "service_links.csv"),
            "locations": export_table(conn, "locations", exports / "locations.csv"),
            "signals": export_table(conn, "signals", exports / "signals.csv"),
        }
    finally:
        conn.close()

    for name, count in counts.items():
        print(f"Exported {name}: {count} rows")


if __name__ == "__main__":
    main()

