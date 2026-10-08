"""SQLite storage: company invoice records, the last audit's results, and a log of every check."""
import os
import sqlite3

import pandas as pd

from engine import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS vendors (
    vendor_id TEXT PRIMARY KEY,
    vendor_name TEXT NOT NULL,
    vendor_name_normalized TEXT,
    erp_source TEXT
);

-- The company's payment history. Imported once, kept across runs.
CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id TEXT NOT NULL,
    vendor_id TEXT NOT NULL,
    vendor_name TEXT NOT NULL,
    invoice_date TEXT NOT NULL,
    due_date TEXT,
    amount REAL NOT NULL,
    currency TEXT DEFAULT 'USD',
    department TEXT,
    po_number TEXT,
    payment_status TEXT DEFAULT 'pending',
    erp_source TEXT,
    is_anomaly INTEGER,          -- optional label (1 = known bad); enables accuracy figures in the audit
    anomaly_type TEXT,
    source TEXT,                 -- file or screen the record came from
    imported_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (vendor_id) REFERENCES vendors(vendor_id)
);

-- Rebuilt by every audit.
CREATE TABLE IF NOT EXISTS detection_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_row_id INTEGER NOT NULL,
    rule_score REAL DEFAULT 0.0,
    ml_score REAL DEFAULT 0.0,
    final_score REAL DEFAULT 0.0,
    risk_category TEXT,
    flags TEXT,
    detected_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (invoice_row_id) REFERENCES invoices(id)
);

-- Every invoice checked in the app, with its verdict.
CREATE TABLE IF NOT EXISTS receipt_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    checked_at TEXT DEFAULT CURRENT_TIMESTAMP,
    source TEXT,
    file_name TEXT,
    invoice_id TEXT,
    vendor_name TEXT,
    invoice_date TEXT,
    amount REAL,
    currency TEXT,
    final_score REAL,
    risk_category TEXT,
    flags TEXT,
    tamper_score REAL,
    verdict TEXT
);

CREATE INDEX IF NOT EXISTS idx_invoices_vendor_id ON invoices(vendor_id);
CREATE INDEX IF NOT EXISTS idx_invoices_vendor_name ON invoices(vendor_name);
CREATE INDEX IF NOT EXISTS idx_invoices_invoice_id ON invoices(invoice_id);
CREATE INDEX IF NOT EXISTS idx_detection_results_invoice ON detection_results(invoice_row_id);
"""


def connect(path: str | None = None) -> sqlite3.Connection:
    """Open (and create if needed) the records database."""
    path = path or config.DB_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA cache_size=-65536")  # 64 MB: the 2 MB default thrashes on 100K-row imports
    conn.executescript(SCHEMA)
    return conn


def query(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> pd.DataFrame:
    return pd.read_sql_query(sql, conn, params=params)


def count_invoices(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]


def append(conn: sqlite3.Connection, table: str, df: pd.DataFrame):
    with conn:
        df.to_sql(table, conn, if_exists="append", index=False, chunksize=500)


def add_vendors(conn: sqlite3.Connection, vendors: pd.DataFrame):
    """Insert vendors, skipping vendor_ids that already exist."""
    cols = list(vendors.columns)
    with conn:
        conn.executemany(f"INSERT OR IGNORE INTO vendors ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                         vendors.itertuples(index=False, name=None))


def replace_results(conn: sqlite3.Connection, results: pd.DataFrame):
    with conn:
        conn.execute("DELETE FROM detection_results")
        results.to_sql("detection_results", conn, if_exists="append", index=False, chunksize=500)
