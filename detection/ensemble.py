"""
Ensemble module combining rule-based heuristics and Isolation Forest scores.
Assigns calibrated risk categories and aggregates detection flags.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
import logging
import config

logger = logging.getLogger(__name__)


class EnsembleDetector:
    def __init__(self):
        self.ENSEMBLE_ML_WEIGHT = config.ENSEMBLE_ML_WEIGHT
        self.ENSEMBLE_RULE_WEIGHT = config.ENSEMBLE_RULE_WEIGHT
        self.RISK_HIGH_THRESHOLD = config.RISK_HIGH_THRESHOLD
        self.RISK_MEDIUM_THRESHOLD = config.RISK_MEDIUM_THRESHOLD

    def combine_scores(self, df: pd.DataFrame) -> pd.DataFrame:
        """Combine rule score and ML score with confidence boosting for strong rule hits."""
        result_df = df.copy()

        rule_score = result_df["rule_score"] if "rule_score" in result_df.columns else 0.0
        ml_score = result_df["ml_score"] if "ml_score" in result_df.columns else 0.0

        final_score = (self.ENSEMBLE_RULE_WEIGHT * rule_score) + (self.ENSEMBLE_ML_WEIGHT * ml_score)

        # High-confidence rule triggers (exact dup, near dup, confirmed overpayment)
        # receive high confidence in the final score
        high_rule = rule_score >= config.STRONG_RULE_SCORE
        final_score = np.where(high_rule, np.maximum(final_score, config.STRONG_RULE_FLOOR), final_score)

        result_df["final_score"] = final_score
        return result_df

    def classify_risk(self, df: pd.DataFrame) -> pd.DataFrame:
        """Assign categorical risk tiers: HIGH, MEDIUM, LOW."""
        result_df = df.copy()
        if "final_score" not in result_df.columns:
            result_df["final_score"] = 0.0

        conditions = [
            result_df["final_score"] >= self.RISK_HIGH_THRESHOLD,
            result_df["final_score"] >= self.RISK_MEDIUM_THRESHOLD,
        ]
        choices = ["HIGH", "MEDIUM"]

        result_df["risk_category"] = np.select(conditions, choices, default="LOW")
        return result_df

    def generate_flags(self, df: pd.DataFrame) -> pd.DataFrame:
        """Combine rule flags and ML anomaly predictions into comprehensive audit tag."""
        result_df = df.copy()

        idx = result_df.index
        rule = result_df["rule_flags"].astype(str) if "rule_flags" in result_df.columns else pd.Series("none", index=idx)
        rule = rule.mask(rule == "none", "")
        ml = result_df["ml_prediction"] == -1 if "ml_prediction" in result_df.columns else pd.Series(False, index=idx)

        flags = rule.mask(ml, (rule + ",ml_isolation_forest").str.lstrip(","))
        result_df["all_flags"] = flags.mask(flags == "", "none")
        return result_df

    def run_ensemble(self, df: pd.DataFrame) -> pd.DataFrame:
        """Execute full ensemble pipeline: combination, risk classification, flag aggregation."""
        df_scored = self.combine_scores(df)
        df_classified = self.classify_risk(df_scored)
        df_flagged = self.generate_flags(df_classified)

        logger.info(f"Ensemble risk categories counts:\n{df_flagged['risk_category'].value_counts().to_string()}")
        return df_flagged
