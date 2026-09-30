"""
Check new invoices / receipts against the company's records.

New rows are scored exactly like historical invoices in an audit: appended to the history,
cleaned, fuzzy-matched, featurized, run through the rules, scored by the trained Isolation
Forest and blended by the ensemble. Only the new rows come back, with plain-language reasons.
"""
import os
import sys
from functools import cache

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from database.db_manager import DatabaseManager
from detection.ensemble import EnsembleDetector
from detection.ml_detector import MLDetector
from detection.rule_engine import RuleEngine
from intake.importer import resolve_vendor_ids
from matching.fuzzy_matcher import FuzzyMatcher
from preprocessing.cleaner import clean_invoice_data, normalize_vendor_name
from preprocessing.feature_engineer import engineer_features

INPUT_COLUMNS = ["invoice_id", "vendor_name", "invoice_date", "amount", "currency", "vendor_id", "po_number"]


def verdict(risk_category: str, tampered: bool = False) -> str:
    """Record risk decides SUSPICIOUS; an image-tamper flag alone only asks for a manual review
    (the image model is much less accurate than the record checks, see README)."""
    if risk_category == "HIGH":
        return "SUSPICIOUS"
    if risk_category == "MEDIUM" or tampered:
        return "REVIEW"
    return "OK"


def check_invoices(new: pd.DataFrame, db: DatabaseManager, model: MLDetector | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Score new invoices against history.

    new: invoice_id, vendor_name, invoice_date, amount (+ optional currency, vendor_id, po_number).
    Returns (one result row per new invoice, matched historical records per new invoice).
    """
    new = new.reindex(columns=INPUT_COLUMNS).reset_index(drop=True)
    new["id"] = -(new.index + 1)  # temporary ids; real records are >= 1
    new["currency"] = new["currency"].fillna("USD")
    if new["vendor_id"].isna().any():
        vendors = db.execute_query("SELECT vendor_id, vendor_name FROM vendors")
        new["vendor_id"] = new["vendor_id"].fillna(resolve_vendor_ids(new["vendor_name"], vendors))
    new_ids = set(new["id"])
    history = _relevant_history(db, new)

    df = clean_invoice_data(pd.concat([history, new], ignore_index=True))

    # Fuzzy pairs orient the later-dated invoice as the duplicate; for a check, the new one is the suspect.
    matches, _ = FuzzyMatcher().find_potential_duplicates(df)
    flip = matches["record_a_id"].isin(new_ids) & ~matches["record_b_id"].isin(new_ids)
    # copy=True: a view would be overwritten mid-swap, leaving both ids equal
    matches.loc[flip, ["record_a_id", "record_b_id"]] = matches.loc[flip, ["record_b_id", "record_a_id"]].to_numpy(copy=True)
    fuzzy_scores = matches.groupby("record_b_id")["similarity_score"].max().to_dict()

    df = RuleEngine().apply_all_rules(engineer_features(df, fuzzy_scores), matches)
    rows = df[df["id"] < 0].sort_values("id", ascending=False)

    model = model or MLDetector.load()
    if model is not None:
        rows = model.score(rows)
    else:  # not trained yet (run the audit): rules only
        rows = rows.assign(ml_score=0.0, ml_prediction=1)
    rows = EnsembleDetector().run_ensemble(rows)

    related = _related_records(rows, matches, df)
    known_vendors = set(history["vendor_id"])
    rows["reasons"] = [_reasons(r, related[related["new_id"] == r.id], r.vendor_id in known_vendors)
                       for r in rows.itertuples()]
    rows["verdict"] = rows["risk_category"].map(verdict)
    keep = INPUT_COLUMNS + ["id", "final_score", "risk_category", "verdict", "all_flags", "reasons",
                            "rule_score", "ml_score", "amount_to_vendor_median"]
    return rows[keep].reset_index(drop=True), related


_norm_name = cache(normalize_vendor_name)  # vendor names repeat across checks in a long-running app


def _relevant_history(db: DatabaseManager, new: pd.DataFrame) -> pd.DataFrame:
    """Only the records that can change the new rows' scores: same vendor (stats, exact
    duplicates) or same fuzzy-matching block (near duplicates). Scores are identical to
    checking against the full history, without loading it."""
    blocks = {_norm_name(n)[:config.BLOCKING_KEY_LENGTH] for n in new["vendor_name"]} - {""}
    names = db.execute_query("SELECT DISTINCT vendor_name FROM invoices")["vendor_name"]
    in_block = [n for n in names if _norm_name(n)[:config.BLOCKING_KEY_LENGTH] in blocks]
    vendor_ids = list(set(new["vendor_id"]))
    # ponytail: one IN list; SQLite caps bound parameters at 32,766, fine for a single company's vendor names
    sql = (f"SELECT * FROM invoices WHERE vendor_id IN ({','.join('?' * len(vendor_ids))})"
           f" OR vendor_name IN ({','.join('?' * len(in_block)) or 'NULL'})")
    return db.execute_query(sql, params=tuple(vendor_ids + in_block))


def _related_records(rows: pd.DataFrame, matches: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    """Historical records each new invoice duplicates: fuzzy pairs plus exact copies."""
    cols = ["id", "invoice_id", "vendor_name", "invoice_date", "amount"]
    fuzzy = matches[matches["record_b_id"].isin(rows["id"])][["record_b_id", "record_a_id", "similarity_score", "match_type"]]
    fuzzy.columns = ["new_id", "record_id", "similarity", "match_type"]

    key = ["vendor_id", "invoice_id", "amount_usd"]
    exact = rows[key + ["id"]].rename(columns={"id": "new_id"}).merge(
        df[key + ["id"]].rename(columns={"id": "record_id"}), on=key)
    # A copy within the same batch only counts against the earlier row (-1 is first, so earlier = larger id)
    earlier = (exact["record_id"] > 0) | (exact["record_id"] > exact["new_id"])
    exact = exact[earlier][["new_id", "record_id"]].assign(similarity=1.0, match_type="exact")

    related = pd.concat([exact, fuzzy], ignore_index=True).drop_duplicates(["new_id", "record_id"])
    details = df[cols].rename(columns={"id": "record_id"})
    details["invoice_date"] = details["invoice_date"].astype(str).str[:10]
    return related.merge(details, on="record_id", how="left")


def _reasons(r, related: pd.DataFrame, known_vendor: bool) -> str:
    flags = str(r.all_flags).split(",")
    out = []
    for m in related.itertuples():
        what = "Exact duplicate" if m.match_type == "exact" else f"Near-duplicate ({m.similarity:.0%} similar)"
        who = "another invoice in this batch" if m.record_id < 0 else f"record #{m.record_id}"
        out.append(f"{what} of {who}: {m.invoice_id}, {m.vendor_name}, {m.invoice_date}, {m.amount:,.2f}")
    if "overpayment" in flags:
        out.append(f"Amount is {r.amount_to_vendor_median:.1f}x this vendor's usual (median) invoice")
    if "rapid_fire" in flags:
        out.append(f"{config.RAPID_FIRE_MIN_COUNT}+ invoices from this vendor on the same PO and date")
    if "round_number" in flags:
        out.append("Large round-number amount, well above this vendor's usual")
    if "ml_isolation_forest" in flags:
        c = config.ISOLATION_FOREST_CONTAMINATION
        out.append(f"Among the most unusual {c:.0%} of past payments (anomaly model)" if isinstance(c, float)
                   else "Unusual compared with past payments (anomaly model)")
    if not known_vendor:
        out.append("New vendor: no payment history to compare against")
    return "; ".join(out) or "No issues found against existing records"


def add_to_records(results: pd.DataFrame, db: DatabaseManager, source: str = "receipt-check") -> int:
    """Save checked invoices as company records so future duplicates of them are caught."""
    from intake.importer import import_records
    rows = results[INPUT_COLUMNS].copy()
    rows["invoice_date"] = pd.to_datetime(rows["invoice_date"]).dt.date.astype(str)
    return import_records(rows, db, source=source)
