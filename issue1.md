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
- [ ] **Tamper model is weak** (39% precision, 46% recall on test) and flags a genuine sample receipt. *Cannot be fixed in code alone.* An arithmetic content check (CASH − CHANGE = TOTAL) was tested: only 37% of receipts can be checked, and its flags would be right about 35% of the time, no better than the image model, so it was not added. Needs more forged training data. Mitigation in place: a tamper flag can only produce REVIEW
- [ ] **Tamper model trained only on Malaysian retail receipts.** *Needs data:* forged examples of other invoice types. Documented in README
- [ ] **No accuracy figure for the rules on real data.** *Needs data:* a labelled sample. Import it with an `is_anomaly` column and `python cli.py audit` reports precision and recall automatically
- [x] Anomaly model double-counted near duplicates: `max_fuzzy_score` was removed from the model features (the rule already scores it)
- [x] Misleading feature names were renamed to `vendor_invoices_same_month` and `vendor_same_amount_count`. Models saved with old feature names are ignored until the next audit
- [x] **New, found while benchmarking:** sequential invoice numbers (`INV-0501` and `INV-0502` a week later, e.g. a recurring bill) were flagged as near duplicates: 4,750 false flags in 100K clean invoices. Numbers that differ only in their digits must now share the date (312 remain, all same-day). This removed one false positive on the real receipts (6 HIGH → 5)

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
