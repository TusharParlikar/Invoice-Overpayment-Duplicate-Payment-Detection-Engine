# Architecture decision records

Short ADRs for the choices that shape the codebase. Status is **Accepted** unless noted.

---

## ADR-1: Rules first, model second (60/40 ensemble)

- **Context.** Finance teams must be able to justify holding a payment. Duplicate and overpayment have concrete
  definitions; "unusual" does not.
- **Options.** (a) One ML model end to end; (b) rules only; (c) rules + unsupervised model, blended.
- **Decision.** (c): five rules and an Isolation Forest, `final = 0.6·rules + 0.4·model`, strong rule hits floored at
  0.88, HIGH ≥ 0.70.
- **Rationale.** Rules produce explainable, auditable reasons; the model covers patterns nobody wrote a rule for.
  The weights mean the model alone can reach REVIEW but never SUSPICIOUS.
- **Trade-offs.** Rules need tuning per company; the 98.6% benchmark precision reflects rules tested on data built
  alongside them (optimistic).

## ADR-2: Train the anomaly model per company, at audit time

- **Context.** What's normal differs by company and vendor; there are no labels.
- **Options.** Ship a pre-trained model; train per company.
- **Decision.** `pipeline.py` fits an Isolation Forest on the company's records and saves it; checks only score.
- **Consequences.** Model is stale until the next audit; new vendors look unusual; no data leaves the company.

## ADR-3: Flag 1% as "unusual", not 6.5% or "auto" (2026-10)

- **Context.** With `contamination=0.065` the model labelled 6.5% of any history "unusual", so the reason appeared on
  many normal invoices. sklearn's `"auto"` cutoff flagged 11.6% of the 105K benchmark.
- **Decision.** `ISOLATION_FOREST_CONTAMINATION = 0.01`, and the reason says "among the most unusual 1% of past
  payments".
- **Consequences.** Verified: scores, risk categories and rule flags identical on the benchmark; model flags
  6,824 → 1,050 (demo data: 42 → 4). Verdicts unaffected because the ensemble uses the continuous score.

## ADR-4: Check new invoices against relevant history only

- **Context.** A check loaded and processed the whole history (~8.7 s at 105K records).
- **Decision.** Load only rows with the same vendor ID or in the same fuzzy-matching block (`_relevant_history`),
  indexed on `vendor_id` and `vendor_name`.
- **Rationale.** Every feature and rule is per-vendor or per-block, so results are provably identical (verified row
  by row) at ~2.5 s.

## ADR-5: SQLite now, Postgres later

- **Context.** First users run it on one machine; zero setup and local data matter most.
- **Decision.** SQLite file, WAL mode, one connection per browser session.
- **Trade-offs.** Single writer; data lost on ephemeral cloud disks. SQL is confined to `database/db_manager.py` and
  one checker query to keep the Postgres move small. See [DATABASE.md §13](DATABASE.md#13-why-sqlite-trade-offs).

## ADR-6: Local OCR (RapidOCR), not Tesseract or a cloud/LLM service

- **Options.** Tesseract (system binary), EasyOCR/PaddleOCR (PyTorch/Paddle runtimes), cloud OCR / LLM vision.
- **Decision.** RapidOCR on ONNX Runtime.
- **Rationale.** Pip-only install on every OS, CPU-friendly, accurate on printed receipts, and receipts never leave the
  machine. **Trade-off:** ~7–15 s per photo on CPU; OCR mistakes are handled by a review form, not by the model.

## ADR-7: Classical image forensics for tamper detection

- **Options.** CNN / document-forgery deep models; classical features + small classifier.
- **Decision.** ELA + noise-residual block statistics (17 features) with a random forest.
- **Rationale.** 988 labelled receipts (163 forged) is little data for deep models; classical features are fast,
  CPU-only and inspectable.
- **Consequences.** Test ROC-AUC 0.735 (recall 40%, precision 37%). Regularized models and logistic regression did not
  generalize better, so the ceiling is data/features. Hence ADR-8.

## ADR-8: A tamper flag only asks for review; PDFs are not tamper-scored

- **Context.** The tamper model is right about 1 in 3 times it flags. PDF rendering resamples pixels and produced false
  flags (4% as image vs 33% as PDF for the same untouched receipt).
- **Decision.** Tamper flag → REVIEW, never SUSPICIOUS; tamper scoring only for uploaded image files.

## ADR-9: Streamlit UI, no HTTP API yet

- **Options.** FastAPI + React; Gradio; Streamlit.
- **Decision.** Streamlit, with all logic in importable functions (`check_invoices`, `run_audit`).
- **Trade-offs.** Fast to build, free hosting option, but no API for other systems and limited control over UX/auth.
  An API is a thin wrapper around `check_invoices` when needed.

## ADR-10: Remove the synthetic data generator (2026-10)

- **Context.** The generator produced the 105K benchmark but made the product look tested on reality.
- **Decision.** Company history comes from real exports; demo history from real receipts (`forgery.dataset`).
- **Consequences.** The benchmark numbers are kept, clearly labelled as synthetic and optimistic; they can no longer be
  regenerated from the repository.

## ADR-11: No `UNIQUE(vendor_id, invoice_id)` on invoices

- **Decision.** Allow duplicate rows.
- **Rationale.** Detecting invoices recorded twice is the product; a uniqueness constraint would reject exactly the
  evidence the audit needs.
