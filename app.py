"""
Invoice checker web app.

    python -m streamlit run app.py
"""
import os
from datetime import date

import pandas as pd
import streamlit as st

from engine import checks, config, db, importer, receipts, scoring, tamper

st.set_page_config(page_title="Invoice Checker", page_icon="🧾", layout="wide")
CURRENCIES = ["USD", "EUR", "GBP", "INR", "MYR", "Other"]
IMAGE_TYPES = ["png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff"]
TABLE_TYPES = ["xlsx", "xls", "csv"]
TEMPLATE = os.path.join(config.ROOT, "templates", "company_records_template.csv")
SHOW_VERDICT = {"SUSPICIOUS": st.error, "REVIEW": st.warning, "OK": st.success}


@st.cache_resource
def _model(mtime: float):  # keyed on file time so a retrain reloads it
    return scoring.load_model()


def anomaly_model():
    path = config.ANOMALY_MODEL_PATH
    return _model(os.path.getmtime(path)) if os.path.exists(path) else None


# One connection per browser session: a sqlite3 connection must not be shared across sessions' threads
if "conn" not in st.session_state:
    st.session_state.conn = db.connect()
conn = st.session_state.conn
n_records = db.count_invoices(conn)

with st.sidebar:
    st.header("🧾 Invoice Checker")
    st.metric("Company records", f"{n_records:,}")
    st.caption("Anomaly model: " + ("trained" if anomaly_model() else "not trained (rules only)"))
    st.caption("Image-tamper model: " + ("loaded" if tamper.available() else "missing"))

if n_records == 0:
    st.info("**Start here.** Open **Company records**, download the template or upload your own export of past "
            "invoices, import it, then click **Audit records & train model**. After that you can check new invoices.")

tab_one, tab_batch, tab_records = st.tabs(["Check one invoice", "Check a batch", "Company records"])

# ── Check one invoice ──────────────────────────────────────────────────────
with tab_one:
    mode = st.radio("Input", ["Photo / scan", "PDF", "Type it in"], horizontal=True)
    fields, image, tamper_checkable, file_key = {}, None, False, None
    if mode != "Type it in":
        up = st.file_uploader("Receipt or invoice", type=IMAGE_TYPES if mode == "Photo / scan" else ["pdf"])
        if up:
            file_key = f"{up.name}-{up.size}"
            if st.session_state.get("ocr_key") != file_key:
                with st.spinner("Reading the document..."):
                    text, img, checkable = receipts.extract(up.name, up.getvalue())
                st.session_state.update(ocr_key=file_key, ocr=(text, img, checkable), fields=receipts.parse_fields(text))
            text, image, tamper_checkable = st.session_state.ocr
            fields = st.session_state.fields
            left, right = st.columns([1, 2])
            if image is not None:
                left.image(image, width="stretch")
            right.caption("Fields read from the document. Correct anything misread before checking.")
            with right.expander("Raw text"):
                st.text(text or "(no text found)")

    with st.form(f"fields-{file_key}"):
        c1, c2 = st.columns(2)
        vendor = c1.text_input("Vendor", fields.get("vendor_name") or "")
        invoice_id = c2.text_input("Invoice / receipt number", fields.get("invoice_id") or "")
        parsed = pd.to_datetime(fields.get("invoice_date"), errors="coerce")
        inv_date = c1.date_input("Invoice date", parsed.date() if pd.notna(parsed) else date.today())
        amount = c2.number_input("Total amount", min_value=0.0, value=float(fields.get("amount") or 0.0), step=0.01, format="%.2f")
        cur = fields.get("currency") or "USD"
        currency = c1.selectbox("Currency", CURRENCIES, index=CURRENCIES.index(cur) if cur in CURRENCIES else 0)
        submitted = st.form_submit_button("Check", type="primary")

    if submitted:
        if not vendor.strip() or not invoice_id.strip() or amount <= 0:
            st.error("Vendor, invoice number and a positive amount are required.")
        else:
            new = pd.DataFrame([dict(invoice_id=invoice_id.strip(), vendor_name=vendor.strip(),
                                     invoice_date=inv_date.isoformat(), amount=amount, currency=currency)])
            with st.spinner("Checking against company records..."):
                res, related = checks.check(conn, new, anomaly_model())
                tamper_result = tamper.tamper_score(image) if image is not None and tamper_checkable else None
            v = checks.verdict(res.iloc[0].risk_category, bool(tamper_result and tamper_result[1]))
            checks.log_checks(conn, res, source=mode, file_name=file_key,
                              tamper_score=tamper_result[0] if tamper_result else None, verdicts=[v])
            st.session_state.last_check = (res, related, tamper_result, v)

    if "last_check" in st.session_state:
        res, related, tamper_result, v = st.session_state.last_check
        r = res.iloc[0]
        st.divider()
        SHOW_VERDICT[v](f"**{v}**  ·  risk score {r.final_score:.2f} ({r.risk_category})")
        reasons = r.reasons.split("; ")
        if tamper_result and tamper_result[1]:
            reasons.append(f"The image may have been edited ({tamper_result[0]:.0%} tamper probability): "
                           "compare it with the original document")
        st.markdown("\n".join(f"- {x}" for x in reasons))
        if not related.empty:
            st.markdown("**Matching records**")
            st.dataframe(related.drop(columns=["new_id"]), hide_index=True)
        with st.expander("Score details"):
            st.write(f"Rules {r.rule_score:.2f} · anomaly model {r.ml_score:.2f} · combined 60/40 into {r.final_score:.2f}. "
                     + ("Image-tamper probability: " + (f"{tamper_result[0]:.0%}" if tamper_result else
                        "not checked (only uploaded photos/scans are; PDFs lose the traces it reads)")))
        if st.button("Add to company records", help="Once the invoice is approved/paid, so later copies are caught"):
            checks.add_to_records(conn, res)
            del st.session_state["last_check"]
            st.success("Saved to records.")

# ── Check a batch ──────────────────────────────────────────────────────────
with tab_batch:
    st.caption("Upload the invoices due for payment (CSV or Excel), same columns as the records template.")
    up = st.file_uploader("Invoices to check", type=TABLE_TYPES, key="batch_file")
    dayfirst = st.checkbox("Dates are day-first (dd/mm/yyyy)", key="batch_dayfirst")
    if up:
        rows, problems = importer.normalize(importer.read_table(up), dayfirst=dayfirst)
        for p in problems:
            st.warning(p)
        if not rows.empty and st.button(f"Check {len(rows):,} invoices", type="primary"):
            with st.spinner("Checking..."):
                res, _ = checks.check(conn, rows, anomaly_model())
            checks.log_checks(conn, res, source="batch", file_name=up.name)
            st.session_state.batch = res

    if "batch" in st.session_state:
        res = st.session_state.batch
        counts = res["verdict"].value_counts()
        c1, c2, c3 = st.columns(3)
        c1.metric("Suspicious", int(counts.get("SUSPICIOUS", 0)))
        c2.metric("Review", int(counts.get("REVIEW", 0)))
        c3.metric("OK", int(counts.get("OK", 0)))
        view = res[["verdict", "invoice_id", "vendor_name", "invoice_date", "amount", "currency", "final_score", "reasons"]]
        st.dataframe(view.sort_values("final_score", ascending=False), hide_index=True)
        st.download_button("Download results (CSV)", view.to_csv(index=False), "check_results.csv", "text/csv")
        if st.button("Add all OK invoices to company records"):
            n = checks.add_to_records(conn, res[res["verdict"] == "OK"], source="batch-check")
            del st.session_state["batch"]
            st.success(f"Saved {n:,} invoices to records.")

# ── Company records ────────────────────────────────────────────────────────
with tab_records:
    st.subheader("1. Import past invoices")
    st.markdown("Export paid invoices from your accounting system (SAP, Oracle, QuickBooks, Tally, Excel...) as CSV "
                "or Excel. **Required columns:** invoice number, vendor name, invoice date, amount. **Optional:** "
                "currency, vendor ID, PO number. Column names are matched loosely (*Invoice No*, *Supplier*, *Total* "
                "all work). Import more files any time; they are added to what's there.")
    with open(TEMPLATE, "rb") as f:
        st.download_button("Download template (CSV)", f.read(), "company_records_template.csv", "text/csv")
    up = st.file_uploader("Company records", type=TABLE_TYPES, key="import_file")
    dayfirst = st.checkbox("Dates are day-first (dd/mm/yyyy)", key="import_dayfirst")
    if up:
        rows, problems = importer.normalize(importer.read_table(up), dayfirst=dayfirst)
        for p in problems:
            (st.error if rows.empty else st.warning)(p)
        if not rows.empty:
            st.caption(f"{len(rows):,} rows ready. Recognized columns: {', '.join(rows.columns)}")
            st.dataframe(rows.head(20), hide_index=True)
            if st.button(f"Import {len(rows):,} records", type="primary"):
                n = importer.import_records(conn, rows, source=up.name)
                st.success(f"Imported {n:,} records. Now run step 2.")

    st.subheader("2. Audit records & train model")
    st.caption("Scores every record for duplicates and overpayments already paid, and trains the anomaly model on "
               "your history. Re-run after each import.")
    if st.button("Audit records & train model", disabled=n_records == 0):
        with st.spinner("Auditing..."):
            s = checks.audit(conn)
        st.success(f"Audited {s['records']:,} records: {s.get('HIGH', 0):,} high risk, {s.get('MEDIUM', 0):,} medium."
                   + ("" if s["model_trained"] else f" Anomaly model skipped: needs {config.MIN_RECORDS_FOR_MODEL}+ records."))

    st.subheader("Highest-risk records (last audit)")
    st.dataframe(db.query(conn, """
        SELECT i.id, i.invoice_id, i.vendor_name, i.invoice_date, i.amount, d.final_score, d.risk_category, d.flags
        FROM detection_results d JOIN invoices i ON i.id = d.invoice_row_id
        WHERE d.risk_category != 'LOW' ORDER BY d.final_score DESC LIMIT 500"""), hide_index=True)

    st.subheader("Recent checks")
    st.dataframe(db.query(conn, "SELECT checked_at, source, invoice_id, vendor_name, amount, verdict, final_score, "
                                "tamper_score FROM receipt_checks ORDER BY id DESC LIMIT 200"), hide_index=True)

    with st.expander("All records (latest 1,000)"):
        st.dataframe(db.query(conn, "SELECT * FROM invoices ORDER BY id DESC LIMIT 1000"), hide_index=True)
