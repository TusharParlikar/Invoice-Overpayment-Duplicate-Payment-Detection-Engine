"""
Every tunable value in one place. Detection changes apply to new checks immediately and to
stored audit results after the next audit (python cli.py audit).
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("INVOICE_DB_PATH", os.path.join(ROOT, "data", "invoices.db"))
ANOMALY_MODEL_PATH = os.path.join(ROOT, "models", "anomaly.joblib")
TAMPER_MODEL_PATH = os.path.join(ROOT, "models", "tamper.joblib")

BACKUP_DIR = os.path.join(ROOT, "data", "backups")

# USD per unit of each currency, used to compare amounts across currencies. These defaults are
# approximate; set your own in the app (Company records > Exchange rates), saved to EXCHANGE_RATES_PATH.
# A currency without a rate is compared as-is and the check says so.
EXCHANGE_RATES_PATH = os.path.join(ROOT, "data", "exchange_rates.csv")
USD_RATES = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27, "INR": 0.012, "MYR": 0.22, "CAD": 0.73, "AUD": 0.66,
             "JPY": 0.0067, "CNY": 0.14, "SGD": 0.74, "AED": 0.27, "CHF": 1.12}

# Near-duplicate matching
BLOCKING_KEY_LENGTH = 3               # only compare invoices whose cleaned vendor names share this many leading chars
NEAR_DUP_AMOUNT_TOLERANCE = 0.005     # candidate pair: amounts within 0.5% (+0.01)
NEAR_DUP_DATE_WINDOW_DAYS = 7         # candidate pair: dates within this many days
INVOICE_ID_MATCH_THRESHOLD = 0.85     # same vendor + invoice numbers this similar = duplicate
INVOICE_ID_SUPPORT_THRESHOLD = 0.70   # invoice-number similarity needed to back a vendor-name match
FUZZY_THRESHOLD = 0.85                # vendor-name similarity for a name-variant match (same vendor ID)
VENDOR_NAME_STRICT_THRESHOLD = 0.90   # vendor-name similarity accepted across different vendor IDs
LEVENSHTEIN_WEIGHT = 0.4              # name similarity = 40% Levenshtein + 60% Jaro-Winkler
JARO_WINKLER_WEIGHT = 0.6

# Rules
OVERPAYMENT_MIN_HISTORY = 3           # the vendor needs this many other invoices to judge an amount ...
OVERPAYMENT_MULTIPLIER = 3.0          # ... amount >= this x the median of the vendor's other invoices ...
OVERPAYMENT_MIN_ZSCORE = 3.5          # ... and this unusual for this vendor (robust z-score of log amounts)
OVERPAYMENT_REVIEW_MULTIPLIER = 2.0   # weaker evidence (this x median and ...
OVERPAYMENT_REVIEW_ZSCORE = 2.5       # ... this z-score) asks for a review instead of holding the payment
RAPID_FIRE_MIN_COUNT = 3              # this many invoices on one vendor + PO + date = burst
ROUND_NUMBER_MIN_AMOUNT = 25_000      # large round amount: at least this ...
ROUND_NUMBER_STEP = 5_000             # ... a multiple of this ...
ROUND_NUMBER_MIN_MEDIAN_RATIO = 2.5   # ... and this x the vendor's median

# Anomaly model (Isolation Forest, trained on the company's own records)
MIN_RECORDS_FOR_MODEL = 50            # fewer records than this: no model, checks use rules only
ISOLATION_FOREST_CONTAMINATION = 0.01 # share of history labelled "unusual" (flag + reason only, not the score)
ISOLATION_FOREST_N_ESTIMATORS = 200
ISOLATION_FOREST_RANDOM_STATE = 42

# Risk score = 60% rules + 40% anomaly model
ENSEMBLE_RULE_WEIGHT = 0.6
ENSEMBLE_ML_WEIGHT = 0.4
STRONG_RULE_SCORE = 0.80              # a rule hit at least this strong ...
STRONG_RULE_FLOOR = 0.88              # ... lifts the risk score to at least this
RISK_HIGH_THRESHOLD = 0.7             # HIGH  -> verdict SUSPICIOUS
RISK_MEDIUM_THRESHOLD = 0.4           # MEDIUM -> verdict REVIEW
