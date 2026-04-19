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
    return conn


def apply_schema(conn: sqlite3.Connection, schema_path: str | Path) -> None:
    sql = Path(schema_path).read_text(encoding="utf-8")
    conn.executescript(sql)
    conn.commit()


def upsert_municipality(conn: sqlite3.Connection, row: dict[str, str | None]) -> None:
    conn.execute(
        """
        INSERT INTO municipalities (municipality_id, name, county, website_url, domain)
        VALUES (:municipality_id, :name, :county, :website_url, :domain)
        ON CONFLICT(municipality_id) DO UPDATE SET
            name = excluded.name,
            county = excluded.county,
            website_url = excluded.website_url,
            domain = excluded.domain
        """,
        row,
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
            }
            if not payload["municipality_id"] or not payload["name"]:
                continue
            upsert_municipality(conn, payload)
            count += 1
    conn.commit()
    return count


def get_municipality(conn: sqlite3.Connection, municipality_id: str) -> dict | None:
    row = conn.execute(
        "SELECT municipality_id, name, county, website_url, domain FROM municipalities WHERE municipality_id = ?",
        (municipality_id,),
    ).fetchone()
    return dict(row) if row else None


def list_municipalities(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT municipality_id, name, county, website_url, domain FROM municipalities ORDER BY municipality_id"
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
            contact_id, municipality_id, name, title, department, email, email_type, phone, source_url, confidence
        )
        VALUES (
            :contact_id, :municipality_id, :name, :title, :department, :email, :email_type, :phone, :source_url, :confidence
        )
        ON CONFLICT(contact_id) DO UPDATE SET
            name = COALESCE(excluded.name, contacts.name),
            title = COALESCE(excluded.title, contacts.title),
            department = COALESCE(excluded.department, contacts.department),
            email_type = COALESCE(excluded.email_type, contacts.email_type),
            phone = COALESCE(excluded.phone, contacts.phone),
            confidence = MAX(contacts.confidence, excluded.confidence)
        """,
        row,
    )


def upsert_service_link(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        """
        INSERT INTO service_links (
            service_id, municipality_id, category, label, url, domain, vendor, confidence, source_url
        )
        VALUES (
            :service_id, :municipality_id, :category, :label, :url, :domain, :vendor, :confidence, :source_url
        )
        ON CONFLICT(service_id) DO UPDATE SET
            label = COALESCE(excluded.label, service_links.label),
            vendor = COALESCE(excluded.vendor, service_links.vendor),
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
            confidence = MAX(signals.confidence, excluded.confidence)
        """,
        row,
    )


def commit(conn: sqlite3.Connection) -> None:
    conn.commit()

