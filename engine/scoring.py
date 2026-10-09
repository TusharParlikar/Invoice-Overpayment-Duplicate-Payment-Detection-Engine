"""
Score invoices for duplicates, overpayments and unusual amounts.

    clean -> near-duplicate pairs -> per-vendor features -> 6 rules -> risk score (the strongest rule)

A full audit and a check of new invoices both run these same steps (see engine/checks.py).
"""
import re
from functools import cache

import numpy as np
import pandas as pd
from rapidfuzz import fuzz
from rapidfuzz.distance import JaroWinkler

from engine import config

# ── Cleaning ───────────────────────────────────────────────────────────────

# Legal forms differ between systems for the same company ("Sdn. Bhd." vs "S/B", "Ltd" vs "Limited")
_SUFFIXES = re.compile(r"\b(llc|inc|incorporated|corp|corporation|ltd|limited|co|company|sdn|bhd|berhad|s/b|sb|"
                       r"pvt|private|plc|gmbh|llp|plt|pte|pty)\b")


@cache
def normalize_vendor_name(name) -> str:
    """'ACME Corp., LLC' -> 'acme'; 'TRI SHAAS SDN BHD (728515-M)' -> 'tri shaas'; 'The Acme Co' -> 'acme'."""
    if not isinstance(name, str):
        return ""
    name = re.sub(r"^the\s+", "", re.sub(r"[.,]", "", name.lower().strip()))
    bare = re.sub(r"\(.*?\)", " ", name)  # registration numbers and "(M)" are not part of the name
    out = re.sub(r"\s+", " ", _SUFFIXES.sub("", bare)).strip()
    return out or re.sub(r"\s+", " ", name).strip()  # "(SEMENYIH) SDN BHD": keep something to compare


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
    df["invoice_key"] = df["invoice_id"].astype(str).str.upper().str.replace(r"[^A-Z0-9]", "", regex=True)
    return df.fillna({"amount": 0, "amount_usd": 0})


# ── Near-duplicate matching ────────────────────────────────────────────────

PAIR_COLUMNS = ["record_a_id", "record_b_id", "similarity_score", "levenshtein_score", "jaro_winkler_score", "match_type"]


def find_near_duplicates(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Pairs of invoices that are the same invoice entered twice in different formats.

    Candidates share a vendor-name prefix or a vendor ID, amounts within NEAR_DUP_AMOUNT_TOLERANCE and dates within
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
    # Two blockings: the name prefix finds the same vendor under another ID, the vendor ID finds a name written
    # differently ("THE ACME" vs "ACME"). A pair found by both is kept once.
    groups = [g for block, g in w.groupby("block") if block] + [g for _, g in w.groupby("vendor_id")]
    for g in groups:
        if len(g) < 2:
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
    return pd.DataFrame(pairs, columns=PAIR_COLUMNS).drop_duplicates(["record_a_id", "record_b_id"]), candidates


# ── Features (per vendor) ──────────────────────────────────────────────────

LOO_MAX_GROUP = 500  # above this many invoices one invoice barely moves the vendor's median, so all are used


def _others_stats(a: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """For each amount: median and MAD of the vendor's *other* amounts (NaN if there are none).

    Leaving the invoice out matters: an overpayment included in its own baseline drags the baseline
    toward itself, which hid every overpayment at vendors with fewer than ~14 invoices.
    """
    m = len(a)
    if m == 1:
        return np.full(1, np.nan), np.full(1, np.nan)
    if m > LOO_MAX_GROUP:
        med = np.median(a)
        return np.full(m, med), np.full(m, np.median(np.abs(a - med)))
    others = np.broadcast_to(a, (m, m))[~np.eye(m, dtype=bool)].reshape(m, m - 1)
    med = np.median(others, axis=1)
    return med, np.median(np.abs(others - med[:, None]), axis=1)


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    amount = df["amount_usd"].to_numpy(float)
    med, n = np.full(len(df), np.nan), np.full(len(df), np.nan)
    for idx in df.groupby("vendor_id").indices.values():
        med[idx], _ = _others_stats(amount[idx])
        n[idx] = len(idx) - 1
    df["vendor_history_count"] = n
    df["amount_to_vendor_median"] = np.nan_to_num(amount / np.where(med > 0, med, np.nan), nan=1.0)
    # How unusual the amount is for this vendor, on a log scale: amounts vary by multiples (a supermarket bill of 5 or
    # 500 is normal, a supplier billing 1,000 every month is not), so 3x means much more for a steady vendor. Robust
    # z-score: median and MAD (x1.4826 = standard deviation for normal data) of the vendor's other invoices. A vendor
    # that always bills the same amount has MAD 0; any other amount is then infinitely unusual, capped at 50.
    log = np.log(np.maximum(amount, 0.01))
    lmed, lmad = (np.full(len(df), np.nan) for _ in range(2))
    for idx in df.groupby("vendor_id").indices.values():
        lmed[idx], lmad[idx] = _others_stats(log[idx])
    with np.errstate(divide="ignore", invalid="ignore"):
        z = (log - lmed) / (1.4826 * lmad)
    df["amount_robust_z"] = np.nan_to_num(np.clip(np.where((lmad == 0) & np.isclose(log, lmed), 0.0, z), -50, 50))
    return df


# ── Rules ──────────────────────────────────────────────────────────────────

def apply_rules(df: pd.DataFrame, pairs: pd.DataFrame) -> pd.DataFrame:
    """Each rule scores 0-1. final_score is the strongest, risk_category its tier (HIGH holds the payment,
    MEDIUM asks for a review), flags names the rules that fired."""
    df = df.copy()
    ratio, amount = df["amount_to_vendor_median"], df["amount_usd"]
    rules = {}
    # A later copy of the same vendor + invoice number + amount (the first stays clean)
    rules["exact_dup"] = df.duplicated(["vendor_id", "invoice_id", "amount_usd"], keep="first").astype(float)
    rules["near_dup"] = df["id"].map(pairs.groupby("record_b_id")["similarity_score"].max()).fillna(0.0)
    # The same number billed again for a different amount (tax added, rounded up, a "corrected" copy). A review,
    # not a hold: some vendors do reissue a number. Exact copies are left to exact_dup. A "number" on 3+ of the
    # vendor's invoices is a store, terminal or tax ID read as the invoice number (a Domino's GST ID on 5 receipts).
    uses = df.groupby(["vendor_id", "invoice_key"])["invoice_key"].transform("size")
    rebilled = (df["invoice_key"] != "") & (uses == 2) & df.duplicated(["vendor_id", "invoice_key"], keep="first")
    rules["same_number"] = np.where(rebilled & (rules["exact_dup"] == 0), 0.6, 0.0)
    judged = df["vendor_history_count"] >= config.OVERPAYMENT_MIN_HISTORY
    z = df["amount_robust_z"]
    over = judged & (ratio >= config.OVERPAYMENT_MULTIPLIER) & (z >= config.OVERPAYMENT_MIN_ZSCORE)
    maybe_over = judged & (ratio >= config.OVERPAYMENT_REVIEW_MULTIPLIER) & (z >= config.OVERPAYMENT_REVIEW_ZSCORE)
    # Strong evidence holds the payment (HIGH); moderate evidence asks for a review (MEDIUM)
    rules["overpayment"] = np.where(over, np.maximum(0.85, (ratio - 1.0).clip(0.0, 5.0) / 5.0), np.where(maybe_over, 0.7, 0.0))
    burst = df.groupby(["vendor_id", "po_number", "invoice_date"])["invoice_id"].transform("count")
    rules["rapid_fire"] = np.where(burst >= config.RAPID_FIRE_MIN_COUNT, 0.90, 0.0)
    round_amt = ((amount >= config.ROUND_NUMBER_MIN_AMOUNT) & (amount % config.ROUND_NUMBER_STEP == 0)
                 & (ratio >= config.ROUND_NUMBER_MIN_MEDIAN_RATIO))
    rules["round_number"] = np.where(round_amt, 0.65, 0.0)

    scores = pd.DataFrame(rules, index=df.index)
    df["final_score"] = scores.max(axis=1).clip(upper=1.0)
    df["risk_category"] = np.select([df["final_score"] >= config.RISK_HIGH_THRESHOLD,
                                     df["final_score"] >= config.RISK_MEDIUM_THRESHOLD], ["HIGH", "MEDIUM"], "LOW")
    df["flags"] = scores.gt(0).dot(scores.columns + ",").str.rstrip(",").replace("", "none")
    return df


def score(df: pd.DataFrame, new_ids=frozenset()) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    """Clean, match, featurize and apply rules. Returns (rows, near-duplicate pairs, candidates checked).

    new_ids: rows being checked; in a pair with a historical record they are always the duplicate side.
    """
    df = clean(df)
    pairs, candidates = find_near_duplicates(df)
    flip = pairs["record_a_id"].isin(new_ids) & ~pairs["record_b_id"].isin(new_ids)
    # copy=True: a view would be overwritten mid-swap, leaving both ids equal
    pairs.loc[flip, ["record_a_id", "record_b_id"]] = pairs.loc[flip, ["record_b_id", "record_a_id"]].to_numpy(copy=True)
    return apply_rules(add_features(df), pairs), pairs, candidates
