"""
Invoice checker web app.

    python -m streamlit run app.py
"""
import hmac
import os
import re
import threading
from datetime import date

import pandas as pd
import streamlit as st
from PIL import ImageDraw

from engine import checks, config, db, importer, receipts, scoring, tamper

st.set_page_config(page_title="Invoice Checker", page_icon="🧾", layout="wide")
IMAGE_TYPES = ["png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff"]
TABLE_TYPES = ["xlsx", "xls", "csv"]
TEMPLATE = os.path.join(config.ROOT, "templates", "company_records_template.csv")
SHOW_VERDICT = {"SUSPICIOUS": st.error, "REVIEW": st.warning, "OK": st.success}
DATE_ORDER = {"Detect from the file": None, "Day first (dd/mm/yyyy)": True, "Month first (mm/dd/yyyy)": False}


def md(text: str) -> str:
    """Escape Markdown: vendor names and invoice numbers come from uploads and must not render as links or images."""
    return re.sub(r"([!-/:-@\[-`{-~])", r"\\\1", str(text))


@st.cache_resource
def _preload_ocr():
    """Load the OCR engine (20-30 s) in the background at startup instead of on the first upload."""
    threading.Thread(target=receipts.load_engine, daemon=True).start()


# Optional shared password: set INVOICE_APP_PASSWORD before starting the app
PASSWORD = os.environ.get("INVOICE_APP_PASSWORD")
if PASSWORD and not st.session_state.get("signed_in"):
    entered = st.text_input("Password", type="password")
    if entered and hmac.compare_digest(entered.encode(), PASSWORD.encode()):
        st.session_state.signed_in = True
        st.rerun()
    if entered:
        st.error("Wrong password.")
    st.stop()

_preload_ocr()

# One connection per browser session: a sqlite3 connection must not be shared across sessions' threads
if "conn" not in st.session_state:
    st.session_state.conn = db.connect()
conn = st.session_state.conn
n_records = db.count_invoices(conn)

with st.sidebar:
    st.header("🧾 Invoice Checker")
    st.metric("Company records", f"{n_records:,}")
    # The password is shared, so the check log records the name each person enters here
    user = st.text_input("Your name", key="user", help="Saved with every check you run (Recent checks)").strip() or None
    st.caption("Image-tamper model: " + ("loaded" if tamper.available() else "missing or unreadable"))
    if tamper.version_warning():
        st.warning(tamper.version_warning())

if n_records == 0:
    st.info("**Start here.** Open **Company records**, download the template or upload your own export of past "
            "invoices, import it, then click **Audit records**. After that you can check new invoices.")

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
            if not fields.get("invoice_id"):
                right.warning("No invoice number found. Type it from the document: duplicates are matched on it, "
                              "so without it a copy of a paid invoice may not be caught.")
            with right.expander("Raw text"):
                st.text(text or "(no text found)")

    with st.form(f"fields-{file_key}"):
        c1, c2 = st.columns(2)
        vendor = c1.text_input("Vendor", fields.get("vendor_name") or "")
        invoice_id = c2.text_input("Invoice / receipt number", fields.get("invoice_id") or "")
        parsed = pd.to_datetime(fields.get("invoice_date"), errors="coerce")
        inv_date = c1.date_input("Invoice date", parsed.date() if pd.notna(parsed) else date.today())
        amount = c2.number_input("Total amount", min_value=0.0, value=float(fields.get("amount") or 0.0), step=0.01, format="%.2f")
        currencies = sorted(scoring.usd_rates())  # the ones with an exchange rate (Company records > Exchange rates)
        cur = fields.get("currency") if fields.get("currency") in currencies else "USD"
        currency = c1.selectbox("Currency", currencies, index=currencies.index(cur))
        vendor_id = c2.text_input("Vendor ID (optional)", help="Your system's vendor code. Use it when the name on the "
                                  "document differs from the name in your records")
        submitted = st.form_submit_button("Check", type="primary")

    if submitted:
        if not vendor.strip() or not invoice_id.strip() or amount <= 0:
            st.error("Vendor, invoice number and a positive amount are required.")
        else:
            new = pd.DataFrame([dict(invoice_id=invoice_id.strip(), vendor_name=vendor.strip(), vendor_id=vendor_id.strip() or None,
                                     invoice_date=inv_date.isoformat(), amount=amount, currency=currency)])
            with st.spinner("Checking against company records..."):
                res, related = checks.check(conn, new)
                tamper_skip = (tamper.cannot_assess(image) if image is not None and tamper_checkable else
                               "not checked (only uploaded photos/scans are; PDFs lose the traces it reads)")
                tamper_result = None if tamper_skip else tamper.tamper_score(image)
            v = checks.verdict(res.iloc[0].risk_category, bool(tamper_result and tamper_result[1]))
            checks.log_checks(conn, res, source=mode, file_name=file_key,
                              tamper_score=tamper_result[0] if tamper_result else None, verdicts=[v], checked_by=user)
            st.session_state.last_check = (res, related, tamper_result, tamper_skip, v, image)

    if "last_check" in st.session_state:
        res, related, tamper_result, tamper_skip, v, checked_image = st.session_state.last_check
        r = res.iloc[0]
        st.divider()
        SHOW_VERDICT[v](f"**{v}**  ·  risk score {r.final_score:.2f} ({r.risk_category})")
        reasons = r.reasons.split("; ")
        if tamper_result and tamper_result[1]:
            reasons.append(f"The image may have been edited ({tamper_result[0]:.0%} tamper probability): "
                           "compare the outlined area with the original document")
        if tamper_skip and "JPEG" in tamper_skip:  # say "can't assess", so it isn't read as "not edited"
            reasons.append("Image " + tamper_skip)
        st.markdown("\n".join(f"- {md(x)}" for x in reasons))
        if tamper_result and tamper_result[1] and tamper_result[2]:
            x, y, w, h = tamper_result[2]
            pad = 3 * w  # show the block with its surroundings
            outlined = checked_image.convert("RGB")
            ImageDraw.Draw(outlined).rectangle([x - 4, y - 4, x + w + 4, y + h + 4], outline=(220, 30, 30), width=4)
            crop = outlined.crop((max(0, x - pad), max(0, y - pad),
                                  min(outlined.width, x + w + pad), min(outlined.height, y + h + pad)))
            st.image([outlined, crop], caption=["Most suspicious area", "Close-up"], width=320)
        if not related.empty:
            st.markdown("**Matching records**")
            st.dataframe(related.drop(columns=["new_id"]), hide_index=True)
        with st.expander("Score details"):
            st.text(f"Risk score {r.final_score:.2f}: the strongest rule that fired (flags: {r.flags}). "
                    + "Image-tamper probability: " + (f"{tamper_result[0]:.0%}" if tamper_result else tamper_skip or "not checked"))
        if v == "SUSPICIOUS":  # adding it would make the duplicate or overpayment part of the vendor's history
            st.caption("A suspicious invoice can't be added to company records from here. If it turns out to be fine, "
                       "add it with an import.")
        elif st.button("Add to company records", help="Once the invoice is approved/paid, so later copies are caught"):
            n = checks.add_to_records(conn, res)
            del st.session_state["last_check"]
            st.success("Saved to records." if n else "Already in the records.")

# ── Check a batch ──────────────────────────────────────────────────────────
with tab_batch:
    st.caption("Upload the invoices due for payment (CSV or Excel), same columns as the records template.")
    up = st.file_uploader("Invoices to check", type=TABLE_TYPES, key="batch_file")
    order = st.radio("Date order", list(DATE_ORDER), horizontal=True, key="batch_date_order")
    if up:
        rows, problems = importer.normalize(importer.read_table(up), dayfirst=DATE_ORDER[order])
        for p in problems:
            st.warning(md(p))
        if not rows.empty and st.button(f"Check {len(rows):,} invoices", type="primary"):
            with st.spinner("Checking..."):
                res, _ = checks.check(conn, rows)
            checks.log_checks(conn, res, source="batch", file_name=up.name, checked_by=user)
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
        st.download_button("Download results (CSV)", checks.to_csv(view), "check_results.csv", "text/csv")
        if st.button("Add all OK invoices to company records"):
            ok = res[res["verdict"] == "OK"]
            n = checks.add_to_records(conn, ok, source="batch-check")
            del st.session_state["batch"]
            st.success(f"Saved {n:,} invoices to records." + (f" {len(ok) - n:,} were already there." if len(ok) > n else ""))

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
    order = st.radio("Date order", list(DATE_ORDER), horizontal=True, key="import_date_order")
    if up:
        rows, problems = importer.normalize(importer.read_table(up), dayfirst=DATE_ORDER[order])
        for p in problems:
            (st.error if rows.empty else st.warning)(md(p))
        if not rows.empty:
            st.caption(f"{len(rows):,} rows ready. Recognized columns: {', '.join(rows.columns)}")
            st.dataframe(rows.head(20), hide_index=True)
            if st.button(f"Import {len(rows):,} records", type="primary"):
                if n_records:
                    db.backup(conn)
                n = importer.import_records(conn, rows, source=up.name)
                st.success(f"Imported {n:,} records. Now run step 2."
                           + (f" Skipped {len(rows) - n:,} already in the records." if len(rows) > n else ""))

    st.subheader("2. Audit records")
    st.caption("Scores every record for duplicates and overpayments already paid. Re-run after each import.")
    if st.button("Audit records", disabled=n_records == 0):
        with st.spinner("Auditing..."):
            s = checks.audit(conn)
        st.success(f"Audited {s['records']:,} records: {s.get('HIGH', 0):,} high risk, {s.get('MEDIUM', 0):,} medium."
                   + (f" Merged {s['vendor_ids_merged']:,} duplicate vendor IDs." if s.get("vendor_ids_merged") else ""))

    with st.expander("Exchange rates"):
        st.caption("USD per one unit of each currency, used to compare amounts across currencies. Rates are applied to "
                   "new checks immediately and to stored results at the next audit.")
        rates = pd.DataFrame(sorted(scoring.usd_rates().items()), columns=["currency", "usd_per_unit"])
        edited = st.data_editor(rates, num_rows="dynamic", hide_index=True, key="rates")
        if st.button("Save rates"):
            edited = edited.dropna()
            edited["currency"] = edited["currency"].astype(str).str.upper().str.strip()
            os.makedirs(os.path.dirname(config.EXCHANGE_RATES_PATH), exist_ok=True)
            edited[edited["usd_per_unit"] > 0].to_csv(config.EXCHANGE_RATES_PATH, index=False)
            st.success("Saved.")

    st.subheader("Highest-risk records (last audit)")
    st.dataframe(db.query(conn, """
        SELECT i.id, i.invoice_id, i.vendor_name, i.invoice_date, i.amount, d.final_score, d.risk_category, d.flags
        FROM detection_results d JOIN invoices i ON i.id = d.invoice_row_id
        WHERE d.risk_category != 'LOW' ORDER BY d.final_score DESC LIMIT 500"""), hide_index=True)

    st.subheader("Recent checks")
    st.dataframe(db.query(conn, "SELECT checked_at, checked_by, source, invoice_id, vendor_name, amount, verdict, final_score, "
                                "tamper_score FROM receipt_checks ORDER BY id DESC LIMIT 200"), hide_index=True)

    with st.expander("All records (latest 1,000)"):
        st.dataframe(db.query(conn, "SELECT * FROM invoices ORDER BY id DESC LIMIT 1000"), hide_index=True)
