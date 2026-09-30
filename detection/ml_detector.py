import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
import logging
import config

logger = logging.getLogger(__name__)

class MLDetector:
    def __init__(self):
        self.n_estimators = config.ISOLATION_FOREST_N_ESTIMATORS
        self.contamination = config.ISOLATION_FOREST_CONTAMINATION
        self.random_state = config.ISOLATION_FOREST_RANDOM_STATE
        
        self.model = IsolationForest(
            n_estimators=self.n_estimators,
            contamination=self.contamination,
            random_state=self.random_state
        )
        self.scaler = StandardScaler()
        
        self.FEATURE_COLUMNS = [
            'amount_usd', 'amount_zscore', 'amount_to_vendor_median', 
            'amount_to_vendor_max', 'vendor_invoice_count_30d', 
            'days_since_last_invoice', 'same_amount_count_30d', 'max_fuzzy_score'
        ]

    def prepare_features(self, df: pd.DataFrame) -> np.ndarray:
        available_cols = [c for c in self.FEATURE_COLUMNS if c in df.columns]
        
        features_df = pd.DataFrame(index=df.index)
        for col in self.FEATURE_COLUMNS:
            if col in df.columns:
                features_df[col] = df[col]
            else:
                features_df[col] = 0.0
                
        features_df = features_df.fillna(0.0)
        scaled_features = self.scaler.fit_transform(features_df)
        return scaled_features

    def detect_anomalies(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
            
        result_df = df.copy()
        
        X = self.prepare_features(result_df)
        
        self.model.fit(X)
        preds = self.model.predict(X)
        raw_scores = self.model.decision_function(X)
        
        min_score = raw_scores.min()
        max_score = raw_scores.max()
        
        if max_score > min_score:
            norm_scores = 1.0 - (raw_scores - min_score) / (max_score - min_score)
        else:
            norm_scores = np.zeros_like(raw_scores)
            
        result_df['ml_score'] = norm_scores
        result_df['ml_prediction'] = preds
        
        n_anomalies = (preds == -1).sum()
        pct_anomalies = n_anomalies / len(preds) * 100 if len(preds) > 0 else 0.0
        logger.info(f"ML Anomaly Detection: Found {n_anomalies} anomalies ({pct_anomalies:.2f}%)")
        
        return result_df
