# Issue 1: Project cleanup and open problems

## Reported problems (fixed in 250f9e5)

- [x] **Overcomplicated:** 8 packages and 20 Python files were merged into one `engine/` package (7 modules). Removed `sys.path` hacks, stateless classes, unused DB methods and the unused `fuzzy_match_pairs` table
- [x] **No clear instructions for a company to upload its data:** README now has a step-by-step "Load your company's data" guide with a column table, plus `templates/company_records_template.csv` (also downloadable in the app)
- [x] **Messy app:** 3 tabs instead of 4, a guided Import → Audit flow, a "Start here" banner, and reasons shown before scores
- [x] **Too many files / messy structure:** one `cli.py` replaces `pipeline.py` and the `python -m ...` entry points
- [x] **Documentation not proper:** 8 docs files were replaced by the README and `docs/HOW_IT_WORKS.md`
- [x] **Leftovers from earlier sessions:** removed a hard-coded fake "40.0%" fallback metric, the unreproducible 98.6% synthetic benchmark claim, and change-history comments
- [x] **Models might be overtrained:** the tamper random forest memorized its training set (train AUC 0.999, test 0.73). It was replaced with a logistic regression (train 0.777, test 0.770); the threshold now comes from out-of-fold predictions; the audit no longer tunes its threshold on the labels it reports; the anomaly model is skipped below 50 records

## Follow-up problems

### Models and accuracy
- [x] **Tamper model was weak** (39% precision, 46% recall). Retrained on the dataset's marked forgery regions, block by block (gradient boosting on 57 forensic features per 32×32 block, score = most suspicious block). Test: ROC-AUC 0.770 → 0.909, precision 39% → 75%, recall 46% → 69%; CV 0.902 agrees with test, so it is not overfit. 9 of 10 on the answer-key receipts (was 8), and the genuine sample receipt is no longer flagged. The app outlines the suspicious area
- [ ] **Tamper model trained only on Malaysian retail receipts.** *Needs data:* forged examples of other invoice types. Documented in README
- [x] **Accuracy on real data is now measured:** `tests/benchmark.py`, 10-fold on the 377 real receipts, with realistic duplicate errors and overpayments applied to real records. Labelled company data would still be better (`is_anomaly` column)
- [x] Anomaly model double-counted near duplicates: `max_fuzzy_score` was removed from the model features (the rule already scores it)
- [x] Misleading feature names were renamed to `vendor_invoices_same_month` and `vendor_same_amount_count`. Models saved with old feature names are ignored until the next audit
- [x] **New, found while benchmarking:** sequential invoice numbers (`INV-0501` and `INV-0502` a week later, e.g. a recurring bill) were flagged as near duplicates: 4,750 false flags in 100K clean invoices. Numbers that differ only in their digits must now share the date (312 remain, all same-day). This removed one false positive on the real receipts (6 HIGH → 5)

### Accuracy (follow-up)
- [x] **Overpayment rule almost never fired**: it compared each invoice with a mean/std that included the invoice itself, which needs ~14+ invoices per vendor. Now uses the vendor's *other* invoices, a robust z-score on log amounts, and two tiers (SUSPICIOUS / REVIEW). Benchmark recall at 10×: 18% → 38% (49% for vendors with 10+ invoices); answer-key batch 16/20 → 18/20
- [x] **Vendor name variants**: registration numbers in brackets and legal forms (SDN BHD, S/B, BHD...) are now ignored when matching names. Name-variant duplicates caught: 77% → 99%; all-errors-combined: 75% → 99%
- [ ] **Trade-off accepted:** genuine retail receipts flagged rose from 0.8% to 3.8% (2.1% SUSPICIOUS) because the overpayment rule now actually works. Retail shops' amounts vary enormously; steady supplier invoices should see fewer false alarms. Tune with `OVERPAYMENT_*` in `engine/config.py` once labelled company data exists
- [ ] **Remaining answer-key misses**: 1 forged receipt scores 0.961 (threshold 0.978); 2 overpayments (3.7×, 7.9×) at shops whose own receipts span ~10× and ~90×

### Data handling
- [x] Exchange rates: an editable table in the app (Company records → Exchange rates, saved to `data/exchange_rates.csv`) with defaults for 12 currencies. A currency without a rate is now called out in the check's reasons. The currency picker lists only currencies that have a rate
- [x] Vendor spelling: a new vendor's result suggests the closest known vendor ("Did you mean ...?"), and the single-invoice form has an optional Vendor ID field
- [x] OCR speed: tuning RapidOCR (detection size, threads) and downscaling gave no measurable gain; the time is spent in the models themselves. Fixed the part that could be fixed: the 20–30 s engine load now happens in the background at app start instead of on the first upload
- [x] `payment_status` / `currency` defaults now apply: the importer only inserts columns the file has
- [x] Old databases: `db.connect` drops the leftover `fuzzy_match_pairs` table and the unused date/amount indexes

### Security and operations
- [x] Login: optional shared password (`INVOICE_APP_PASSWORD`, compared in constant time). Individual accounts and roles are not implemented
- [x] Encryption: documented setup for HTTPS (`--server.sslCertFile`) and disk encryption in the README ("Running it for a team"). Not built into the app
- [x] Backups: `python cli.py backup` (consistent while running, keeps the last 30) plus an automatic backup before every import from the app. Migrations are still schema-on-connect (`CREATE IF NOT EXISTS` and drops), which is enough for the current schema
- [x] CI: `.github/workflows/tests.yml` runs `tests/test_engine.py` on every push and pull request
- [x] LICENSE: MIT for the code. The research-only terms of the training dataset are noted in the README

### Performance
- [x] Audit scores the Isolation Forest once again (training scores are reused)
- [x] Re-measured on 100K records: 0.5 s per single check (the README's "~2.5 s" was outdated), 3.5 s for a batch of 50, 7.5 s for a full audit
