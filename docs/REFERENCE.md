# Reference: CLI, Python API and configuration

**There is no HTTP/REST API.** The engine is used through the Streamlit app, four command-line entry points, and the
Python functions below (which is also how an HTTP API would be added: wrap `check_invoices`).

---

## 1. Command line

| Command | What it does | Output / side effects |
|---|---|---|
| `python -m intake.importer FILE [--dayfirst]` | Import invoices from CSV/Excel. `--dayfirst` reads ambiguous dates as dd/mm/yyyy (default mm/dd/yyyy) | Rows added to `vendors`/`invoices`; prints rows imported, skipped-row warnings, total |
| `python pipeline.py` | Audit every record and train the anomaly model | Replaces `detection_results` and `fuzzy_match_pairs`; writes `models/anomaly.joblib`; prints step timings, risk counts, and precision/recall if labels exist |
| `python -m streamlit run app.py` | Start the web app on port 8501 | — |
| `python -m forgery.dataset [--history CSV]` | Download/extract the receipt dataset (once); optionally write demo company history from genuine receipts with a readable receipt number | Files in `~/.cache/invoice-engine`; the CSV |
| `python -m forgery.train` | Train and evaluate the image-tamper model | Writes `models/tamper.joblib`; prints validation/test metrics |
| `python tests/test_core.py` | Smoke tests (also runs under `pytest`) | `ok test_…` lines; uses a temp database |

**Exit behaviour:** commands raise on unexpected errors (non-zero exit). The importer reports unusable rows as warnings
and still imports the rest; a file missing required columns imports nothing and lists the columns it found.

---

## 2. Python API

All functions take/return pandas DataFrames unless noted.

### Intake

| Function | Purpose |
|---|---|
| `intake.importer.read_table(file) -> DataFrame` | Read `.csv`, `.xlsx/.xlsm/.xls` from a path or file-like object with `.name` |
| `intake.importer.normalize(raw, dayfirst=False) -> (DataFrame, list[str])` | Map columns to the schema, coerce types, drop unusable rows; returns rows and human-readable problems |
| `intake.importer.resolve_vendor_ids(names, vendors) -> Series` | Vendor names → vendor IDs (existing or new slug) |
| `intake.importer.import_records(df, db, source="import") -> int` | Insert normalized rows and new vendors; returns rows inserted |
| `intake.ocr.extract(file_name, data) -> (text, image, tamper_checkable)` | Text from an image or PDF; `tamper_checkable` is True only for image uploads |
| `intake.ocr.ocr_image(img) -> str` | OCR a PIL image into reading-order lines |
| `intake.fields.parse_fields(text, dayfirst=True) -> dict` | `{vendor_name, invoice_id, invoice_date, amount, currency}`; any value may be `None` |

### Detection

| Function | Purpose |
|---|---|
| `detection.checker.check_invoices(new, db, model=None) -> (results, related)` | Score new invoices against history (see below) |
| `detection.checker.verdict(risk_category, tampered=False) -> str` | `"SUSPICIOUS"` / `"REVIEW"` / `"OK"` |
| `detection.checker.add_to_records(results, db, source="receipt-check") -> int` | Save checked invoices as records |
| `pipeline.run_audit(db_path=None, verbose=True) -> dict` | Flow A; returns `{"records", "near_duplicate_pairs", "HIGH", "MEDIUM", "LOW"}` (keys present only when non-zero) |
| `detection.ml_detector.MLDetector` | `fit(df)`, `score(df)`, `detect_anomalies(df)`, `save(path=None)`, `MLDetector.load(path=None)` (returns `None` if no file) |
| `forgery.model.tamper_score(img) -> (probability, flagged) \| None` | `None` when `models/tamper.joblib` is missing |

**`check_invoices` input** (`new`): columns `invoice_id`, `vendor_name`, `invoice_date` (ISO string or date), `amount`;
optional `currency` (default USD), `vendor_id` (resolved from the name if missing), `po_number`.

**`results`**: one row per input row, in input order:

| Column | Meaning |
|---|---|
| input columns + `id` | `id` is a temporary negative ID (−1, −2, …) |
| `final_score`, `risk_category` | 0–1 score; HIGH / MEDIUM / LOW |
| `verdict` | From risk only (tamper is applied by the caller: `verdict(risk, tampered)`) |
| `all_flags` | e.g. `exact_dup,ml_isolation_forest` or `none` |
| `reasons` | `"; "`-joined plain-language reasons |
| `rule_score`, `ml_score`, `amount_to_vendor_median` | Components |

**`related`**: `new_id`, `record_id` (negative = another row in the same batch), `similarity`, `match_type`
(`exact`, `cross_erp_id`, `vendor_variant`), `invoice_id`, `vendor_name`, `invoice_date`, `amount`.

**Example**

```python
import pandas as pd
from database.db_manager import DatabaseManager
from detection.checker import check_invoices

with DatabaseManager() as db:
    results, related = check_invoices(pd.DataFrame([{
        "invoice_id": "INV-0003", "vendor_name": "Acme Supplies Ltd",
        "invoice_date": "2024-01-22", "amount": 1030.00,
    }]), db)
print(results[["verdict", "final_score", "reasons"]])
```

### Database

`database.db_manager.DatabaseManager(db_path=None)`, usable as a context manager. Public methods: `init_db`,
`bulk_insert_vendors`, `bulk_insert_invoices`, `get_all_invoices`, `get_invoices_by_vendor`,
`get_invoices_by_date_range`, `count_invoices`, `write_detection_results`, `write_fuzzy_matches`,
`log_receipt_check`, `execute_query(sql, params=None)`, `close`. See [DATABASE.md](DATABASE.md).

---

## 3. Configuration

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `INVOICE_DB_PATH` | `<project>/data/invoices.db` | Database file location. Put it outside cloud-synced folders for real data |
| `INVOICE_DATA_DIR` | `~/.cache/invoice-engine` | Receipt dataset and feature cache |

No secrets, API keys or credentials are used anywhere.

### `config.py`

Every value is read by the code. Detection settings apply to new checks immediately and to stored results after the
next audit.

| Setting | Default | Effect |
|---|---|---|
| `BLOCKING_KEY_LENGTH` | 3 | Vendor-name prefix length for fuzzy-matching blocks (shorter = more comparisons) |
| `NEAR_DUP_AMOUNT_TOLERANCE` | 0.005 | Amount window for near-duplicate candidates (0.5% + 0.01) |
| `NEAR_DUP_DATE_WINDOW_DAYS` | 7 | Date window for near-duplicate candidates |
| `INVOICE_ID_MATCH_THRESHOLD` | 0.85 | Invoice-number similarity that alone confirms a same-vendor duplicate |
| `INVOICE_ID_SUPPORT_THRESHOLD` | 0.70 | Invoice-number similarity needed alongside a vendor-name match |
| `FUZZY_THRESHOLD` | 0.85 | Vendor-name similarity for a name-variant match |
| `VENDOR_NAME_STRICT_THRESHOLD` | 0.90 | Vendor-name similarity accepted across different vendor IDs |
| `LEVENSHTEIN_WEIGHT` / `JARO_WINKLER_WEIGHT` | 0.4 / 0.6 | Name-similarity blend |
| `OVERPAYMENT_MULTIPLIER` / `OVERPAYMENT_MIN_ZSCORE` | 3.0 / 3.5 | Overpayment rule: ≥ 3× vendor median and z ≥ 3.5 |
| `RAPID_FIRE_MIN_COUNT` | 3 | Burst rule: invoices on one vendor + PO + date |
| `ROUND_NUMBER_MIN_AMOUNT` / `ROUND_NUMBER_STEP` / `ROUND_NUMBER_MIN_MEDIAN_RATIO` | 25,000 / 5,000 / 2.5 | Round-number rule |
| `ISOLATION_FOREST_CONTAMINATION` | 0.01 | Share of history flagged "unusual" (flag + reason only; scores unaffected) |
| `ISOLATION_FOREST_N_ESTIMATORS` / `_RANDOM_STATE` | 200 / 42 | Forest size and seed |
| `ENSEMBLE_RULE_WEIGHT` / `ENSEMBLE_ML_WEIGHT` | 0.6 / 0.4 | Score blend |
| `STRONG_RULE_SCORE` / `STRONG_RULE_FLOOR` | 0.80 / 0.88 | A rule hit ≥ 0.80 lifts the final score to ≥ 0.88 |
| `RISK_HIGH_THRESHOLD` / `RISK_MEDIUM_THRESHOLD` | 0.7 / 0.4 | HIGH → SUSPICIOUS, MEDIUM → REVIEW |

Constants that live next to their code (change there): `VENDOR_MATCH_CUTOFF` (90) and `MIN_CONTAINED_NAME` (15) in
`intake/importer.py`; OCR/PDF settings in `intake/ocr.py`; tamper features in `forgery/model.py`.
