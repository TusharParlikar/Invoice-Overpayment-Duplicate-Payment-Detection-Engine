"""
Invoice Overpayment & Duplicate Payment Detection Engine
End-to-end pipeline orchestration.
"""
import os
import sys
import time
import logging

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import config
from database.db_manager import DatabaseManager
from data.generate_synthetic_data import generate_all_data
from preprocessing.cleaner import clean_invoice_data
from preprocessing.feature_engineer import engineer_features
from matching.fuzzy_matcher import FuzzyMatcher
from detection.rule_engine import RuleEngine
from detection.ml_detector import MLDetector
from detection.ensemble import EnsembleDetector
from evaluation.metrics import Evaluator


def run_pipeline():
    """Run the full anomaly-detection pipeline end-to-end."""

    # ── logging ────────────────────────────────────────────────────────
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    logger = logging.getLogger(__name__)

    print("\n" + "=" * 70)
    print("  Invoice Overpayment & Duplicate Payment Detection Engine")
    print("=" * 70 + "\n")

    pipeline_start = time.time()

    # ── Step 1 - Generate Synthetic Data ───────────────────────────────
    t0 = time.time()
    logger.info("Step 1 - Generating synthetic data ...")
    df_vendors, df_invoices = generate_all_data()

    # Persist to SQLite
    db = DatabaseManager(config.DB_PATH)
    db.init_db()
    db.bulk_insert_vendors(df_vendors)
    db.bulk_insert_invoices(df_invoices)

    # Reload from DB so we have the auto-incremented `id` column
    df = db.get_all_invoices()
    db.close()

    anomaly_count = int(df["is_anomaly"].sum())
    pct = anomaly_count / len(df) * 100
    print(f"  [OK] Generated {len(df):,} invoices with {anomaly_count:,} anomalies ({pct:.1f}%)")
    print(f"    Time: {time.time() - t0:.2f}s\n")

    # ── Step 2 - Preprocess ────────────────────────────────────────────
    t0 = time.time()
    logger.info("Step 2 - Preprocessing ...")
    df = clean_invoice_data(df)
    print(f"  [OK] Cleaned {len(df):,} records")
    print(f"    Time: {time.time() - t0:.2f}s\n")

    # ── Step 3 - Fuzzy Matching ────────────────────────────────────────
    t0 = time.time()
    logger.info("Step 3 - Fuzzy matching (Levenshtein + Jaro-Winkler) ...")
    matcher = FuzzyMatcher()
    fuzzy_matches, fuzzy_scores = matcher.find_potential_duplicates(df)
    print(f"  [OK] Evaluated {matcher.candidate_count:,} candidate pairs; confirmed {len(fuzzy_matches):,} near-duplicates")
    print(f"    Time: {time.time() - t0:.2f}s\n")

    # ── Step 4 - Feature Engineering ───────────────────────────────────
    t0 = time.time()
    logger.info("Step 4 - Feature engineering ...")
    n_cols_before = len(df.columns)
    df = engineer_features(df, fuzzy_scores)
    n_new = len(df.columns) - n_cols_before
    print(f"  [OK] Engineered {n_new} statistical and behavioral features")
    print(f"    Time: {time.time() - t0:.2f}s\n")

    # ── Step 5 - Rule-Based Detection ──────────────────────────────────
    t0 = time.time()
    logger.info("Step 5 - Rule-based detection (5 rules) ...")
    rule_engine = RuleEngine()
    df = rule_engine.apply_all_rules(df, fuzzy_matches)
    rule_flagged = int((df["rule_score"] > 0).sum())
    print(f"  [OK] Rule engine flagged {rule_flagged:,} invoices")
    print(f"    Time: {time.time() - t0:.2f}s\n")

    # ── Step 6 - ML Detection (Isolation Forest) ──────────────────────
    t0 = time.time()
    logger.info("Step 6 - Isolation Forest unsupervised anomaly detection ...")
    ml_detector = MLDetector()
    df = ml_detector.detect_anomalies(df)
    ml_flagged = int((df["ml_prediction"] == -1).sum())
    print(f"  [OK] Isolation Forest flagged {ml_flagged:,} anomalies")
    print(f"    Time: {time.time() - t0:.2f}s\n")

    # ── Step 7 - Ensemble Scoring ──────────────────────────────────────
    t0 = time.time()
    logger.info("Step 7 - Ensemble scoring ...")
    ensemble = EnsembleDetector()
    df = ensemble.run_ensemble(df)
    print("  [OK] Risk category distribution:")
    for cat, cnt in df["risk_category"].value_counts().items():
        print(f"      {cat:8s}: {cnt:,}")
    print(f"    Time: {time.time() - t0:.2f}s\n")

    # ── Step 8 - Evaluation ────────────────────────────────────────────
    t0 = time.time()
    logger.info("Step 8 - Evaluation ...")
    evaluator = Evaluator()
    evaluator.print_report(df)
    evaluator.compare_with_baseline(df, matcher.candidate_count, len(fuzzy_matches))
    print(f"    Time: {time.time() - t0:.2f}s\n")

    # ── Step 9 - Save Results ──────────────────────────────────────────
    t0 = time.time()
    logger.info("Step 9 - Saving results to SQLite ...")
    db = DatabaseManager(config.DB_PATH)
    result_cols = ["id", "rule_score", "ml_score", "final_score", "risk_category", "all_flags"]
    available = [c for c in result_cols if c in df.columns]
    results_df = df[available].copy()
    results_df.rename(columns={"id": "invoice_row_id", "all_flags": "flags"}, inplace=True)
    db.write_detection_results(results_df)

    if not fuzzy_matches.empty:
        db.write_fuzzy_matches(fuzzy_matches)

    db.close()
    print(f"  [OK] Results saved to {config.DB_PATH}")
    print(f"    Time: {time.time() - t0:.2f}s\n")

    # ── Done ───────────────────────────────────────────────────────────
    total = time.time() - pipeline_start
    print("=" * 70)
    print(f"  Pipeline completed in {total:.2f}s")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    run_pipeline()
