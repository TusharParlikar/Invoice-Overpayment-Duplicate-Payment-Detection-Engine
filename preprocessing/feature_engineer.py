"""
Feature engineering for the ML anomaly-detection model.
Computes per-vendor statistical, frequency, and duplicate-risk features.
"""
import sys
import os
import pandas as pd
import numpy as np
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)


def compute_amount_features(df: pd.DataFrame) -> pd.DataFrame:
    """Compute per-vendor z-scores and ratio features."""
    df = df.copy()
    amt_col = "amount_usd" if "amount_usd" in df.columns else "amount"

    stats = df.groupby("vendor_id")[amt_col].agg(["mean", "std", "median", "max"])
    stats.columns = ["v_mean", "v_std", "v_median", "v_max"]

    df = df.merge(stats, on="vendor_id", how="left")

    v_std_safe = df["v_std"].replace(0, 1.0).fillna(1.0)
    v_med_safe = df["v_median"].replace(0, 1.0).fillna(1.0)
    v_max_safe = df["v_max"].replace(0, 1.0).fillna(1.0)

    df["amount_zscore"] = (df[amt_col] - df["v_mean"]) / v_std_safe
    df["amount_to_vendor_median"] = df[amt_col] / v_med_safe
    df["amount_to_vendor_max"] = df[amt_col] / v_max_safe

    df.drop(columns=["v_mean", "v_std", "v_median", "v_max"], inplace=True)
    return df


def compute_frequency_features(df: pd.DataFrame) -> pd.DataFrame:
    """Compute per-vendor invoice frequency and time-gap features."""
    df = df.copy()
    df["invoice_date"] = pd.to_datetime(df["invoice_date"])
    df = df.sort_values(["vendor_id", "invoice_date"])

    # Days since last invoice
    prev_dates = df.groupby("vendor_id")["invoice_date"].shift(1)
    df["days_since_last_invoice"] = (df["invoice_date"] - prev_dates).dt.days.fillna(999.0)

    # 30-day invoice count per vendor
    # Fast vectorized approximation: count occurrences per (vendor_id, year_month)
    df["ym"] = df["invoice_date"].dt.to_period("M")
    monthly_counts = df.groupby(["vendor_id", "ym"])["invoice_id"].transform("count")
    df["vendor_invoice_count_30d"] = monthly_counts.astype(float)
    df.drop(columns=["ym"], inplace=True)

    return df


def compute_duplicate_risk_features(df: pd.DataFrame) -> pd.DataFrame:
    """Count duplicate or near-duplicate amounts per vendor."""
    df = df.copy()
    amt_col = "amount_usd" if "amount_usd" in df.columns else "amount"

    df["_rounded_amt"] = df[amt_col].round(1)
    same_amt = df.groupby(["vendor_id", "_rounded_amt"])["invoice_id"].transform("count")
    df["same_amount_count_30d"] = same_amt.astype(float)
    df.drop(columns=["_rounded_amt"], inplace=True)

    return df


def add_fuzzy_match_features(df: pd.DataFrame, fuzzy_scores: dict) -> pd.DataFrame:
    """Map maximum fuzzy similarity score to each invoice."""
    df = df.copy()
    if fuzzy_scores and "id" in df.columns:
        df["max_fuzzy_score"] = df["id"].map(fuzzy_scores).fillna(0.0).astype(float)
    else:
        df["max_fuzzy_score"] = 0.0
    return df


def engineer_features(df: pd.DataFrame, fuzzy_scores: dict = None) -> pd.DataFrame:
    """Run complete feature engineering pipeline."""
    logger.info("Engineering features ...")
    df = compute_amount_features(df)
    df = compute_frequency_features(df)
    df = compute_duplicate_risk_features(df)
    df = add_fuzzy_match_features(df, fuzzy_scores)

    numeric_cols = [
        "amount_zscore", "amount_to_vendor_median", "amount_to_vendor_max",
        "vendor_invoice_count_30d", "days_since_last_invoice",
        "same_amount_count_30d", "max_fuzzy_score",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = df[col].fillna(0.0)

    # Sort back to original index order
    df = df.sort_index()
    return df
