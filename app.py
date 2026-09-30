"""
Receipt authenticity checker (Streamlit UI).

    streamlit run app.py
"""
import os
from datetime import date

import pandas as pd
import streamlit as st

import config
from database.db_manager import DatabaseManager
from detection.checker import add_to_records, check_invoices, verdict
from detection.ml_detector import MODEL_PATH, MLDetector
from forgery import model as tamper
from intake import ocr
from intake.fields import parse_fields
from intake.importer import import_records, normalize, read_table
from pipeline import run_audit

st.set_page_config(page_title="Receipt Checker", page_icon="🧾", layout="wide")
CURRENCIES = ["USD", "EUR", "GBP", "INR", "MYR", "Other"]
IMAGE_TYPES = ["png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff"]
VERDICT_STYLE = {"SUSPICIOUS": st.error, "REVIEW": st.warning, "OK": st.success}


@st.cache_resource
def _init_schema() -> bool:  # once per server process
    with DatabaseManager(config.DB_PATH) as db:
        db.init_db()
    return True


def get_db() -> DatabaseManager:
    """One SQLite connection per browser session: a sqlite3 connection must not be used by two
    sessions' script threads at the same time."""
    _init_schema()
    if "db" not in st.session_state:
        st.session_state.db = DatabaseManager(config.DB_PATH)
    return st.session_state.db


@st.cache_resource
def get_anomaly_model(_mtime: float):  # keyed on file time so retraining reloads it
    return MLDetector.load()


def anomaly_model():
    return get_anomaly_model(os.path.getmtime(MODEL_PATH)) if os.path.exists(MODEL_PATH) else None


def show_results(res: pd.DataFrame, related: pd.DataFrame, tamper_result=None):
    r = res.iloc[0]
    v = verdict(r.risk_category, bool(tamper_result and tamper_result[1]))
    VERDICT_STYLE[v](f"**{v}**: record risk {r.risk_category} (score {r.final_score:.2f})")
    c1, c2, c3 = st.columns(3)
    c1.metric("Record risk score", f"{r.final_score:.2f}", help="0-1 blend of rules (60%) and anomaly model (40%)")
    c2.metric("Rules / anomaly model", f"{r.rule_score:.2f} / {r.ml_score:.2f}")
    if tamper_result is None:
        c3.metric("Image tamper probability", "n/a", help="Only for uploaded image files (PDF pages are re-rendered, which erases the traces it reads)")
    else:
        c3.metric("Image tamper probability", f"{tamper_result[0]:.0%}", "flagged" if tamper_result[1] else "not flagged",
                  delta_color="inverse" if tamper_result[1] else "off")
    st.markdown("**Why**")
    for reason in r.reasons.split("; "):
        st.markdown(f"- {reason}")
    if tamper_result and tamper_result[1]:
        st.markdown("- Image statistics suggest the scan may have been edited: compare with the original document")
    if not related.empty:
        st.markdown("**Matching records**")
        st.dataframe(related.drop(columns=["new_id"]), hide_index=True)
    return v


# ── Sidebar: status + training ─────────────────────────────────────────
db = get_db()
with st.sidebar:
    st.header("🧾 Receipt Checker")
    n_records = db.count_invoices()
    st.metric("Company records", f"{n_records:,}")
    if os.path.exists(MODEL_PATH):
        st.caption(f"Anomaly model trained {pd.Timestamp(os.path.getmtime(MODEL_PATH), unit='s'):%Y-%m-%d %H:%M} (UTC)")
    else:
        st.caption("Anomaly model not trained yet: checks use rules only.")
    st.caption("Image tamper model: " + ("loaded" if tamper.available() else "missing (run python -m forgery.train)"))
    if st.button("Audit records & train model", disabled=n_records == 0, width="stretch"):
        with st.spinner("Auditing all records and training the anomaly model..."):
            summary = run_audit(verbose=False)
        st.success(f"Audited {summary['records']:,} records: " +
                   ", ".join(f"{k} {v:,}" for k, v in summary.items() if k in ("HIGH", "MEDIUM", "LOW")))

tab_check, tab_batch, tab_import, tab_records = st.tabs(
    ["🔍 Check receipt", "📑 Batch check (Excel/CSV)", "📥 Import records", "📊 Records"])

# ── Check one receipt ──────────────────────────────────────────────────
with tab_check:
    mode = st.radio("Receipt input", ["Photo / scan", "PDF", "Manual entry"], horizontal=True)
    fields, image, is_scan, file_key = {}, None, False, "manual"
    if mode != "Manual entry":
        up = st.file_uploader("Upload receipt", type=IMAGE_TYPES if mode == "Photo / scan" else ["pdf"])
        if up:
            file_key = f"{up.name}-{up.size}"
            if st.session_state.get("ocr_key") != file_key:
                with st.spinner("Reading receipt..."):
                    text, img, scan = ocr.extract(up.name, up.getvalue())
                st.session_state.update(ocr_key=file_key, ocr=(text, img, scan), fields=parse_fields(text))
            text, image, is_scan = st.session_state.ocr
            fields = st.session_state.fields
            left, right = st.columns([1, 2])
            if image is not None:
                left.image(image, width="stretch")
            right.caption("Fields read from the receipt: check and correct them before running the check.")
            with right.expander("Raw extracted text"):
                st.text(text or "(no text found)")

    with st.form(f"fields-{file_key}"):
        c1, c2 = st.columns(2)
        vendor = c1.text_input("Vendor", fields.get("vendor_name") or "")
        invoice_id = c2.text_input("Invoice / receipt number", fields.get("invoice_id") or "")
        parsed_date = pd.to_datetime(fields.get("invoice_date"), errors="coerce")
        inv_date = c1.date_input("Invoice date", parsed_date.date() if pd.notna(parsed_date) else date.today())
        amount = c2.number_input("Total amount", min_value=0.0, value=float(fields.get("amount") or 0.0), step=0.01, format="%.2f")
        cur = fields.get("currency") or "USD"
        currency = c1.selectbox("Currency", CURRENCIES, index=CURRENCIES.index(cur) if cur in CURRENCIES else 0)
        submitted = st.form_submit_button("Check authenticity", type="primary")

    if submitted:
        if not vendor.strip() or not invoice_id.strip() or amount <= 0:
            st.error("Vendor, invoice number and a positive amount are required.")
        else:
            new = pd.DataFrame([dict(invoice_id=invoice_id.strip(), vendor_name=vendor.strip(),
                                     invoice_date=inv_date.isoformat(), amount=amount, currency=currency)])
            with st.spinner("Checking against company records..."):
                res, related = check_invoices(new, db, anomaly_model())
                tamper_result = tamper.tamper_score(image) if (image is not None and is_scan) else None
            st.session_state.last_check = (res, related, tamper_result)
            r = res.iloc[0]
            db.log_receipt_check(dict(
                source=mode, file_name=st.session_state.get("ocr_key") if mode != "Manual entry" else None,
                invoice_id=r.invoice_id, vendor_name=r.vendor_name, invoice_date=r.invoice_date, amount=r.amount,
                currency=r.currency, final_score=r.final_score, risk_category=r.risk_category, flags=r.all_flags,
                tamper_score=tamper_result[0] if tamper_result else None,
                verdict=verdict(r.risk_category, bool(tamper_result and tamper_result[1]))))

    if "last_check" in st.session_state:
        res, related, tamper_result = st.session_state.last_check
        st.divider()
        show_results(res, related, tamper_result)
        if st.button("Add this invoice to company records",
                     help="Do this once the invoice is approved/paid, so future copies of it are caught"):
            add_to_records(res, db)
            del st.session_state["last_check"]
            st.success("Saved to records.")

# ── Batch check from Excel / CSV ───────────────────────────────────────
with tab_batch:
    st.caption("Columns: invoice number, vendor, date, amount (names matched loosely; currency, vendor id, PO optional).")
    up = st.file_uploader("Invoices to check", type=["xlsx", "xls", "csv"], key="batch_file")
    dayfirst = st.checkbox("Dates are day-first (dd/mm/yyyy)", key="batch_dayfirst")
    if up:
        rows, problems = normalize(read_table(up), dayfirst=dayfirst)
        for p in problems:
            st.warning(p)
        if not rows.empty and st.button(f"Check {len(rows):,} invoices", type="primary"):
            with st.spinner("Checking..."):
                res, related = check_invoices(rows, db, anomaly_model())
            st.session_state.batch = (res, related)
            for r in res.itertuples():
                db.log_receipt_check(dict(source="excel", file_name=up.name, invoice_id=r.invoice_id, vendor_name=r.vendor_name,
                                          invoice_date=str(r.invoice_date), amount=r.amount, currency=r.currency,
                                          final_score=r.final_score, risk_category=r.risk_category, flags=r.all_flags,
                                          verdict=r.verdict))
    if "batch" in st.session_state:
        res, related = st.session_state.batch
        counts = res["verdict"].value_counts()
        c1, c2, c3 = st.columns(3)
        c1.metric("Suspicious", int(counts.get("SUSPICIOUS", 0)))
        c2.metric("Review", int(counts.get("REVIEW", 0)))
        c3.metric("OK", int(counts.get("OK", 0)))
        view = res[["verdict", "invoice_id", "vendor_name", "invoice_date", "amount", "currency", "final_score", "reasons"]]
        st.dataframe(view.sort_values("final_score", ascending=False), hide_index=True)
        st.download_button("Download results (CSV)", view.to_csv(index=False), "check_results.csv", "text/csv")
        if st.button("Add all OK invoices to company records"):
            n = add_to_records(res[res["verdict"] == "OK"], db, source="batch-check")
            del st.session_state["batch"]
            st.success(f"Saved {n:,} invoices to records.")

# ── Import company history ─────────────────────────────────────────────
with tab_import:
    st.caption("Load past invoices/payments (ERP export) so new receipts can be checked against them. "
               "Required columns: invoice number, vendor, date, amount.")
    up = st.file_uploader("Company records", type=["xlsx", "xls", "csv"], key="import_file")
    dayfirst = st.checkbox("Dates are day-first (dd/mm/yyyy)", key="import_dayfirst")
    if up:
        raw = read_table(up)
        rows, problems = normalize(raw, dayfirst=dayfirst)
        for p in problems:
            (st.error if rows.empty else st.warning)(p)
        if not rows.empty:
            st.caption(f"Recognized columns: {', '.join(rows.columns)}")
            st.dataframe(rows.head(20), hide_index=True)
            if st.button(f"Import {len(rows):,} records", type="primary"):
                n = import_records(rows, db, source=up.name)
                st.success(f"Imported {n:,} records. Now click **Audit records & train model** in the sidebar.")

# ── Records & results ──────────────────────────────────────────────────
with tab_records:
    checks = db.execute_query("SELECT checked_at, source, invoice_id, vendor_name, amount, verdict, risk_category, "
                              "final_score, tamper_score FROM receipt_checks ORDER BY id DESC LIMIT 200")
    st.subheader("Recent checks")
    st.dataframe(checks, hide_index=True)

    st.subheader("Last audit: highest-risk records")
    flagged = db.execute_query("""
        SELECT i.id, i.invoice_id, i.vendor_name, i.invoice_date, i.amount, d.final_score, d.risk_category, d.flags
        FROM detection_results d JOIN invoices i ON i.id = d.invoice_row_id
        WHERE d.risk_category != 'LOW' ORDER BY d.final_score DESC LIMIT 500""")
    st.dataframe(flagged, hide_index=True)

    st.subheader("Company records")
    st.dataframe(db.execute_query("SELECT * FROM invoices ORDER BY id DESC LIMIT 1000"), hide_index=True)
