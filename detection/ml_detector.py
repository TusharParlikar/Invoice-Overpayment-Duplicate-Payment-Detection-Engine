import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
import joblib
import logging
import config

logger = logging.getLogger(__name__)

MODEL_PATH = os.path.join(config.PROJECT_ROOT, "models", "anomaly.joblib")

class MLDetector:
    def __init__(self):
        self.n_estimators = config.ISOLATION_FOREST_N_ESTIMATORS
        self.contamination = config.ISOLATION_FOREST_CONTAMINATION
        self.random_state = config.ISOLATION_FOREST_RANDOM_STATE
        
        # contamination="auto" skips the scoring pass fit() would run to place its cutoff;
        # detect_anomalies applies self.contamination itself from a single score_samples pass.
        self.model = IsolationForest(
            n_estimators=self.n_estimators,
            contamination="auto",
            random_state=self.random_state
        )
        self.scaler = StandardScaler()
        
        self.FEATURE_COLUMNS = [
            'amount_usd', 'amount_zscore', 'amount_to_vendor_median', 
            'amount_to_vendor_max', 'vendor_invoice_count_30d', 
            'days_since_last_invoice', 'same_amount_count_30d', 'max_fuzzy_score'
        ]

    def _feature_frame(self, df: pd.DataFrame) -> pd.DataFrame:
        features_df = pd.DataFrame(index=df.index)
        for col in self.FEATURE_COLUMNS:
            if col in df.columns:
                features_df[col] = df[col]
            else:
                features_df[col] = 0.0
        return features_df.fillna(0.0)

    def prepare_features(self, df: pd.DataFrame) -> np.ndarray:
        return self.scaler.fit_transform(self._feature_frame(df))

    def fit(self, df: pd.DataFrame) -> np.ndarray:
        """Train scaler + forest on df (the company's history). Returns df's raw scores (< 0 = anomaly)."""
        X = self.prepare_features(df)
        # Identical to fit(contamination=c) + decision_function + predict, but scores the 200 trees once instead of 3x
        self.model.fit(X)
        samples = self.model.score_samples(X)
        # A number flags that share of the history as unusual; "auto" uses sklearn's paper cutoff (score 0.5)
        self.offset_ = -0.5 if self.contamination == "auto" else np.percentile(samples, 100.0 * self.contamination)
        raw_scores = samples - self.offset_
        self.raw_min_, self.raw_max_ = raw_scores.min(), raw_scores.max()
        return raw_scores

    def score(self, df: pd.DataFrame) -> pd.DataFrame:
        """Score new invoices with the already-fitted model (see fit / load)."""
        X = self.scaler.transform(self._feature_frame(df))
        return self._with_scores(df, self.model.score_samples(X) - self.offset_)

    def _with_scores(self, df: pd.DataFrame, raw_scores: np.ndarray) -> pd.DataFrame:
        result_df = df.copy()
        span = self.raw_max_ - self.raw_min_
        # 0-1, higher = more unusual, scaled to the training range (new points outside it are clipped)
        norm_scores = np.clip(1.0 - (raw_scores - self.raw_min_) / span, 0.0, 1.0) if span > 0 else np.zeros_like(raw_scores)
        preds = np.where(raw_scores < 0, -1, 1)
        result_df['ml_score'] = norm_scores
        result_df['ml_prediction'] = preds

        n_anomalies = (preds == -1).sum()
        pct_anomalies = n_anomalies / len(preds) * 100 if len(preds) > 0 else 0.0
        logger.info(f"ML Anomaly Detection: Found {n_anomalies} anomalies ({pct_anomalies:.2f}%)")
        return result_df

    def detect_anomalies(self, df: pd.DataFrame) -> pd.DataFrame:
        """Fit on df and score df itself (batch audit)."""
        if df.empty:
            return df
        return self._with_scores(df, self.fit(df))

    def save(self, path: str = None):
        path = path or MODEL_PATH
        os.makedirs(os.path.dirname(path), exist_ok=True)
        joblib.dump(self, path)

    @staticmethod
    def load(path: str = None) -> "MLDetector | None":
        path = path or MODEL_PATH
        return joblib.load(path) if os.path.exists(path) else None
