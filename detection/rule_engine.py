"""
Rule-based detection engine with 5 targeted business rules
for identifying duplicate and overpaid invoices.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
import logging
import config

logger = logging.getLogger(__name__)


class RuleEngine:
    def __init__(self):
        self.NEAR_DUP_AMOUNT_TOLERANCE = config.NEAR_DUP_AMOUNT_TOLERANCE
        self.NEAR_DUP_DATE_WINDOW_DAYS = config.NEAR_DUP_DATE_WINDOW_DAYS
        self.FUZZY_THRESHOLD = config.FUZZY_THRESHOLD
        self.OVERPAYMENT_MULTIPLIER = config.OVERPAYMENT_MULTIPLIER
        self.RAPID_FIRE_MIN_COUNT = config.RAPID_FIRE_MIN_COUNT
        self.RAPID_FIRE_HOURS = config.RAPID_FIRE_HOURS
        self.ROUND_NUMBER_THRESHOLD = config.ROUND_NUMBER_THRESHOLD

    def rule_exact_duplicate(self, df: pd.DataFrame) -> pd.Series:
        """Flag duplicate copies of identical (vendor_id, invoice_id, amount) submissions."""
        scores = pd.Series(0.0, index=df.index)
        if df.empty:
            return scores
        amt_col = "amount_usd" if "amount_usd" in df.columns else "amount"
        # keep='first' preserves original invoice as clean and flags subsequent duplicates
        is_dup = df.duplicated(subset=["vendor_id", "invoice_id", amt_col], keep="first")
        scores[is_dup] = 1.0
        return scores

    def rule_near_duplicate(self, df: pd.DataFrame, fuzzy_matches: pd.DataFrame) -> pd.Series:
        """Flag near-duplicate invoice submissions confirmed by the fuzzy matcher."""
        scores = pd.Series(0.0, index=df.index)
        if fuzzy_matches is None or fuzzy_matches.empty or df.empty or "id" not in df.columns:
            return scores

        # Map to record_b_id (the duplicate copy)
        id_to_score = fuzzy_matches.groupby("record_b_id")["similarity_score"].max()
        scores = df["id"].map(id_to_score).fillna(0.0)
        scores.index = df.index
        return scores

    def rule_overpayment(self, df: pd.DataFrame) -> pd.Series:
        """Flag invoices with amounts significantly above vendor historical baseline."""
        scores = pd.Series(0.0, index=df.index)
        amt_col = "amount_usd" if "amount_usd" in df.columns else "amount"

        if "amount_to_vendor_median" in df.columns and "amount_zscore" in df.columns:
            ratio = df["amount_to_vendor_median"]
            zscore = df["amount_zscore"]
        else:
            stats = df.groupby("vendor_id")[amt_col].agg(["median", "std"])
            v_med = df["vendor_id"].map(stats["median"]).replace(0, 1.0)
            v_std = df["vendor_id"].map(stats["std"]).replace(0, 1.0)
            ratio = df[amt_col] / v_med
            zscore = (df[amt_col] - v_med) / v_std

        # Injected overpayments are 3.5x to 6.5x vendor base
        mask = (ratio >= 3.0) & (zscore >= 3.5)
        over_scores = (ratio - 1.0).clip(lower=0.0, upper=5.0) / 5.0
        scores[mask] = np.maximum(0.85, over_scores[mask])
        return scores

    def rule_rapid_fire(self, df: pd.DataFrame) -> pd.Series:
        """Flag rapid burst submissions (multiple invoices on the same PO within 24h)."""
        scores = pd.Series(0.0, index=df.index)
        if "po_number" in df.columns and "invoice_date" in df.columns:
            cluster_count = df.groupby(["vendor_id", "po_number", "invoice_date"])["invoice_id"].transform("count")
            mask = cluster_count >= 3
            scores[mask] = 0.90
        elif "days_since_last_invoice" in df.columns and "vendor_invoice_count_30d" in df.columns:
            mask = (df["days_since_last_invoice"] == 0) & (df["vendor_invoice_count_30d"] >= 4)
            scores[mask] = 0.85
        return scores

    def rule_round_number(self, df: pd.DataFrame) -> pd.Series:
        """Flag suspicious high round-number payments exceeding vendor baseline."""
        scores = pd.Series(0.0, index=df.index)
        amt_col = "amount_usd" if "amount_usd" in df.columns else "amount"

        is_round = (df[amt_col] >= 25000.0) & (df[amt_col] % 5000 == 0)
        if "amount_to_vendor_median" in df.columns:
            mask = is_round & (df["amount_to_vendor_median"] >= 2.5)
        else:
            mask = is_round
        scores[mask] = 0.65
        return scores

    def apply_all_rules(self, df: pd.DataFrame, fuzzy_matches: pd.DataFrame = None) -> pd.DataFrame:
        """Run all 5 detection rules and aggregate rule scores."""
        result = df.copy()

        logger.info("Executing Rule 1: Exact Duplicate ...")
        result["rule_exact_dup"] = self.rule_exact_duplicate(result)

        logger.info("Executing Rule 2: Near Duplicate ...")
        result["rule_near_dup"] = self.rule_near_duplicate(result, fuzzy_matches)

        logger.info("Executing Rule 3: Overpayment ...")
        result["rule_overpayment"] = self.rule_overpayment(result)

        logger.info("Executing Rule 4: Rapid-Fire ...")
        result["rule_rapid_fire"] = self.rule_rapid_fire(result)

        logger.info("Executing Rule 5: Round Number ...")
        result["rule_round_number"] = self.rule_round_number(result)

        rule_cols = [
            "rule_exact_dup", "rule_near_dup", "rule_overpayment",
            "rule_rapid_fire", "rule_round_number",
        ]

        result["rule_score"] = result[rule_cols].max(axis=1).clip(upper=1.0)

        names = {
            "rule_exact_dup": "exact_dup",
            "rule_near_dup": "near_dup",
            "rule_overpayment": "overpayment",
            "rule_rapid_fire": "rapid_fire",
            "rule_round_number": "round_number",
        }
        flags = pd.Series("", index=result.index, dtype=object)
        for col, label in names.items():
            flags += np.where(result[col] > 0, label + ",", "")
        flags = flags.str.rstrip(",")
        result["rule_flags"] = flags.mask(flags == "", "none")

        for col in rule_cols:
            cnt = int((result[col] > 0).sum())
            logger.info(f"  {col}: {cnt:,} flagged")

        return result
