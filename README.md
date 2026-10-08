# Invoice Overpayment & Duplicate Payment Detection Engine

**Check an invoice before you pay it.** Load your company's past invoices once. After that, every new invoice
(a photo, scan, PDF, typed-in entry or Excel batch) is compared with that history and gets a verdict with reasons:

| Verdict | Meaning | What to do |
|---|---|---|
| **SUSPICIOUS** | It duplicates an invoice already on record, or the amount is far above what this vendor normally charges | Hold the payment and investigate |
| **REVIEW** | Something is unusual but not conclusive (odd amount, or the image may have been edited) | Someone takes a second look |
| **OK** | No match with existing records, and the amount is normal for this vendor | Pay as usual |

It catches duplicates that exact-match ERP checks miss (`SAP-2024-123456` vs `SAP2024123456`, "Acme Corp" vs
"ACME CORPORATION LLC", dates a few days apart). Everything runs locally: no data leaves your machine.

---

## 1. Install

Python 3.10 or newer.

```bash
pip install -r requirements.txt
python tests/test_engine.py        # should print 4 "ok" lines
python -m streamlit run app.py     # opens http://localhost:8501
```

## 2. Load your company's data

The engine needs your **past invoices** (ideally 6 to 24 months of paid invoices) to know what is normal and what
has already been paid.

**Step 1: Export from your accounting system.** Any system that can export CSV or Excel works (SAP, Oracle,
QuickBooks, Xero, Tally, or a spreadsheet). One row per invoice:

| Column | Required? | Example | Also recognized as |
|---|---|---|---|
| Invoice Number | **yes** | `INV-2024-0001` | Invoice No, Invoice ID, Document Number, Receipt No, Bill No, Reference |
| Vendor Name | **yes** | `Acme Supplies Ltd` | Vendor, Supplier, Supplier Name, Payee, Merchant |
| Invoice Date | **yes** | `2024-01-15` | Date, Document Date, Posting Date, Bill Date |
| Amount | **yes** | `1250.00` | Total, Invoice Amount, Gross Amount, Amount Paid |
| Currency | no (default USD) | `USD` | Curr, CCY |
| Vendor ID | no, but recommended | `V-1001` | Vendor Code, Supplier ID, Supplier Code |
| PO Number | no | `PO-5501` | PO, Purchase Order |

Column names are matched ignoring case, spaces, `_`, `-` and `.`. Amounts may contain currency symbols and
thousands separators (`$1,250.00`). Rows missing a required value are skipped and reported. A ready-made example is
in [templates/company_records_template.csv](templates/company_records_template.csv) and can also be downloaded in the app.

**Step 2: Import it.** In the app open **Company records**, then upload the file, check the preview, and click
**Import**. If your dates are written day-first (`15/01/2024`), tick *Dates are day-first*. You can import several
files (one per year or per ERP); each import adds to the records.

**Step 3: Audit.** Click **Audit records & train model**. This scans all records for duplicates already paid
(listed under *Highest-risk records*) and trains the anomaly model on your history. Re-run it after each import.

Same steps from the command line:

```bash
python cli.py import invoices_2024.xlsx        # add --dayfirst for dd/mm/yyyy dates
python cli.py audit
```

**No data yet?** `python cli.py demo` loads about 380 real scanned receipts as sample records (it downloads a 670 MB
research dataset once).

## 3. Check new invoices

In the app:

- **Check one invoice:** upload a photo, scan or PDF (the fields are read automatically, and you correct anything
  misread) or type the fields in, then click **Check**. Once the invoice is paid, click **Add to company records** so a
  later copy of it is caught.
- **Check a batch:** upload the invoices due for payment (same columns as above). You get a verdict per invoice and a
  CSV download.

From the command line: `python cli.py check due_this_week.xlsx --out results.csv`.

Every check is logged in the *Recent checks* table.

---

## What it checks

| Check | Fires when | Verdict |
|---|---|---|
| Exact duplicate | Same vendor, invoice number and amount as a record | SUSPICIOUS |
| Near duplicate | Same invoice in a different format: number differs only by dashes/spaces or a typo, or vendor name spelled differently; amount within 0.5%, date within 7 days | SUSPICIOUS |
| Overpayment | At least 3× the vendor's median invoice **and** 3.5 standard deviations above its mean | SUSPICIOUS |
| Burst | 3+ invoices from one vendor on the same PO and date | SUSPICIOUS |
| Large round amount | At least 25,000, a multiple of 5,000, and 2.5× the vendor's median | REVIEW |
| Anomaly model | Unusual compared with your own payment history (Isolation Forest) | REVIEW at most |
| Image tamper | Pixel statistics suggest a photo or scan was edited | REVIEW at most |

All thresholds are in [engine/config.py](engine/config.py). How each part works, the models, and how they were
validated: [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md).

## Results and limitations

- **Real receipts:** with 377 genuine receipts loaded as history, the audit flags 6 as high risk; 4 of them are the
  same receipt recorded twice. Re-uploading a receipt already on record is caught as an exact duplicate.
- **Rules:** no accuracy figure on real company data yet. If your records carry an `is_anomaly` column (1 = known
  bad), the audit reports precision and recall on them.
- **Image-tamper model:** weak. On held-out receipts it catches about 46% of edited images, and about 39% of its flags are
  real edits. That is why it can only ever ask for a review, and genuine receipts are sometimes flagged.
- **No login.** Run it on a machine inside your company network; anyone who can open the page sees the records.
- Receipts carry vendor names, not IDs: very different spellings of one vendor count as a new vendor.
- Currency conversion covers USD, EUR and GBP only (fixed rates in `engine/config.py`). Don't mix other currencies
  for the same vendor.
- OCR takes about 7–15 s per photo on a laptop CPU and sometimes misreads fields, so you confirm them in a form first.

## Project layout

```
app.py              web app (Streamlit)
cli.py              command line: import, audit, check, demo, train-tamper
engine/
  config.py         every threshold and path
  importer.py       CSV/Excel → records (column matching, vendor IDs)
  scoring.py        cleaning, near-duplicate matching, features, rules, anomaly model, risk score
  checks.py         audit all records; check new invoices; reasons
  receipts.py       OCR and field extraction from photos/PDFs
  tamper.py         image-tamper model and its training
  db.py             SQLite schema and helpers
templates/          company records template
models/tamper.joblib  shipped image-tamper model (the anomaly model is trained per company and not committed)
tests/test_engine.py
```

## Credits

- Receipt data: *Find it again! A Receipt Dataset for Document Forgery Detection*, B. Martínez Tornés et al.,
  ICDAR 2023, L3i, University of La Rochelle
  ([dataset page](https://l3i-share.univ-lr.fr/2023Finditagain/index.html)). Published for research; check its terms
  before commercial use of the tamper model.
- OCR: [RapidOCR](https://github.com/RapidAI/RapidOCR). String similarity: [RapidFuzz](https://github.com/rapidfuzz/RapidFuzz).
  Models: [scikit-learn](https://scikit-learn.org/).

No license file has been added yet, so all rights are reserved by the author.
