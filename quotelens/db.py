import json
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    stored_path TEXT,
    supplier TEXT,
    rfq_ref TEXT,
    quote_no TEXT,
    quote_date TEXT,
    kind TEXT NOT NULL CHECK (kind IN ('pdf_text','pdf_scan','image')),
    status TEXT NOT NULL CHECK (status IN ('parsed','needs_review','failed','approved')),
    message TEXT NOT NULL DEFAULT '',
    stated_total REAL,
    raw_text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_rfq ON documents (rfq_ref);
CREATE TABLE IF NOT EXISTS line_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    line_no INTEGER NOT NULL,
    raw_desc TEXT NOT NULL,
    qty REAL NOT NULL,
    unit TEXT NOT NULL,
    rate REAL NOT NULL,
    amount REAL NOT NULL,
    catalog_id TEXT,
    match_score REAL NOT NULL DEFAULT 0,
    rate_norm REAL,
    flags TEXT NOT NULL DEFAULT '[]',
    overridden INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_items_doc ON line_items (document_id);
CREATE INDEX IF NOT EXISTS idx_items_catalog ON line_items (catalog_id);
CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    catalog_id TEXT NOT NULL,
    rate REAL NOT NULL CHECK (rate > 0),
    supplier TEXT,
    observed_on TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('synthetic','approved_quote','manual'))
);
CREATE INDEX IF NOT EXISTS idx_history_catalog ON price_history (catalog_id);
"""


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def history_rates(conn, catalog_id):
    return [r[0] for r in conn.execute("SELECT rate FROM price_history WHERE catalog_id=?", (catalog_id,))]


def flags_of(row):
    return json.loads(row["flags"])
