"""
Invoice Overpayment & Duplicate Payment Detection Engine
Audit every invoice in the database and (re)train the anomaly model.

    python -m intake.importer invoices.xlsx   # 1. load company records (CSV/Excel)
    python pipeline.py                        # 2. audit all records + train the model
    streamlit run app.py                      # 3. check new receipts in the web app
"""
import os
import sys
import time
import logging

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from database.db_manager import DatabaseManager
from preprocessing.cleaner import clean_invoice_data
from preprocessing.feature_engineer import engineer_features
from matching.fuzzy_matcher import FuzzyMatcher
from detection.rule_engine import RuleEngine
from detection.ml_detector import MLDetector
from detection.ensemble import EnsembleDetector
from evaluation.metrics import Evaluator


def run_audit(db_path: str = None, verbose: bool = True) -> dict:
    """Score all records, save results + the trained model. Returns a summary dict."""
    say = print if verbose else (lambda *a, **k: None)
    logger = logging.getLogger(__name__)
    db_path = db_path or config.DB_PATH

    say("\n" + "=" * 70)
    say("  Invoice Overpayment & Duplicate Payment Detection Engine: audit")
    say("=" * 70 + "\n")
    pipeline_start = time.time()

    # ── Step 1 - Load records ──────────────────────────────────────────
    t0 = time.time()
    logger.info("Step 1 - Loading records ...")
    with DatabaseManager(db_path) as db:
        db.init_db()
        df = db.get_all_invoices()
    if df.empty:
        say("  No records yet. Import some first:  python -m intake.importer your_invoices.xlsx\n")
        return {"records": 0}
    say(f"  [OK] Loaded {len(df):,} invoices from {df['vendor_id'].nunique():,} vendors")
    say(f"    Time: {time.time() - t0:.2f}s\n")

    # ── Step 2 - Preprocess ────────────────────────────────────────────
    t0 = time.time()
    logger.info("Step 2 - Preprocessing ...")
    df = clean_invoice_data(df)
    say(f"  [OK] Cleaned {len(df):,} records")
    say(f"    Time: {time.time() - t0:.2f}s\n")

    # ── Step 3 - Fuzzy Matching ────────────────────────────────────────
    t0 = time.time()
    logger.info("Step 3 - Fuzzy matching (Levenshtein + Jaro-Winkler) ...")
    matcher = FuzzyMatcher()
    fuzzy_matches, fuzzy_scores = matcher.find_potential_duplicates(df)
    say(f"  [OK] Evaluated {matcher.candidate_count:,} candidate pairs; confirmed {len(fuzzy_matches):,} near-duplicates")
    say(f"    Time: {time.time() - t0:.2f}s\n")

    # ── Step 4 - Feature Engineering ───────────────────────────────────
    t0 = time.time()
    logger.info("Step 4 - Feature engineering ...")
    n_cols_before = len(df.columns)
    df = engineer_features(df, fuzzy_scores)
    say(f"  [OK] Engineered {len(df.columns) - n_cols_before} statistical and behavioral features")
    say(f"    Time: {time.time() - t0:.2f}s\n")

    # ── Step 5 - Rule-Based Detection ──────────────────────────────────
    t0 = time.time()
    logger.info("Step 5 - Rule-based detection (5 rules) ...")
    df = RuleEngine().apply_all_rules(df, fuzzy_matches)
    say(f"  [OK] Rule engine flagged {int((df['rule_score'] > 0).sum()):,} invoices")
    say(f"    Time: {time.time() - t0:.2f}s\n")

    # ── Step 6 - Train + score the anomaly model (Isolation Forest) ────
    t0 = time.time()
    logger.info("Step 6 - Training Isolation Forest on company history ...")
    ml_detector = MLDetector()
    df = ml_detector.detect_anomalies(df)
    ml_detector.save()
    say(f"  [OK] Isolation Forest trained on {len(df):,} records, flagged {int((df['ml_prediction'] == -1).sum()):,}; saved to models/")
    say(f"    Time: {time.time() - t0:.2f}s\n")

    # ── Step 7 - Ensemble Scoring ──────────────────────────────────────
    t0 = time.time()
    logger.info("Step 7 - Ensemble scoring ...")
    df = EnsembleDetector().run_ensemble(df)
    risk_counts = df["risk_category"].value_counts()
    say("  [OK] Risk category distribution:")
    for cat, cnt in risk_counts.items():
        say(f"      {cat:8s}: {cnt:,}")
    say(f"    Time: {time.time() - t0:.2f}s\n")

    # ── Step 8 - Evaluation (only when records carry is_anomaly labels) ─
    labelled = df[df["is_anomaly"].notna()] if "is_anomaly" in df.columns else df.iloc[0:0]
    if len(labelled) and labelled["is_anomaly"].nunique() > 1 and verbose:
        t0 = time.time()
        logger.info("Step 8 - Evaluation on labelled records ...")
        evaluator = Evaluator()
        evaluator.print_report(labelled)
        evaluator.compare_with_baseline(labelled, matcher.candidate_count, len(fuzzy_matches))
        say(f"    Time: {time.time() - t0:.2f}s\n")

    # ── Step 9 - Save Results ──────────────────────────────────────────
    t0 = time.time()
    logger.info("Step 9 - Saving results to SQLite ...")
    results_df = df[["id", "rule_score", "ml_score", "final_score", "risk_category", "all_flags"]].rename(
        columns={"id": "invoice_row_id", "all_flags": "flags"})
    with DatabaseManager(db_path) as db:
        db.write_detection_results(results_df)
        db.write_fuzzy_matches(fuzzy_matches)
    say(f"  [OK] Results saved to {db_path}")
    say(f"    Time: {time.time() - t0:.2f}s\n")

    say("=" * 70)
    say(f"  Audit completed in {time.time() - pipeline_start:.2f}s")
    say("=" * 70 + "\n")
    return {"records": len(df), "near_duplicate_pairs": len(fuzzy_matches), **risk_counts.to_dict()}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    run_audit()
