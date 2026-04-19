from __future__ import annotations

import csv
import sqlite3
from pathlib import Path


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    _run_lightweight_migrations(conn)
    return conn


def apply_schema(conn: sqlite3.Connection, schema_path: str | Path) -> None:
    sql = Path(schema_path).read_text(encoding="utf-8")
    conn.executescript(sql)
    _run_lightweight_migrations(conn)
    conn.commit()


def upsert_municipality(conn: sqlite3.Connection, row: dict[str, str | None]) -> None:
    payload = {
        "municipality_id": (row.get("municipality_id") or "").strip(),
        "name": (row.get("name") or "").strip(),
        "county": _clean_optional(row.get("county")),
        "website_url": _clean_optional(row.get("website_url")),
        "domain": _clean_optional(row.get("domain"), to_lower=True),
        "jobs_url": _clean_optional(row.get("jobs_url")),
        "directory_url": _clean_optional(row.get("directory_url")),
        "assessor_url": _clean_optional(row.get("assessor_url")),
        "tax_url": _clean_optional(row.get("tax_url")),
    }
    conn.execute(
        """
        INSERT INTO municipalities (
            municipality_id, name, county, website_url, domain, jobs_url, directory_url, assessor_url, tax_url
        )
        VALUES (
            :municipality_id, :name, :county, :website_url, :domain, :jobs_url, :directory_url, :assessor_url, :tax_url
        )
        ON CONFLICT(municipality_id) DO UPDATE SET
            name = excluded.name,
            county = excluded.county,
            website_url = excluded.website_url,
            domain = excluded.domain,
            jobs_url = excluded.jobs_url,
            directory_url = excluded.directory_url,
            assessor_url = excluded.assessor_url,
            tax_url = excluded.tax_url
        """,
        payload,
    )


def load_municipalities_from_csv(conn: sqlite3.Connection, csv_path: str | Path) -> int:
    count = 0
    with Path(csv_path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            payload = {
                "municipality_id": (row.get("municipality_id") or "").strip(),
                "name": (row.get("name") or "").strip(),
                "county": (row.get("county") or "").strip() or None,
                "website_url": (row.get("website_url") or "").strip() or None,
                "domain": (row.get("domain") or "").strip().lower() or None,
                "jobs_url": (row.get("jobs_url") or "").strip() or None,
                "directory_url": (row.get("directory_url") or "").strip() or None,
                "assessor_url": (row.get("assessor_url") or "").strip() or None,
                "tax_url": (row.get("tax_url") or "").strip() or None,
            }
            if not payload["municipality_id"] or not payload["name"]:
                continue
            upsert_municipality(conn, payload)
            count += 1
    conn.commit()
    return count


def get_municipality(conn: sqlite3.Connection, municipality_id: str) -> dict | None:
    row = conn.execute(
        """
        SELECT municipality_id, name, county, website_url, domain, jobs_url, directory_url, assessor_url, tax_url
        FROM municipalities
        WHERE municipality_id = ?
        """,
        (municipality_id,),
    ).fetchone()
    return dict(row) if row else None


def list_municipalities(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT municipality_id, name, county, website_url, domain, jobs_url, directory_url, assessor_url, tax_url
        FROM municipalities
        ORDER BY municipality_id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def municipality_has_pages(conn: sqlite3.Connection, municipality_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM pages WHERE municipality_id = ? LIMIT 1",
        (municipality_id,),
    ).fetchone()
    return row is not None


def upsert_page(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        """
        INSERT INTO pages (page_id, municipality_id, url, page_type, title, discovered_from)
        VALUES (:page_id, :municipality_id, :url, :page_type, :title, :discovered_from)
        ON CONFLICT(page_id) DO UPDATE SET
            page_type = excluded.page_type,
            title = COALESCE(excluded.title, pages.title),
            discovered_from = COALESCE(excluded.discovered_from, pages.discovered_from)
        """,
        row,
    )


def upsert_contact(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        """
        INSERT INTO contacts (
            contact_id, municipality_id, name, title, department, email, email_type, phone, phone_ext, source_context, source_url, confidence
        )
        VALUES (
            :contact_id, :municipality_id, :name, :title, :department, :email, :email_type, :phone, :phone_ext, :source_context, :source_url, :confidence
        )
        ON CONFLICT(contact_id) DO UPDATE SET
            name = COALESCE(excluded.name, contacts.name),
            title = COALESCE(excluded.title, contacts.title),
            department = COALESCE(excluded.department, contacts.department),
            email_type = COALESCE(excluded.email_type, contacts.email_type),
            phone = COALESCE(excluded.phone, contacts.phone),
            phone_ext = COALESCE(excluded.phone_ext, contacts.phone_ext),
            source_context = CASE
                WHEN excluded.phone IS NOT NULL
                    AND (contacts.phone IS NULL OR excluded.confidence >= contacts.confidence)
                THEN COALESCE(excluded.source_context, contacts.source_context)
                ELSE contacts.source_context
            END,
            source_url = COALESCE(excluded.source_url, contacts.source_url),
            confidence = MAX(contacts.confidence, excluded.confidence)
        """,
        row,
    )


def upsert_service_link(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        """
        INSERT INTO service_links (
            service_id, municipality_id, category, label, url, domain, vendor, service_page_type, confidence, source_url
        )
        VALUES (
            :service_id, :municipality_id, :category, :label, :url, :domain, :vendor, :service_page_type, :confidence, :source_url
        )
        ON CONFLICT(service_id) DO UPDATE SET
            label = COALESCE(excluded.label, service_links.label),
            vendor = COALESCE(excluded.vendor, service_links.vendor),
            service_page_type = COALESCE(excluded.service_page_type, service_links.service_page_type),
            confidence = MAX(service_links.confidence, excluded.confidence),
            source_url = COALESCE(excluded.source_url, service_links.source_url)
        """,
        row,
    )


def upsert_location(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        """
        INSERT INTO locations (location_id, municipality_id, address, hours, source_url)
        VALUES (:location_id, :municipality_id, :address, :hours, :source_url)
        ON CONFLICT(location_id) DO UPDATE SET
            address = COALESCE(excluded.address, locations.address),
            hours = COALESCE(excluded.hours, locations.hours)
        """,
        row,
    )


def upsert_signal(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        """
        INSERT INTO signals (signal_id, municipality_id, signal_type, value, confidence, source_url)
        VALUES (:signal_id, :municipality_id, :signal_type, :value, :confidence, :source_url)
        ON CONFLICT(signal_id) DO UPDATE SET
            value = excluded.value,
            source_url = CASE
                WHEN excluded.confidence >= signals.confidence AND excluded.source_url IS NOT NULL
                THEN excluded.source_url
                ELSE signals.source_url
            END,
            confidence = MAX(signals.confidence, excluded.confidence)
        """,
        row,
    )


def commit(conn: sqlite3.Connection) -> None:
    conn.commit()


def get_municipality_table_counts(conn: sqlite3.Connection, municipality_id: str) -> dict[str, int]:
    tables = ("pages", "contacts", "service_links", "locations", "signals")
    counts: dict[str, int] = {}
    for table in tables:
        count = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE municipality_id = ?",
            (municipality_id,),
        ).fetchone()[0]
        counts[table] = int(count)
    return counts


def fetch_municipality_rows(
    conn: sqlite3.Connection,
    municipality_id: str,
    table_name: str,
    limit: int | None = None,
) -> list[dict]:
    query = f"SELECT * FROM {table_name} WHERE municipality_id = ?"
    params: list[object] = [municipality_id]
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(query, tuple(params)).fetchall()
    return [dict(row) for row in rows]


def _run_lightweight_migrations(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "contacts"):
        _ensure_column(conn, "contacts", "phone_ext", "TEXT")
        _ensure_column(conn, "contacts", "source_context", "TEXT")
    if _table_exists(conn, "municipalities"):
        _ensure_column(conn, "municipalities", "jobs_url", "TEXT")
        _ensure_column(conn, "municipalities", "directory_url", "TEXT")
        _ensure_column(conn, "municipalities", "assessor_url", "TEXT")
        _ensure_column(conn, "municipalities", "tax_url", "TEXT")
    if _table_exists(conn, "service_links"):
        _ensure_column(conn, "service_links", "service_page_type", "TEXT")
    conn.commit()


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, column_type: str) -> None:
    existing = {
        str(row["name"]).lower()
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name.lower() in existing:
        return
    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")


def _clean_optional(value: str | None, to_lower: bool = False) -> str | None:
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    if to_lower:
        return cleaned.lower()
    return cleaned
