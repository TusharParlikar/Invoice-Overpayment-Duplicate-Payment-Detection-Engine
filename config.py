"""
Configuration for the Invoice Overpayment & Duplicate Payment Detection Engine.
Every value here is read by the code. Changes to detection settings take effect for new
checks immediately and for stored results after the next audit (python pipeline.py).
"""
import os

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("INVOICE_DB_PATH", os.path.join(PROJECT_ROOT, "data", "invoices.db"))

# ---------------------------------------------------------------------------
# Fuzzy matching (matching/fuzzy_matcher.py)
# ---------------------------------------------------------------------------
BLOCKING_KEY_LENGTH = 3               # compare invoices whose cleaned vendor names share this many leading chars
NEAR_DUP_AMOUNT_TOLERANCE = 0.005     # candidate pair: amounts within 0.5% (+0.01 absolute)
NEAR_DUP_DATE_WINDOW_DAYS = 7         # candidate pair: dates within this many days
INVOICE_ID_MATCH_THRESHOLD = 0.85     # same vendor + invoice numbers this similar = duplicate
INVOICE_ID_SUPPORT_THRESHOLD = 0.70   # invoice-number similarity needed to back a vendor-name match
FUZZY_THRESHOLD = 0.85                # vendor-name similarity for a name-variant match (same vendor ID)
VENDOR_NAME_STRICT_THRESHOLD = 0.90   # vendor-name similarity accepted across different vendor IDs
LEVENSHTEIN_WEIGHT = 0.4              # name similarity = 40% Levenshtein ratio
JARO_WINKLER_WEIGHT = 0.6             #                 + 60% Jaro-Winkler

# ---------------------------------------------------------------------------
# Business rules (detection/rule_engine.py)
# ---------------------------------------------------------------------------
OVERPAYMENT_MULTIPLIER = 3.0          # amount >= this x the vendor's median ...
OVERPAYMENT_MIN_ZSCORE = 3.5          # ... and this many standard deviations above the vendor's mean
RAPID_FIRE_MIN_COUNT = 3              # this many invoices on one vendor + PO + date = burst
ROUND_NUMBER_MIN_AMOUNT = 25_000      # "large round amount": at least this much ...
ROUND_NUMBER_STEP = 5_000             # ... a multiple of this ...
ROUND_NUMBER_MIN_MEDIAN_RATIO = 2.5   # ... and this x the vendor's median

# ---------------------------------------------------------------------------
# Anomaly model (detection/ml_detector.py)
# ---------------------------------------------------------------------------
# Share of the company's own history the model labels "unusual" (the ml_isolation_forest flag and the
# "unusual compared with past payments" reason). It does not change risk scores or verdicts: the
# ensemble uses the continuous anomaly score. 0.065 flagged 6.5% of every history, clean or not;
# "auto" (sklearn's paper cutoff) flagged 11.6% of the 105K benchmark, so a strict 1% is used.
ISOLATION_FOREST_CONTAMINATION = 0.01
ISOLATION_FOREST_N_ESTIMATORS = 200
ISOLATION_FOREST_RANDOM_STATE = 42

# ---------------------------------------------------------------------------
# Ensemble and risk (detection/ensemble.py)
# ---------------------------------------------------------------------------
ENSEMBLE_RULE_WEIGHT = 0.6
ENSEMBLE_ML_WEIGHT = 0.4
STRONG_RULE_SCORE = 0.80              # a rule hit at least this strong ...
STRONG_RULE_FLOOR = 0.88              # ... lifts the final score to at least this
RISK_HIGH_THRESHOLD = 0.7             # HIGH -> verdict SUSPICIOUS
RISK_MEDIUM_THRESHOLD = 0.4           # MEDIUM -> verdict REVIEW
