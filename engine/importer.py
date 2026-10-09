"""
Load a company's invoice/payment history from CSV or Excel (any ERP export).

Column names are matched loosely ("Invoice No", "invoice_number", "Supplier", ...).
Required: invoice number, vendor name, invoice date, amount. Everything else is optional.
See templates/company_records_template.csv.
"""
import re
import sqlite3
from functools import cache

import pandas as pd
from rapidfuzz import fuzz, process

from engine import db
from engine.scoring import normalize_vendor_name

REQUIRED = ["invoice_id", "vendor_name", "invoice_date", "amount"]
ALIASES = {
    "invoice_id": ["invoice id", "invoice no", "invoice number", "invoice", "inv no", "document number",
                   "doc no", "receipt no", "receipt number", "bill no", "bill number", "reference", "ref"],
    "vendor_name": ["vendor name", "vendor", "supplier", "supplier name", "payee", "company", "merchant", "seller"],
    "vendor_id": ["vendor id", "vendor code", "supplier id", "supplier code", "vendor number"],
    "invoice_date": ["invoice date", "date", "document date", "posting date", "bill date", "receipt date"],
    "amount": ["amount", "total", "invoice amount", "gross amount", "total amount", "amount paid", "value"],
    "currency": ["currency", "curr", "ccy"],
    "po_number": ["po number", "po", "purchase order", "po no"],
    "due_date": ["due date", "payment due"],
    "department": ["department", "dept", "cost center", "cost centre"],
    "payment_status": ["payment status", "status"],
    "erp_source": ["erp source", "erp", "source system"],
    "is_anomaly": ["is anomaly", "is fraud", "fraud", "label"],
    "anomaly_type": ["anomaly type", "fraud type"],
}
INVOICE_COLUMNS = ["invoice_id", "vendor_id", "vendor_name", "invoice_date", "due_date", "amount", "currency",
                   "department", "po_number", "payment_status", "erp_source", "is_anomaly", "anomaly_type", "source"]
VENDOR_MATCH_CUTOFF = 90  # name similarity (0-100) needed to reuse an existing vendor_id
MIN_CONTAINED_NAME = 15   # one name containing the other only counts for names this long ("acme" in "acme bakery" doesn't)


def read_table(file) -> pd.DataFrame:
    """CSV or Excel from a path or a file-like object with a .name (e.g. a Streamlit upload)."""
    name = getattr(file, "name", str(file)).lower()
    if name.endswith((".xlsx", ".xlsm", ".xls")):
        return pd.read_excel(file)
    return pd.read_csv(file, low_memory=False)


def detect_dayfirst(dates: pd.Series) -> tuple[bool, str | None]:
    """Date order of text dates like 03/01/2024: a first part above 12 means day-first, a second part above 12
    month-first. Returns (dayfirst, a warning when the file doesn't say or says both)."""
    parts = dates.astype(str).str.extract(r"^\s*(\d{1,2})[/.\-](\d{1,2})[/.\-]\d{2,4}\b").dropna().astype(int)
    day, month = bool((parts[0] > 12).any()), bool((parts[1] > 12).any())
    if day and month:
        return True, ("Dates are written in both orders (some like 13/01, some like 01/13). They were read as day-first; "
                      "rows that don't fit were skipped. Check the file.")
    if day or month or parts.empty:
        return day, None
    return False, ("Every date like 03/01/2024 could be day- or month-first; they were read as month-first "
                   "(March 1). If they are day-first, choose day-first and load the file again.")


def normalize(raw: pd.DataFrame, dayfirst: bool | None = None) -> tuple[pd.DataFrame, list[str]]:
    """Map columns to the invoice schema and coerce types. Returns (valid rows, problems found).
    dayfirst: date order of text dates like 03/01/2024; None detects it from the file."""
    lookup = {alias: field for field, names in ALIASES.items() for alias in [field.replace("_", " "), *names]}
    rename = {}
    for col in raw.columns:
        field = lookup.get(re.sub(r"[\s_\-.#:]+", " ", str(col).strip().lower()).strip())
        if field and field not in rename.values():
            rename[col] = field
    df = raw.rename(columns=rename)[list(rename.values())].copy()

    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        return df.iloc[0:0], [f"Missing required column(s): {', '.join(missing)}. Found: {', '.join(map(str, raw.columns))}"]

    problems = []
    if dayfirst is None:
        dayfirst, warning = detect_dayfirst(df["invoice_date"])
        problems += [warning] if warning else []
    df["invoice_id"] = df["invoice_id"].astype(str).str.strip()
    df["vendor_name"] = df["vendor_name"].astype(str).str.strip()
    df["amount"] = pd.to_numeric(df["amount"].astype(str).str.replace(r"[^\d.\-]", "", regex=True), errors="coerce")
    for col in ["invoice_date", "due_date"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce", dayfirst=dayfirst, format="mixed").dt.date.astype(str)
            df.loc[df[col] == "NaT", col] = None

    bad = df["amount"].isna() | df["invoice_date"].isna() | df["invoice_id"].isin(["", "nan"]) | df["vendor_name"].isin(["", "nan"])
    if bad.any():
        problems.append(f"Skipped {int(bad.sum())} row(s) with a missing or unreadable invoice number, vendor, date or amount.")
    df = df[~bad].copy()

    df["currency"] = df["currency"].fillna("USD").astype(str).str.upper() if "currency" in df.columns else "USD"
    if "is_anomaly" in df.columns:
        df["is_anomaly"] = pd.to_numeric(df["is_anomaly"], errors="coerce")
    return df, problems


def resolve_vendor_ids(names: pd.Series, vendors: pd.DataFrame) -> pd.Series:
    """Map vendor names to known vendor_ids (exact or fuzzy on the normalized name); new names get a new id."""
    known = dict(zip(vendors["vendor_name"].map(normalize_vendor_name), vendors["vendor_id"])) if not vendors.empty else {}
    choices = list(known)
    long_names = [x for x in choices if len(x) >= MIN_CONTAINED_NAME]

    @cache
    def one(c: str) -> str:
        if c in known:
            return known[c]
        if choices:
            hit = process.extractOne(c, choices, scorer=fuzz.ratio, score_cutoff=VENDOR_MATCH_CUTOFF)
            if not hit and len(c) >= MIN_CONTAINED_NAME:  # truncated/extended: "home master hardware &" vs "... & tailoring"
                hit = process.extractOne(c, long_names, scorer=fuzz.partial_ratio, score_cutoff=95)
            if hit:
                return known[hit[0]]
        return "V-" + (re.sub(r"[^a-z0-9]+", "-", c).strip("-")[:40].upper() or "UNKNOWN")

    return names.map(normalize_vendor_name).map(one)


def import_records(conn: sqlite3.Connection, df: pd.DataFrame, source: str = "import") -> int:
    """Insert normalized rows (see normalize) and any new vendors. Returns rows inserted.

    Rows already in the records (same vendor, number, date and amount) are skipped, so importing a file twice
    doesn't turn every row into a duplicate. Repeats within one file are kept: they may be real double payments."""
    if df.empty:
        return 0
    df = df.copy()
    if "vendor_id" not in df.columns or df["vendor_id"].isna().any():
        resolved = resolve_vendor_ids(df["vendor_name"], db.query(conn, "SELECT vendor_id, vendor_name FROM vendors"))
        df["vendor_id"] = df["vendor_id"].fillna(resolved) if "vendor_id" in df.columns else resolved
    df["vendor_id"] = df["vendor_id"].astype(str)
    df["source"] = source

    key = ["vendor_id", "invoice_id", "invoice_date", "amount"]
    as_text = lambda d: d[key].astype({"amount": float}).astype(str)  # same types on both sides, even when empty
    have = as_text(db.query(conn, f"SELECT DISTINCT {', '.join(key)} FROM invoices"))
    df = df[as_text(df).merge(have, on=key, how="left", indicator=True)["_merge"].eq("left_only").to_numpy()]
    if df.empty:
        return 0

    first = df.drop_duplicates("vendor_id")
    vendors = first[["vendor_id", "vendor_name"]].assign(
        vendor_name_normalized=first["vendor_name"].map(normalize_vendor_name),
        erp_source=first["erp_source"] if "erp_source" in df.columns else None)
    db.add_vendors(conn, vendors)
    # Only columns the file has, so the schema defaults (currency USD, payment_status pending) apply to the rest
    db.append(conn, "invoices", df[[c for c in INVOICE_COLUMNS if c in df.columns and df[c].notna().any()]])
    return len(df)


def merge_split_vendors(conn: sqlite3.Connection) -> int:
    """One vendor under several generated IDs ("V-..."), created before name normalization improved
    ('AEON CO. (M) BHD (126926-H)' and 'AEON CO. (M) BHD'), splits its history and hides duplicates between
    them. IDs whose names now normalize the same are merged into the one with the most invoices. IDs from
    the company's own system are left alone. Returns the number of IDs merged away."""
    v = db.query(conn, """SELECT v.vendor_id, v.vendor_name, COUNT(i.id) AS n FROM vendors v
                          LEFT JOIN invoices i ON i.vendor_id = v.vendor_id
                          WHERE v.vendor_id LIKE 'V-%' GROUP BY v.vendor_id""")
    v = v.assign(key=v["vendor_name"].map(normalize_vendor_name)).sort_values(["n", "vendor_id"], ascending=[False, True])
    v["keep"] = v.groupby("key")["vendor_id"].transform("first")
    moves = v[v["vendor_id"] != v["keep"]]
    with conn:
        conn.executemany("UPDATE invoices SET vendor_id = ? WHERE vendor_id = ?", zip(moves["keep"], moves["vendor_id"]))
        conn.executemany("DELETE FROM vendors WHERE vendor_id = ?", zip(moves["vendor_id"]))
    return len(moves)
