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
| Currency | no (default USD) | `USD`, `EUR`, `INR` | Curr, CCY |
| Vendor ID | no, but recommended | `V-1001` | Vendor Code, Supplier ID, Supplier Code |
| PO Number | no | `PO-5501` | PO, Purchase Order |

Column names are matched ignoring case, spaces, `_`, `-` and `.`. Amounts may contain currency symbols and
thousands separators (`$1,250.00`). Rows missing a required value are skipped and reported. A ready-made example is
in [templates/company_records_template.csv](templates/company_records_template.csv) and can also be downloaded in the app.

**Step 2: Import it.** In the app open **Company records**, then upload the file, check the preview, and click
**Import**. If your dates are written day-first (`15/01/2024`), tick *Dates are day-first*. You can import several
files (one per year or per ERP); each import adds to the records.

**Step 3: Check the exchange rates** (only if you pay in more than one currency). Amounts are converted to USD before
they are compared. The app ships approximate rates for 12 common currencies; set your own under **Company records →
Exchange rates**. A check tells you when an invoice's currency has no rate.

**Step 4: Audit.** Click **Audit records & train model**. This scans all records for duplicates already paid
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
  misread) or type the fields in, then click **Check**. If the vendor's name on the document differs from your records,
  the result suggests the closest known vendor; fix the name or enter its **Vendor ID**. Once the invoice is paid,
  click **Add to company records** so a later copy of it is caught.
- **Check a batch:** upload the invoices due for payment (same columns as above). You get a verdict per invoice and a
  CSV download.

From the command line: `python cli.py check due_this_week.xlsx --out results.csv`.

Every check is logged in the *Recent checks* table.

---

## What it checks

| Check | Fires when | Verdict |
|---|---|---|
| Exact duplicate | Same vendor, invoice number and amount as a record | SUSPICIOUS |
| Near duplicate | Same invoice in a different format: number differs only by dashes/spaces or a typo, or vendor name spelled differently; amount within 0.5%, date within 7 days. The vendor's next invoice (sequential number, new date) is not a duplicate | SUSPICIOUS |
| Overpayment | At least 3× the median of the vendor's other invoices **and** far outside how much that vendor's amounts normally vary (needs 3+ past invoices) | SUSPICIOUS |
| Possible overpayment | At least 2× the median and clearly outside the vendor's normal range | REVIEW |
| Burst | 3+ invoices from one vendor on the same PO and date | SUSPICIOUS |
| Large round amount | At least 25,000, a multiple of 5,000, and 2.5× the vendor's median | REVIEW |
| Anomaly model | Unusual compared with your own payment history (Isolation Forest) | REVIEW at most |
| Image tamper | Pixel statistics suggest part of a photo or scan was edited; the app outlines the area | REVIEW at most |

All thresholds are in [engine/config.py](engine/config.py). How each part works, the models, and how they were
validated: [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md).

## Results and limitations

Measured on real data (`python tests/benchmark.py`, 10-fold cross-validation on 377 real receipts):

| | Result |
|---|---|
| Duplicates: exact copy, retyped number, OCR misread, date shifted, vendor name written differently | 99–100% caught |
| Genuine receipts flagged | 3.8% (2.1% held as SUSPICIOUS, the rest REVIEW) |
| Overpayment 10× / 5× / 3× | 38% / 33% / 17% caught |
| Edited receipt images (held-out test set) | 69% caught; 75% of flags are real edits |

- Overpayment recall is low on this data because retail shop receipts vary enormously (one shop's receipts range from 5 to 465).
  With steady supplier invoices an unusual amount stands out far more. Accuracy on your own data: import a labelled sample with an
  `is_anomaly` column and the audit reports precision and recall.
- The tamper model was trained only on Malaysian retail receipts; other document types are untested. A tamper flag only asks for a
  review, and the outlined area shows the reviewer what to compare.
- **Speed** (100,000 records, laptop CPU): checking 1 invoice takes 0.5 s, and a full audit takes 7.5 s. OCR adds 7–15 s per photo, and the tamper check a few seconds.
- Very different spellings of one vendor still count as a new vendor unless you give its vendor ID.

## Running it for a team

- **Where:** a machine inside the company network: `python -m streamlit run app.py --server.address 0.0.0.0`. Colleagues
  open `http://<machine>:8501`. Don't use free hosting with real invoices; its disk is wiped on restart.
- **Password:** set `INVOICE_APP_PASSWORD` before starting the app, and everyone has to enter it. This is a single shared password,
  with no individual accounts or roles.
- **HTTPS:** add `--server.sslCertFile cert.pem --server.sslKeyFile key.pem` (or put it behind your company's reverse
  proxy). Without it, traffic, including the password, is unencrypted on the network.
- **Data at rest:** everything is in `data/invoices.db`. Keep the machine's disk encrypted (BitLocker, FileVault, LUKS).
- **Backups:** the app backs up the database before every import. Schedule `python cli.py backup` daily (Task Scheduler or
  cron). It writes `data/backups/`, keeps the last 30, and is safe while the app is running. To restore, stop the app and
  copy a backup over `data/invoices.db`.

## Project layout

```
app.py              web app (Streamlit)
cli.py              command line: import, audit, check, backup, demo, train-tamper
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
tests/test_engine.py  run by GitHub Actions on every push
tests/benchmark.py    accuracy on real records (10-fold)
```

## Credits

- Receipt data: *Find it again! A Receipt Dataset for Document Forgery Detection*, B. Martínez Tornés et al.,
  ICDAR 2023, L3i, University of La Rochelle
  ([dataset page](https://l3i-share.univ-lr.fr/2023Finditagain/index.html)). Published for research; check its terms
  before commercial use of the tamper model.
- OCR: [RapidOCR](https://github.com/RapidAI/RapidOCR). String similarity: [RapidFuzz](https://github.com/rapidfuzz/RapidFuzz).
  Models: [scikit-learn](https://scikit-learn.org/).

Code: [MIT License](LICENSE). The shipped tamper model was trained on a research dataset; see its terms above.
