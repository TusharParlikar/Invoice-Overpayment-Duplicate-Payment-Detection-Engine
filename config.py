"""
Centralized configuration for the Invoice Overpayment & Duplicate Payment Detection Engine.
All thresholds, weights, file paths, and hyperparameters live here.
"""
import os

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(PROJECT_ROOT, "data", "invoices.db")

# ---------------------------------------------------------------------------
# Dataset Generation
# ---------------------------------------------------------------------------
NUM_RECORDS = 105_000
ANOMALY_RATE = 0.065
EXACT_DUP_RATE = 0.015
NEAR_DUP_RATE = 0.015
OVERPAYMENT_RATE = 0.015
RAPID_FIRE_RATE = 0.01

# ---------------------------------------------------------------------------
# Fuzzy Matching
# ---------------------------------------------------------------------------
FUZZY_THRESHOLD = 0.85
LEVENSHTEIN_WEIGHT = 0.4
JARO_WINKLER_WEIGHT = 0.6
BLOCKING_KEY_LENGTH = 3

# ---------------------------------------------------------------------------
# Isolation Forest (ML)
# ---------------------------------------------------------------------------
ISOLATION_FOREST_CONTAMINATION = 0.065
ISOLATION_FOREST_N_ESTIMATORS = 200
ISOLATION_FOREST_RANDOM_STATE = 42

# ---------------------------------------------------------------------------
# Ensemble
# ---------------------------------------------------------------------------
ENSEMBLE_ML_WEIGHT = 0.4
ENSEMBLE_RULE_WEIGHT = 0.6

# ---------------------------------------------------------------------------
# Risk Thresholds
# ---------------------------------------------------------------------------
RISK_HIGH_THRESHOLD = 0.7
RISK_MEDIUM_THRESHOLD = 0.4

# ---------------------------------------------------------------------------
# Detection Rule Thresholds
# ---------------------------------------------------------------------------
OVERPAYMENT_MULTIPLIER = 2.0
RAPID_FIRE_HOURS = 48
RAPID_FIRE_MIN_COUNT = 3
ROUND_NUMBER_THRESHOLD = 10_000
NEAR_DUP_DATE_WINDOW_DAYS = 7
NEAR_DUP_AMOUNT_TOLERANCE = 0.01

# ---------------------------------------------------------------------------
# Feature Engineering
# ---------------------------------------------------------------------------
ROLLING_WINDOW_DAYS = 30
ROLLING_MEDIAN_MONTHS = 6

# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------
RANDOM_SEED = 42
