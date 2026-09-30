CREATE TABLE IF NOT EXISTS vendors (
    vendor_id TEXT PRIMARY KEY,
    vendor_name TEXT NOT NULL,
    vendor_name_normalized TEXT,
    erp_source TEXT
);

CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id TEXT NOT NULL,
    vendor_id TEXT NOT NULL,
    vendor_name TEXT NOT NULL,
    invoice_date TEXT NOT NULL,
    due_date TEXT NOT NULL,
    amount REAL NOT NULL,
    currency TEXT DEFAULT 'USD',
    department TEXT,
    po_number TEXT,
    payment_status TEXT DEFAULT 'pending',
    erp_source TEXT,
    is_anomaly INTEGER DEFAULT 0,
    anomaly_type TEXT,
    FOREIGN KEY (vendor_id) REFERENCES vendors(vendor_id)
);

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

CREATE TABLE IF NOT EXISTS fuzzy_match_pairs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_a_id INTEGER NOT NULL,
    record_b_id INTEGER NOT NULL,
    similarity_score REAL NOT NULL,
    levenshtein_score REAL DEFAULT 0.0,
    jaro_winkler_score REAL DEFAULT 0.0,
    match_type TEXT,
    FOREIGN KEY (record_a_id) REFERENCES invoices(id),
    FOREIGN KEY (record_b_id) REFERENCES invoices(id)
);

CREATE INDEX IF NOT EXISTS idx_invoices_vendor_id ON invoices(vendor_id);
CREATE INDEX IF NOT EXISTS idx_invoices_invoice_date ON invoices(invoice_date);
CREATE INDEX IF NOT EXISTS idx_invoices_amount ON invoices(amount);
CREATE INDEX IF NOT EXISTS idx_invoices_invoice_id ON invoices(invoice_id);
