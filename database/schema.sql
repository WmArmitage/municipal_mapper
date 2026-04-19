CREATE TABLE IF NOT EXISTS municipalities (
    municipality_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    county TEXT,
    website_url TEXT,
    domain TEXT
);

CREATE TABLE IF NOT EXISTS pages (
    page_id TEXT PRIMARY KEY,
    municipality_id TEXT,
    url TEXT,
    page_type TEXT,
    title TEXT,
    discovered_from TEXT,
    FOREIGN KEY (municipality_id) REFERENCES municipalities(municipality_id)
);

CREATE TABLE IF NOT EXISTS contacts (
    contact_id TEXT PRIMARY KEY,
    municipality_id TEXT,
    name TEXT,
    title TEXT,
    department TEXT,
    email TEXT,
    email_type TEXT,
    phone TEXT,
    source_url TEXT,
    confidence REAL,
    FOREIGN KEY (municipality_id) REFERENCES municipalities(municipality_id)
);

CREATE TABLE IF NOT EXISTS service_links (
    service_id TEXT PRIMARY KEY,
    municipality_id TEXT,
    category TEXT,
    label TEXT,
    url TEXT,
    domain TEXT,
    vendor TEXT,
    confidence REAL,
    source_url TEXT,
    FOREIGN KEY (municipality_id) REFERENCES municipalities(municipality_id)
);

CREATE TABLE IF NOT EXISTS locations (
    location_id TEXT PRIMARY KEY,
    municipality_id TEXT,
    address TEXT,
    hours TEXT,
    source_url TEXT,
    FOREIGN KEY (municipality_id) REFERENCES municipalities(municipality_id)
);

CREATE TABLE IF NOT EXISTS signals (
    signal_id TEXT PRIMARY KEY,
    municipality_id TEXT,
    signal_type TEXT,
    value TEXT,
    confidence REAL,
    source_url TEXT,
    FOREIGN KEY (municipality_id) REFERENCES municipalities(municipality_id)
);

CREATE INDEX IF NOT EXISTS idx_pages_municipality ON pages (municipality_id);
CREATE INDEX IF NOT EXISTS idx_contacts_municipality ON contacts (municipality_id);
CREATE INDEX IF NOT EXISTS idx_service_links_municipality ON service_links (municipality_id);
CREATE INDEX IF NOT EXISTS idx_locations_municipality ON locations (municipality_id);
CREATE INDEX IF NOT EXISTS idx_signals_municipality ON signals (municipality_id);
