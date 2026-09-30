# Glossary

| Term | Meaning |
|---|---|
| **AP (accounts payable)** | The finance function that records and pays supplier invoices |
| **ERP** | Enterprise resource planning system (SAP, Oracle, NetSuite, ...) where invoices are recorded |
| **Invoice number / `invoice_id`** | The number printed on the vendor's invoice or receipt (not the database row ID) |
| **Record / `invoices.id`** | A row in the company's payment history; shown as "record #N" |
| **Vendor ID** | Stable vendor code from the ERP, or a slug the importer creates from the normalized name |
| **Normalized vendor name** | Lower case, punctuation and legal suffixes removed (`Acme Corp.` → `acme`) |
| **Exact duplicate** | Same vendor ID, invoice number and (USD) amount as an earlier record |
| **Near duplicate** | Same payment entered differently: reformatted number, name variant, date a few days apart |
| **Blocking / block** | Grouping invoices by the first 3 letters of the normalized vendor name so only plausible pairs are compared |
| **Levenshtein ratio** | String similarity based on the number of single-character edits |
| **Jaro-Winkler** | String similarity that rewards matching prefixes; good for names |
| **Overpayment** | Amount far above the vendor's usual: ≥ 3× median and z-score ≥ 3.5 |
| **Burst (rapid-fire)** | Several invoices from one vendor on the same PO and date |
| **z-score** | How many standard deviations a value is from the mean |
| **Isolation Forest** | Unsupervised anomaly model: random trees isolate unusual points in fewer splits |
| **Contamination** | Share of the training data the anomaly model labels as unusual (1% here) |
| **Ensemble** | Weighted blend of rule score (60%) and model score (40%) |
| **Risk category** | HIGH (≥ 0.70), MEDIUM (≥ 0.40), LOW |
| **Verdict** | What the user sees: SUSPICIOUS (HIGH), REVIEW (MEDIUM or tamper flag), OK |
| **Audit (flow A)** | Scoring every record and retraining the anomaly model (`pipeline.py`) |
| **Check (flow B)** | Scoring new invoices/receipts against history (`detection/checker.py`) |
| **Relevant history** | The records that can affect a check: same vendor or same fuzzy block |
| **OCR** | Optical character recognition: reading text from an image |
| **ELA (error-level analysis)** | Re-compressing an image and measuring the difference; edited regions respond differently |
| **Noise residual** | Image minus a smoothed version of itself; exposes texture/noise inconsistencies |
| **Tamper score** | Probability from the image model that a receipt image was edited |
| **ROC-AUC** | Probability a model ranks a random forged receipt above a random genuine one (0.5 = chance, 1 = perfect) |
| **Precision / recall** | Of the flags, how many were right / of the real problems, how many were flagged |
| **WAL** | SQLite write-ahead logging: readers keep working while one writer writes |
| ***Find it again!*** | Public research dataset of 988 scanned receipts, 163 forged (ICDAR 2023) |
| **SROIE** | Scanned receipts OCR and information extraction dataset the receipts come from |
