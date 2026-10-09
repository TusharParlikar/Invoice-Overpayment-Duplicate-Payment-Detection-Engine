# Invoice Overpayment & Duplicate Payment Detection Engine

**Check an invoice before you pay it.** Load your company's past invoices once. After that, every new invoice
(a photo, scan, PDF, typed-in entry or Excel batch) is compared with that history and gets a verdict with reasons:

| Verdict | Meaning | What to do |
|---|---|---|
| **SUSPICIOUS** | It duplicates an invoice already on record, or the amount is far above what this vendor normally charges | Hold the payment and investigate |
| **REVIEW** | Something is unusual but not conclusive (odd amount, a known invoice number with a new amount, or the image may have been edited) | Someone takes a second look |
| **OK** | No match with existing records, and the amount is normal for this vendor | Pay as usual |

It catches duplicates that exact-match ERP checks miss (`SAP-2024-123456` vs `SAP2024123456`, "Acme Corp" vs
"ACME CORPORATION LLC", dates a few days apart). Everything runs locally: no data leaves your machine.

## Problem statement

An accounts-payable team pays every invoice that reaches it, often thousands a month, from many vendors, in many formats.
Two kinds of error slip through:

1. **Duplicate payments.** The same invoice is paid twice because it arrived twice (email and paper, a reminder copy), was
   typed in with a different number format (`INV-0042` / `INV0042`), was misread by OCR (`O` for `0`), was entered under a
   differently spelled vendor name, or was re-sent with tax added or the amount rounded up.
2. **Overpayments.** An invoice is far above what that vendor normally charges, through a typing error, a wrong unit price,
   or fraud.

Accounting systems usually block a duplicate only when vendor, invoice number and amount match **exactly**, so every variant
above passes. And edited receipt images (a changed total, a changed date) look genuine to a person checking them by eye.

**Goal:** before an invoice is paid, compare it with everything already paid and say, with reasons, whether to pay it, look
at it again, or hold it, catching the inexact duplicates, unusual amounts and edited images that exact matching misses.

## Why we need this

- **It's a measurable leak.** In APQC's accounts-payable benchmark, even top-performing organizations report that **0.8% of
  their annual disbursements are duplicate or erroneous**; the bottom performers report **2%**
  ([APQC via CFO.com, 2020](https://www.cfo.com/news/metric-of-the-month-detect-and-prevent-duplicate-or-erroneous-payments/656852/)).
  These are shares of payments, not of money: a company making 50,000 payments a year would make roughly 400 to 1,000
  duplicate or wrong ones.
- **Catching it before payment is cheaper than recovering it after.** Once money has gone out, someone has to notice, contact
  the vendor and wait for a refund or credit note. A check before payment just holds the invoice.
- **Exact-match checks miss the common cases.** On the real receipts in the benchmark below, a different number format, an OCR
  slip, a vendor-name variant or a shifted date each pass an exact-match check; this engine catches 97–100% of them.
- **Small teams have no tool for it.** Duplicate-detection modules and recovery audits are built for large ERPs. This runs on
  one laptop from a CSV export, keeps the data in-house, and explains every verdict in plain words.

## Demo

▶️ **[Watch the demo video](https://drive.google.com/file/d/1J0F_hpMlzdyAN1gmBoHU7uYEiX8FU7Yz/view?usp=drive_link)** (3¾ minutes, narrated, on Google Drive): importing a year of invoices, an audit that
finds money already lost, a batch check that holds duplicates and overpayments, a receipt photo recognized as already
paid, and an edited receipt with the changed area outlined.

To try it yourself, [demo/](demo/) has the same mock data (all vendors are fictional): `company_records.csv` to import,
`invoices_to_check.csv` with the expected verdict for each row, and two receipt images. Keep your real records separate
by starting the app on a demo database:

```powershell
$env:INVOICE_DB_PATH = "data\demo.db"          # macOS/Linux: export INVOICE_DB_PATH=data/demo.db
python -m streamlit run app.py
```

---

## 1. Install

Python 3.11 or newer.

```bash
pip install -r requirements.txt
python tests/test_engine.py        # should print 6 "ok" lines
python tests/test_receipts.py      # OCR, parser and tamper model on 4 real receipts: 3 "ok" lines
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
**Import**. The date order (`03/01/2024`: 3 January or March 1?) is detected from the file: any date like `13/01/2024`
means day-first. If every date could be either, the app says so and reads them month-first; choose *Day first* in that
case. You can import several files (one per year or per ERP); each import adds to the records. Rows already in the
records (same vendor, number, date and amount) are skipped and counted, so importing a file twice does no harm.

**Step 3: Check the exchange rates** (only if you pay in more than one currency). Amounts are converted to USD before
they are compared. The app ships approximate rates for 12 common currencies; set your own under **Company records →
Exchange rates**. A check tells you when an invoice's currency has no rate.

**Step 4: Audit.** Click **Audit records**. This scans all records for duplicates already paid (listed under
*Highest-risk records*). It also merges vendor IDs the engine created for one company under two spellings
(`AEON CO. (M) BHD (126926-H)` and `AEON CO. (M) BHD`); vendor IDs from your own system are never changed. Re-run it
after each import.

Same steps from the command line:

```bash
python cli.py import invoices_2024.xlsx        # date order detected; force it with --dayfirst or --monthfirst
python cli.py audit
```

**No data yet?** `python cli.py demo` loads about 530 real scanned receipts as sample records (it downloads a 670 MB
research dataset once).

## 3. Check new invoices

In the app:

- **Check one invoice:** upload a photo, scan or PDF (the fields are read automatically, and you correct anything
  misread) or type the fields in, then click **Check**. If no invoice number could be read, the form says so: type it
  in, since duplicates are matched on it. If the vendor's name on the document differs from your records, the result
  suggests the closest known vendor; fix the name or enter its **Vendor ID**. Once the invoice is paid, click **Add to
  company records** so a later copy of it is caught (not offered for a SUSPICIOUS invoice).
- **Check a batch:** upload the invoices due for payment (same columns as above). You get a verdict per invoice and a
  CSV download.

From the command line: `python cli.py check due_this_week.xlsx --out results.csv`.

Every check is logged in the *Recent checks* table, with the name entered under *Your name* in the sidebar (the
command line logs your system user name).

---

## What it checks

| Check | Fires when | Verdict |
|---|---|---|
| Exact duplicate | Same vendor, invoice number (ignoring dashes and spaces) and amount as a record, however long ago | SUSPICIOUS |
| Near duplicate | Same invoice in a different format: number differs only by dashes/spaces or a typo, or vendor name spelled differently (also with "The" in front); amount within 0.5%, date within 7 days. The vendor's next invoice (sequential number, new date) is not a duplicate | SUSPICIOUS |
| Same number, new amount | Same vendor and invoice number (ignoring dashes and spaces) as a record, but a different amount: tax added, rounded up, a "corrected" copy | REVIEW |
| Overpayment | At least 3× the median of the vendor's other invoices **and** far outside how much that vendor's amounts normally vary (needs 3+ past invoices) | SUSPICIOUS |
| Possible overpayment | At least 2× the median and clearly outside the vendor's normal range | REVIEW |
| Burst | 3+ invoices from one vendor on the same PO and date | SUSPICIOUS |
| Large round amount | At least 25,000, a multiple of 5,000, and 2.5× the vendor's median | REVIEW |
| Image tamper | Pixel statistics suggest part of a photo or scan was edited; the app outlines the area | REVIEW at most |

All thresholds are in [engine/config.py](engine/config.py). How each part works, the models, and how they were
validated: [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md).

## Results and limitations

Measured on real data: `python tests/benchmark.py`, 10-fold cross-validation on the 534 real receipts that
`python cli.py demo` loads from a public dataset, so anyone can reproduce it:

| | Result |
|---|---|
| Duplicates: exact copy, retyped number, OCR misread, date shifted, vendor name written differently, "The" added | 97–100% caught |
| Same number re-billed with tax added or rounded up | 98% caught (REVIEW) |
| Genuine receipts flagged | 5.3% (2.6% held as SUSPICIOUS, the rest REVIEW) |
| Overpayment 10× / 5× / 3× | 53% / 40% / 26% caught |
| Edited receipt images, original scan (held-out test set) | 71% caught; 71% of flags are real edits; 5.5% of genuine flagged |
| Edited receipt images, same scans as JPEG quality 90 | 66% caught; 5.5% of genuine flagged |

- **Photos and emailed receipts:** the tamper check works on scans and good JPEGs (quality 85+). A JPEG below quality 85
  erases the traces it reads (3% caught at quality 75), so the app says *not checked* instead of implying the image is clean.
  A receipt that was **downscaled** can't be judged either (0% caught at half size), and this can't be detected from the
  file: upload the original. OCR doesn't always read the invoice number correctly, so check the number in the form; the
  form warns when none was found.
- Overpayment recall is low on this data because retail shop receipts vary enormously (one shop's receipts range from 5 to 465).
  With steady supplier invoices an unusual amount stands out far more. Accuracy on your own data: import a labelled sample with an
  `is_anomaly` column and the audit reports precision and recall.
- The tamper model was trained only on Malaysian retail receipts; other document types are untested. A tamper flag only asks for a
  review, and the outlined area shows the reviewer what to compare.
- **Speed** (100,000 records, laptop CPU): checking 1 invoice takes 0.2–0.3 s, a batch of 50 under 1.5 s, and a full audit
  11–25 s. A check slows to about 30 s if most vendor names start with the same 3 letters. OCR adds 7–15 s per photo, and the
  tamper check a few seconds.
- Very different spellings of one vendor still count as a new vendor unless you give its vendor ID.

### Where the numbers come from

| Claim | Source | Reproduce |
|---|---|---|
| Duplicates 97–100% caught, re-billing 98%, genuine receipts flagged 5.3%, overpayment 53% / 40% / 26% | [tests/benchmark.py](tests/benchmark.py): 10-fold test on 534 real receipts from a public dataset; full table in [HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md#evaluation-honesty) | `python tests/benchmark.py` (downloads 670 MB once) |
| Edited images 71% caught, 71% of flags right, 66% on JPEG quality 90; 3% at quality 75, 0% at half size | Held-out test set of the tamper model; tables in [HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md#image-tamper-model-enginetamperpy) | `python cli.py train-tamper` |
| Speed: 0.2–0.3 s per check, 11–25 s audit on 100,000 records | [tests/speed.py](tests/speed.py): synthetic records, 2,000 vendors | `python tests/speed.py` |
| OCR adds 7–15 s per photo | Observed while using the app; no script yet | — |
| 0.8%–2% of payments are duplicate or erroneous | [APQC benchmark, via CFO.com](https://www.cfo.com/news/metric-of-the-month-detect-and-prevent-duplicate-or-erroneous-payments/656852/) | external |

## Running it for a team

Where to keep the database, backups and samples, and how to move to a shared online database for several stores:
[docs/STORE.md](docs/STORE.md).

- **Where:** a machine inside the company network: `python -m streamlit run app.py --server.address 0.0.0.0`. Colleagues
  open `http://<machine>:8501`. Don't use free hosting with real invoices; its disk is wiped on restart.
- **Password:** set `INVOICE_APP_PASSWORD` before starting the app, and everyone has to enter it. This is a single shared password,
  with no individual accounts or roles; the check log records the name each person types in the sidebar, which is not verified.
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
  scoring.py        cleaning, near-duplicate matching, per-vendor amount statistics, rules, risk score
  checks.py         audit all records; check new invoices; reasons
  receipts.py       OCR and field extraction from photos/PDFs
  tamper.py         image-tamper model and its training
  db.py             SQLite schema and helpers
templates/          company records template
models/tamper.joblib  shipped image-tamper model
demo/               mock data, and the scripts that make it and record the demo video (make_demo_data.py, record_demo_video.py)
tests/test_engine.py  rules, import and parser tests, run by GitHub Actions on every push
tests/test_receipts.py  OCR + parser + tamper model on 4 committed receipts (tests/fixtures/), also run in CI
tests/benchmark.py    accuracy on real records (10-fold)
tests/speed.py        timing on 100,000 synthetic records
docs/HOW_IT_WORKS.md  how each part works and how it was validated
docs/STORE.md         where to store the data, locally and online
```

## Credits

- Receipt data: *Find it again! A Receipt Dataset for Document Forgery Detection*, B. Martínez Tornés et al.,
  ICDAR 2023, L3i, University of La Rochelle
  ([dataset page](https://l3i-share.univ-lr.fr/2023Finditagain/index.html)). Published for research; check its terms
  before commercial use of the tamper model.
- OCR: [RapidOCR](https://github.com/RapidAI/RapidOCR). String similarity: [RapidFuzz](https://github.com/rapidfuzz/RapidFuzz).
  Tamper model: [scikit-learn](https://scikit-learn.org/).

Code: [MIT License](LICENSE). The shipped tamper model was trained on a research dataset; see its terms above.
