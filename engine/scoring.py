"""
Score invoices for duplicates, overpayments and unusual amounts.

    clean -> near-duplicate pairs -> per-vendor features -> 5 rules -> anomaly model -> risk score

A full audit and a check of new invoices both run these same steps (see engine/checks.py).
"""
import os
import re
from functools import cache

import joblib
import numpy as np
import pandas as pd
from rapidfuzz import fuzz
from rapidfuzz.distance import JaroWinkler
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from engine import config

# ── Cleaning ───────────────────────────────────────────────────────────────

_SUFFIXES = re.compile(r"\b(llc|inc|incorporated|corp|corporation|ltd|limited|co|company)\b")


@cache
def normalize_vendor_name(name) -> str:
    """'ACME Corp., LLC' -> 'acme'."""
    if not isinstance(name, str):
        return ""
    name = re.sub(r"[.,]", "", name.lower().strip())
    return re.sub(r"\s+", " ", _SUFFIXES.sub("", name)).strip()


def usd_rates() -> dict[str, float]:
    """USD per unit of each currency: the editable exchange-rate table if present, else config defaults."""
    try:
        t = pd.read_csv(config.EXCHANGE_RATES_PATH)
    except FileNotFoundError:
        return dict(config.USD_RATES)
    return {**config.USD_RATES, **dict(zip(t["currency"].str.upper().str.strip(), t["usd_per_unit"].astype(float)))}


def clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["invoice_date"] = pd.to_datetime(df["invoice_date"])
    if "due_date" in df.columns:
        df["due_date"] = pd.to_datetime(df["due_date"])
    # A currency without a rate is compared as-is; checks say so in the reasons
    df["amount_usd"] = df["amount"] * df["currency"].map(usd_rates()).fillna(1.0)
    df["vendor_name_clean"] = df["vendor_name"].map(normalize_vendor_name)
    return df.fillna({"amount": 0, "amount_usd": 0})


# ── Near-duplicate matching ────────────────────────────────────────────────

PAIR_COLUMNS = ["record_a_id", "record_b_id", "similarity_score", "levenshtein_score", "jaro_winkler_score", "match_type"]


def find_near_duplicates(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Pairs of invoices that are the same invoice entered twice in different formats.

    Candidates share a vendor-name prefix, amounts within NEAR_DUP_AMOUNT_TOLERANCE and dates within
    NEAR_DUP_DATE_WINDOW_DAYS. A candidate is confirmed when, for the same vendor, the invoice numbers
    match after removing dashes/spaces or are >= INVOICE_ID_MATCH_THRESHOLD similar; or when the vendor
    names are >= FUZZY_THRESHOLD similar (VENDOR_NAME_STRICT_THRESHOLD across vendor IDs) and the
    numbers >= INVOICE_ID_SUPPORT_THRESHOLD. Numbers that differ only in their digits must also share the
    date (sequential invoices are not duplicates). record_b is the later (duplicate) invoice.
    Exact copies are left to the exact-duplicate rule. Returns (pairs, number of candidates checked).
    """
    w = df[["id", "invoice_id", "vendor_id", "vendor_name_clean", "invoice_date", "amount_usd"]].copy()
    w["day"] = pd.to_datetime(w["invoice_date"]).values.astype("datetime64[D]").astype(np.int64)
    w["block"] = w["vendor_name_clean"].astype(str).str.lower().str.strip().str[:config.BLOCKING_KEY_LENGTH]
    w["clean_id"] = w["invoice_id"].astype(str).str.replace("-", "").str.replace(" ", "").str.upper()
    w["id_shape"] = w["clean_id"].str.replace(r"\d", "#", regex=True)  # INV0501 -> INV####

    pairs, candidates = [], 0
    for block, g in w.groupby("block"):
        if not block or len(g) < 2:
            continue
        g = g.sort_values("amount_usd")
        # Plain lists: scalar access on numpy arrays is several times slower in this loop
        ids, raw_ids, clean_ids, shapes, amts, days, names, vids = (g[c].tolist() for c in
            ["id", "invoice_id", "clean_id", "id_shape", "amount_usd", "day", "vendor_name_clean", "vendor_id"])
        for i in range(len(ids)):
            max_amt = amts[i] * (1 + config.NEAR_DUP_AMOUNT_TOLERANCE) + 0.01
            j = i + 1
            while j < len(ids) and amts[j] <= max_amt:
                if (raw_ids[i] == raw_ids[j] and vids[i] == vids[j]) or abs(days[j] - days[i]) > config.NEAR_DUP_DATE_WINDOW_DAYS:
                    j += 1
                    continue
                candidates += 1
                same_vendor = vids[i] == vids[j]
                same_id = same_vendor and clean_ids[i] == clean_ids[j]           # SAP-2024-1 vs SAP20241
                # Numbers that differ only in digits on different dates are the vendor's next invoice (INV-0501,
                # INV-0502 a week later, e.g. a recurring bill), not a retyped copy: a typo keeps the document's date
                if not same_id and shapes[i] == shapes[j] and days[i] != days[j]:
                    j += 1
                    continue
                id_sim = fuzz.ratio(clean_ids[i], clean_ids[j]) / 100.0
                similar_id = same_vendor and id_sim >= config.INVOICE_ID_MATCH_THRESHOLD
                lev = fuzz.ratio(str(names[i]), str(names[j])) / 100.0 if names[i] and names[j] else 0.0
                jw = JaroWinkler.normalized_similarity(str(names[i]), str(names[j])) if names[i] and names[j] else 0.0
                name_sim = config.LEVENSHTEIN_WEIGHT * lev + config.JARO_WINKLER_WEIGHT * jw
                name_variant = name_sim >= config.FUZZY_THRESHOLD and (same_vendor or name_sim >= config.VENDOR_NAME_STRICT_THRESHOLD)

                if same_id or similar_id or (name_variant and id_sim >= config.INVOICE_ID_SUPPORT_THRESHOLD):
                    score = max(name_sim, id_sim if similar_id else (1.0 if same_id else 0.0))
                    later_j = days[j] > days[i] or (days[j] == days[i] and ids[j] > ids[i])
                    a, b = (ids[i], ids[j]) if later_j else (ids[j], ids[i])
                    pairs.append((int(a), int(b), round(float(score), 4), round(float(lev), 4), round(float(jw), 4),
                                  "cross_erp_id" if same_id or similar_id else "vendor_variant"))
                j += 1
    return pd.DataFrame(pairs, columns=PAIR_COLUMNS), candidates


# ── Features (per vendor) ──────────────────────────────────────────────────

# Duplicates are left to the rules; the model only sees how an amount and its timing compare with the vendor's history.
FEATURES = ["amount_usd", "amount_zscore", "amount_to_vendor_median", "amount_to_vendor_max",
            "vendor_invoices_same_month", "days_since_last_invoice", "vendor_same_amount_count"]


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    by_vendor = df.groupby("vendor_id")["amount_usd"]
    safe = lambda s: s.replace(0, 1.0).fillna(1.0)
    df["amount_zscore"] = (df["amount_usd"] - by_vendor.transform("mean")) / safe(by_vendor.transform("std"))
    df["amount_to_vendor_median"] = df["amount_usd"] / safe(by_vendor.transform("median"))
    df["amount_to_vendor_max"] = df["amount_usd"] / safe(by_vendor.transform("max"))

    ordered = df.sort_values(["vendor_id", "invoice_date"])
    gap = ordered["invoice_date"] - ordered.groupby("vendor_id")["invoice_date"].shift(1)
    df["days_since_last_invoice"] = gap.dt.days.fillna(999.0)
    month = df["invoice_date"].dt.to_period("M")
    df["vendor_invoices_same_month"] = df.groupby([df["vendor_id"], month])["invoice_id"].transform("count").astype(float)
    df["vendor_same_amount_count"] = df.groupby([df["vendor_id"], df["amount_usd"].round(1)])["invoice_id"].transform("count").astype(float)
    df[FEATURES] = df[FEATURES].fillna(0.0)
    return df


# ── Rules ──────────────────────────────────────────────────────────────────

def apply_rules(df: pd.DataFrame, pairs: pd.DataFrame) -> pd.DataFrame:
    """Each rule scores 0-1; rule_score is the strongest, rule_flags names those that fired."""
    df = df.copy()
    ratio, amount = df["amount_to_vendor_median"], df["amount_usd"]
    rules = {}
    # A later copy of the same vendor + invoice number + amount (the first stays clean)
    rules["exact_dup"] = df.duplicated(["vendor_id", "invoice_id", "amount_usd"], keep="first").astype(float)
    rules["near_dup"] = df["id"].map(pairs.groupby("record_b_id")["similarity_score"].max()).fillna(0.0)
    over = (ratio >= config.OVERPAYMENT_MULTIPLIER) & (df["amount_zscore"] >= config.OVERPAYMENT_MIN_ZSCORE)
    rules["overpayment"] = np.where(over, np.maximum(0.85, (ratio - 1.0).clip(0.0, 5.0) / 5.0), 0.0)
    burst = df.groupby(["vendor_id", "po_number", "invoice_date"])["invoice_id"].transform("count")
    rules["rapid_fire"] = np.where(burst >= config.RAPID_FIRE_MIN_COUNT, 0.90, 0.0)
    round_amt = ((amount >= config.ROUND_NUMBER_MIN_AMOUNT) & (amount % config.ROUND_NUMBER_STEP == 0)
                 & (ratio >= config.ROUND_NUMBER_MIN_MEDIAN_RATIO))
    rules["round_number"] = np.where(round_amt, 0.65, 0.0)

    scores = pd.DataFrame(rules, index=df.index)
    df["rule_score"] = scores.max(axis=1).clip(upper=1.0)
    df["rule_flags"] = scores.gt(0).dot(scores.columns + ",").str.rstrip(",").replace("", "none")
    return df


# ── Anomaly model (Isolation Forest on the company's own history) ──────────

def train_model(df: pd.DataFrame) -> tuple[dict | None, pd.DataFrame]:
    """Fit on the history and score it. Model is None when there is too little history to learn what normal looks like."""
    if len(df) < config.MIN_RECORDS_FOR_MODEL:
        return None, score_model(None, df)
    scaler = StandardScaler()
    X = scaler.fit_transform(df[FEATURES])
    forest = IsolationForest(n_estimators=config.ISOLATION_FOREST_N_ESTIMATORS,
                             random_state=config.ISOLATION_FOREST_RANDOM_STATE).fit(X)
    samples = forest.score_samples(X)
    offset = np.percentile(samples, 100.0 * config.ISOLATION_FOREST_CONTAMINATION)  # the "unusual" cutoff
    raw = samples - offset
    model = {"features": FEATURES, "scaler": scaler, "forest": forest, "offset": offset,
             "raw_min": raw.min(), "raw_max": raw.max()}
    return model, _with_scores(model, df, raw)


def score_model(model: dict | None, df: pd.DataFrame) -> pd.DataFrame:
    """ml_score 0-1 (higher = more unusual, relative to the training range); ml_prediction -1 = unusual."""
    if model is None:
        return df.assign(ml_score=0.0, ml_prediction=1)
    raw = model["forest"].score_samples(model["scaler"].transform(df[FEATURES])) - model["offset"]
    return _with_scores(model, df, raw)


def _with_scores(model: dict, df: pd.DataFrame, raw: np.ndarray) -> pd.DataFrame:
    span = model["raw_max"] - model["raw_min"]
    return df.assign(ml_score=np.clip(1.0 - (raw - model["raw_min"]) / span, 0.0, 1.0) if span > 0 else 0.0,
                     ml_prediction=np.where(raw < 0, -1, 1))


def save_model(model: dict | None, path: str = config.ANOMALY_MODEL_PATH):
    if model is None:
        if os.path.exists(path):
            os.remove(path)
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    joblib.dump(model, path)


def load_model(path: str = config.ANOMALY_MODEL_PATH) -> dict | None:
    """None if never trained, or trained by an older version on different features (re-run the audit)."""
    try:
        model = joblib.load(path)
    except Exception:
        return None
    return model if isinstance(model, dict) and model.get("features") == FEATURES else None


# ── Risk score ─────────────────────────────────────────────────────────────

def blend(df: pd.DataFrame) -> pd.DataFrame:
    """final_score = 60% rules + 40% model (a strong rule hit alone reaches HIGH), then risk tier and flags."""
    df = df.copy()
    score = config.ENSEMBLE_RULE_WEIGHT * df["rule_score"] + config.ENSEMBLE_ML_WEIGHT * df["ml_score"]
    df["final_score"] = np.where(df["rule_score"] >= config.STRONG_RULE_SCORE,
                                 np.maximum(score, config.STRONG_RULE_FLOOR), score)
    df["risk_category"] = np.select([df["final_score"] >= config.RISK_HIGH_THRESHOLD,
                                     df["final_score"] >= config.RISK_MEDIUM_THRESHOLD], ["HIGH", "MEDIUM"], "LOW")
    flags = df["rule_flags"].replace("none", "")
    flags = flags.mask(df["ml_prediction"] == -1, (flags + ",ml_isolation_forest").str.lstrip(","))
    df["all_flags"] = flags.replace("", "none")
    return df


def score_rules(df: pd.DataFrame, new_ids=frozenset()) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    """Clean, match, featurize and apply rules. Returns (rows, near-duplicate pairs, candidates checked).

    new_ids: rows being checked; in a pair with a historical record they are always the duplicate side.
    """
    df = clean(df)
    pairs, candidates = find_near_duplicates(df)
    flip = pairs["record_a_id"].isin(new_ids) & ~pairs["record_b_id"].isin(new_ids)
    # copy=True: a view would be overwritten mid-swap, leaving both ids equal
    pairs.loc[flip, ["record_a_id", "record_b_id"]] = pairs.loc[flip, ["record_b_id", "record_a_id"]].to_numpy(copy=True)
    return apply_rules(add_features(df), pairs), pairs, candidates
