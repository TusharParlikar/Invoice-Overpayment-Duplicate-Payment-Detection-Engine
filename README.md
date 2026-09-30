# Invoice Overpayment & Duplicate Payment Detection Engine

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Precision](https://img.shields.io/badge/Precision-98.6%25-brightgreen.svg)]()
[![Recall](https://img.shields.io/badge/Recall-96.5%25-green.svg)]()
[![F1-Score](https://img.shields.io/badge/F1--Score-0.975-blue.svg)]()
[![False--Duplicate--Reduction](https://img.shields.io/badge/False--Duplicate--Reduction-59.6%25-orange.svg)]()
[![Records](https://img.shields.io/badge/Scale-100%2C000%2B%20Records-purple.svg)]()

> **Production-grade anomaly detection pipeline** combining **Isolation Forest machine learning**, **rule-based heuristics**, and **high-performance fuzzy matching (Levenshtein + Jaro-Winkler)** to identify duplicate and overpaid invoices across multi-ERP environments (SAP, Oracle, NetSuite).

---

## Key Highlights & Benchmarks

- **104,983 Invoices Audited**: Processed end-to-end in **under 45 seconds**.
- **98.6% Precision** (exceeds 91% requirement): High confidence detection with minimal false alarms (79 false positives out of 99,225 clean records).
- **96.5% Recall**: Detected **5,557 out of 5,758** injected anomalies across all fraud categories.
- **59.6% Reduction in False-Duplicate Flags**: Applied Levenshtein and Jaro-Winkler string similarity to reconcile vendor name variations and cross-ERP invoice ID formats, eliminating 2,470 false candidate matches.
- **Full Data Persistence**: SQLite relational database with WAL mode, indexing, and complete audit trail.

---

## System Architecture

```
                                  INVOICE DATA PIPELINE
                                  
   +-----------------------+     +------------------------+     +-----------------------+
   |   Synthetic Data      | --> |     SQLite Database    | --> |     Preprocessing     |
   | (100K+ ERP Records)   |     |    (WAL Mode + Index)  |     |   (Cleaning & USD)    |
   +-----------------------+     +------------------------+     +-----------------------+
                                                                            |
                                                                            v
   +-----------------------+     +------------------------+     +-----------------------+
   |  Ensemble Aggregator  | <-- |   Isolation Forest ML  | <-- |     Fuzzy Matcher     |
   |  (60% Rules + 40% ML) |     |  (Unsupervised Models) |     | (Levenshtein + J-W)   |
   +-----------------------+     +------------------------+     +-----------------------+
              |                                                             |
              v                                                             v
   +-----------------------+                                    +-----------------------+
   |  Evaluation & Metrics |                                    |  Feature Engineering  |
   | (Precision / Recall)  |                                    | (Z-scores, Frequency) |
   +-----------------------+                                    +-----------------------+
```

---

## Detection Modules

### 1. Fuzzy Matching Engine (`matching/fuzzy_matcher.py`)
- **Challenge**: Across disparate ERP systems (SAP, Oracle, NetSuite), identical vendors appear as *"Acme Corp"*, *"ACME CORPORATION"*, *"Acme Corp Inc"*, or *"acme corporation llc"*, and invoice IDs differ by dashes (`SAP-2024-123456` vs `SAP2024123456` vs `SAP-2024-123456-A`).
- **Solution**:
  - **Blocking Strategy**: Groups by 3-character prefix keys to reduce comparison complexity from $\mathcal{O}(N^2)$ to sparse candidate windows.
  - **Dual Metric Similarity**: Combines Levenshtein ratio ($40\%$) and Jaro-Winkler similarity ($60\%$) via `rapidfuzz`.
  - **Cross-ERP ID Normalization**: Strips delimiters and applies sequence alignment.
  - **False Duplicate Pruning**: Evaluated 4,142 candidate pairs, confirmed 1,672 true cross-ERP duplicates, and pruned 2,470 false matches (**59.6% reduction in false-duplicate flags**).

### 2. Rule-Based Engine (`detection/rule_engine.py`)
Five specialized domain rules:
- **Rule 1 — Exact Duplicate**: Identifies duplicate submissions with matching `(vendor_id, invoice_id, amount)`. Preserves first legitimate entry and flags subsequent copies.
- **Rule 2 — Cross-ERP Near Duplicate**: Surfaces pairs confirmed by fuzzy matching with similar amounts ($\pm 0.5\%$) and dates within 7 days.
- **Rule 3 — Overpayment Anomaly**: Flags transactions exceeding $3.0\times$ vendor historical median with Z-score $\ge 3.5$.
- **Rule 4 — Rapid-Fire Burst**: Flags burst submissions ($\ge 3$ invoices for the same vendor & PO on the same date).
- **Rule 5 — Suspicious Round Number**: Flags large round-number payments ($\ge \$25,000$, multiple of $\$5,000$) departing from vendor baseline.

### 3. Machine Learning Detector (`detection/ml_detector.py`)
- **Algorithm**: `IsolationForest` (scikit-learn) with 200 estimators and 6.5% contamination factor.
- **Feature Space**:
  - `amount_usd`: Currency-standardized invoice amount
  - `amount_zscore`: Per-vendor statistical standard deviation score
  - `amount_to_vendor_median`: Ratio against historical vendor median
  - `amount_to_vendor_max`: Ratio against historical vendor maximum
  - `vendor_invoice_count_30d`: Monthly cadence frequency
  - `days_since_last_invoice`: Inter-arrival submission time
  - `same_amount_count_30d`: Repetitive amount volume
  - `max_fuzzy_score`: Maximum fuzzy similarity score from candidate pairs

### 4. Ensemble Classifier (`detection/ensemble.py`)
- Blends rule scores ($60\%$) and unsupervised ML scores ($40\%$).
- High-confidence rule triggers (exact dup, near dup, statistical overpayment) receive confidence boosting ($\ge 0.88$).
- Structured categorical risk ratings:
  - **HIGH RISK** ($\ge 0.70$): Immediate audit / payment hold
  - **MEDIUM RISK** ($0.40 - 0.70$): Secondary review
  - **LOW RISK** ($< 0.40$): Standard automated approval

---

## Evaluation & Benchmark Results

Run on **104,983 invoices** with **5,758 injected ground-truth anomalies**:

```text
============================================================
  EVALUATION REPORT (Score Threshold: 0.50)
============================================================

--- Overall Detection Performance ---
  Precision: 0.9860  (98.6%)
  Recall:    0.9651  (96.5%)
  F1 Score:  0.9754

--- Breakdown by Anomaly Type ---
  Anomaly Category   | Precision  | Recall     | F1         | Count
  ----------------------------------------------------------
  exact_duplicate    | 0.952      | 1.000      | 0.976      | 1,575
  near_duplicate     | 0.952      | 0.999      | 0.975      | 1,575
  overpayment        | 0.946      | 0.873      | 0.908      | 1,575
  rapid_fire         | 0.929      | 1.000      | 0.963      | 1,033

--- Confusion Matrix ---
  True Negatives (TN) : 99,146 | False Positives (FP):     79
  False Negatives (FN):    201 | True Positives (TP) :  5,557

--- Summary ---
  Total Invoices Audited : 104,983
  Invoices Flagged Risk  : 5,636 (5.37% flag rate)
  True Anomalies Caught  : 5,557 of 5,758 (96.5%)
============================================================

============================================================
  FUZZY MATCHING IMPACT VS NAIVE BASELINE
============================================================
  Candidate pairs evaluated (amount & date proximity) : 4,142
  Pairs confirmed by fuzzy matching (Levenshtein + JW) : 1,672
  False-duplicate matches eliminated                   : 2,470
  False-Duplicate Flag Reduction                       : 59.6%
============================================================
```

### Execution Speed
| Pipeline Stage | Records Processed | Runtime |
|---|---|---|
| Synthetic Data Generation & DB Insert | 104,983 invoices, 2,500 vendors | 13.57s |
| Data Cleaning & Normalization | 104,983 invoices | 5.35s |
| Fuzzy Matching (Levenshtein + JW) | 4,142 candidate pairs | 4.60s |
| Vectorized Feature Engineering | 7 behavioral/statistical features | 0.47s |
| Rule Engine (5 Rules) | 104,983 invoices | 3.37s |
| ML Isolation Forest | 104,983 invoices | 9.62s |
| Ensemble Scoring & Risk Categorization | 104,983 invoices | 2.15s |
| Evaluation Metrics & Baseline Comparison | 104,983 invoices | 3.85s |
| SQLite Result Persistence | 104,983 detection results, 1,672 pairs | 0.91s |
| **Total End-to-End Pipeline** | **104,983 Invoices** | **43.89s** |

---

## Installation & Usage

### 1. Prerequisites
Python 3.10+ installed.

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

Required packages:
- `pandas >= 2.0.0`
- `numpy >= 1.24.0`
- `scikit-learn >= 1.3.0`
- `rapidfuzz >= 3.0.0`
- `Faker >= 19.0.0`

### 3. Run the Full Pipeline
```bash
python pipeline.py
```

### 4. Query the Database
The pipeline creates and populates `data/invoices.db` (SQLite):

```python
from database.db_manager import DatabaseManager

db = DatabaseManager()
# Get all high-risk flagged invoices
high_risk = db.execute_query("""
    SELECT i.invoice_id, i.vendor_name, i.amount, d.final_score, d.risk_category, d.flags
    FROM invoices i
    JOIN detection_results d ON i.id = d.invoice_row_id
    WHERE d.risk_category = 'HIGH'
    ORDER BY d.final_score DESC
""")
print(high_risk.head())
```

---

## Project Structure

```
projecttt/
├── config.py                      # Centralized configuration & hyperparameters
├── pipeline.py                    # End-to-end pipeline orchestrator
├── requirements.txt               # Project dependencies
├── README.md                      # Comprehensive documentation & benchmarks
├── database/
│   ├── schema.sql                 # Relational schema (vendors, invoices, results, fuzzy pairs)
│   └── db_manager.py              # SQLite connection manager, WAL mode, CRUD
├── data/
│   ├── generate_synthetic_data.py # 100K+ realistic invoices with injected anomalies
│   └── invoices.db                # SQLite database (generated at runtime)
├── preprocessing/
│   ├── cleaner.py                 # Currency normalization, vendor cleaning, date parsing
│   ├── data_loader.py             # Database query and ingestion helpers
│   └── feature_engineer.py        # Vectorized statistical & frequency feature engineering
├── matching/
│   └── fuzzy_matcher.py           # Levenshtein + Jaro-Winkler fuzzy reconciliation
├── detection/
│   ├── rule_engine.py             # 5 domain rules for duplicates & overpayments
│   ├── ml_detector.py             # Scikit-learn Isolation Forest model
│   └── ensemble.py                # 60/40 rule + ML ensemble with risk classification
└── evaluation/
    └── metrics.py                 # Precision, recall, F1, confusion matrix, baseline comparison
```

---

## Technologies Used

- **Language**: Python 3.10+
- **Data Manipulation**: Pandas, NumPy
- **Machine Learning**: Scikit-Learn (`IsolationForest`, `StandardScaler`)
- **String Similarity**: `rapidfuzz` (C++ optimized Levenshtein, Jaro-Winkler)
- **Synthetic Data**: `Faker`
- **Database**: SQLite 3 with WAL Mode & B-tree indexes
