"""
The two things the engine does with the scoring in engine/scoring.py:

- audit():  score every company record, train the anomaly model on them, save both.
- check():  score new invoices against the records (only the records that can affect them).
"""
import sqlite3

import pandas as pd
from rapidfuzz import fuzz, process

from engine import config, db
from engine.importer import import_records, resolve_vendor_ids
from engine.scoring import (blend, normalize_vendor_name, save_model, score_model, score_rules, train_model,
                            usd_rates)

INPUT_COLUMNS = ["invoice_id", "vendor_name", "invoice_date", "amount", "currency", "vendor_id", "po_number"]


def audit(conn: sqlite3.Connection, model_path: str = config.ANOMALY_MODEL_PATH) -> dict:
    """Score all records, retrain + save the anomaly model, store results. Returns a summary."""
    records = db.query(conn, "SELECT * FROM invoices")
    if records.empty:
        return {"records": 0}
    df, pairs, candidates = score_rules(records)
    model, df = train_model(df)
    save_model(model, model_path)
    df = blend(df)

    db.replace_results(conn, df[["id", "rule_score", "ml_score", "final_score", "risk_category", "all_flags"]]
                       .rename(columns={"id": "invoice_row_id", "all_flags": "flags"}))
    summary = {"records": len(df), "vendors": df["vendor_id"].nunique(), "model_trained": model is not None,
               "near_duplicate_candidates": candidates, "near_duplicates": len(pairs),
               **df["risk_category"].value_counts().to_dict()}
    if "is_anomaly" in df and df["is_anomaly"].notna().any():
        summary["accuracy"] = evaluate(df[df["is_anomaly"].notna()])
    return summary


def evaluate(df: pd.DataFrame) -> dict:
    """Precision/recall of the HIGH tier against is_anomaly labels, at the configured threshold.
    The threshold is never tuned on the labels it is scored against."""
    truth = df["is_anomaly"].astype(bool)
    flagged = df["risk_category"] == "HIGH"
    tp = int((truth & flagged).sum())
    out = {"labelled": len(df), "known_bad": int(truth.sum()), "flagged": int(flagged.sum()),
           "precision": tp / flagged.sum() if flagged.any() else 0.0, "recall": tp / truth.sum() if truth.any() else 0.0}
    if "anomaly_type" in df:
        out["recall_by_type"] = flagged[truth].groupby(df.loc[truth, "anomaly_type"]).mean().round(3).to_dict()
    return out


def verdict(risk_category: str, tampered: bool = False) -> str:
    """Record risk decides SUSPICIOUS. An image-tamper flag alone only asks for a review:
    the image model is far less accurate than the record checks."""
    if risk_category == "HIGH":
        return "SUSPICIOUS"
    if risk_category == "MEDIUM" or tampered:
        return "REVIEW"
    return "OK"


def check(conn: sqlite3.Connection, new: pd.DataFrame, model: dict | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Score new invoices against the records.

    new: invoice_id, vendor_name, invoice_date, amount (+ optional currency, vendor_id, po_number).
    model: the trained anomaly model (scoring.load_model()), or None for rules only.
    Returns (one result row per new invoice, the records each one matches).
    """
    new = new.reindex(columns=INPUT_COLUMNS).reset_index(drop=True)
    new["id"] = -(new.index + 1)  # temporary ids; stored records are >= 1
    new["currency"] = new["currency"].fillna("USD").astype(str).str.upper()
    vendors = db.query(conn, "SELECT vendor_id, vendor_name FROM vendors")
    if new["vendor_id"].isna().any():
        new["vendor_id"] = new["vendor_id"].fillna(resolve_vendor_ids(new["vendor_name"], vendors))
    history = _relevant_history(conn, new)

    df, pairs, _ = score_rules(pd.concat([history, new], ignore_index=True), new_ids=set(new["id"]))
    rows = df[df["id"] < 0].sort_values("id", ascending=False)
    rows = blend(score_model(model, rows))

    related = _related_records(rows, pairs, df)
    known_vendors, rates = set(history["vendor_id"]), usd_rates()
    rows["reasons"] = [_reasons(r, related[related["new_id"] == r.id],
                                None if r.vendor_id in known_vendors else _closest_vendor(r.vendor_name, vendors),
                                r.currency in rates)
                       for r in rows.itertuples()]
    rows["verdict"] = rows["risk_category"].map(verdict)
    keep = INPUT_COLUMNS + ["id", "final_score", "risk_category", "verdict", "all_flags", "reasons",
                            "rule_score", "ml_score", "amount_to_vendor_median", "vendor_history_count"]
    return rows[keep].reset_index(drop=True), related


def _relevant_history(conn: sqlite3.Connection, new: pd.DataFrame) -> pd.DataFrame:
    """Records that can change the new rows' scores: same vendor (stats, exact duplicates) or same
    matching block (near duplicates). Same scores as checking against everything, without loading it."""
    key = lambda n: normalize_vendor_name(n)[:config.BLOCKING_KEY_LENGTH]
    blocks = {key(n) for n in new["vendor_name"]} - {""}
    names = db.query(conn, "SELECT DISTINCT vendor_name FROM invoices")["vendor_name"]
    in_block = [n for n in names if key(n) in blocks]
    vendor_ids = list(set(new["vendor_id"]))
    # One IN list each: SQLite allows 32,766 parameters, plenty for one company's vendor names
    sql = (f"SELECT * FROM invoices WHERE vendor_id IN ({','.join('?' * len(vendor_ids))})"
           f" OR vendor_name IN ({','.join('?' * len(in_block)) or 'NULL'})")
    return db.query(conn, sql, tuple(vendor_ids + in_block))


def _related_records(rows: pd.DataFrame, pairs: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    """Records each new invoice duplicates: near-duplicate pairs plus exact copies."""
    fuzzy = pairs[pairs["record_b_id"].isin(rows["id"])][["record_b_id", "record_a_id", "similarity_score", "match_type"]]
    fuzzy.columns = ["new_id", "record_id", "similarity", "match_type"]

    key = ["vendor_id", "invoice_id", "amount_usd"]
    exact = rows[key + ["id"]].rename(columns={"id": "new_id"}).merge(df[key + ["id"]].rename(columns={"id": "record_id"}), on=key)
    # A copy within the same batch only counts against the earlier row (-1 is first, so earlier = larger id)
    exact = exact[(exact["record_id"] > 0) | (exact["record_id"] > exact["new_id"])][["new_id", "record_id"]]
    exact = exact.assign(similarity=1.0, match_type="exact")

    related = pd.concat([exact, fuzzy], ignore_index=True).drop_duplicates(["new_id", "record_id"])
    details = df[["id", "invoice_id", "vendor_name", "invoice_date", "amount"]].rename(columns={"id": "record_id"})
    details["invoice_date"] = details["invoice_date"].astype(str).str[:10]
    return related.merge(details, on="record_id", how="left")


def _closest_vendor(name: str, vendors: pd.DataFrame) -> str:
    """Best guess at which known vendor a new name means ("" if nothing is close), so the user can fix the name."""
    names = dict(zip(vendors["vendor_name"].map(normalize_vendor_name), vendors["vendor_name"]))
    hit = process.extractOne(normalize_vendor_name(name), list(names), scorer=fuzz.WRatio, score_cutoff=70)
    return names[hit[0]] if hit else ""


def _reasons(r, related: pd.DataFrame, closest_vendor: str | None, has_rate: bool) -> str:
    """closest_vendor: None for a known vendor, else the nearest known name ("" if none)."""
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
        out.append(f"Among the most unusual {config.ISOLATION_FOREST_CONTAMINATION:.0%} of past payments (anomaly model)")
    if closest_vendor is not None:
        out.append("New vendor: no payment history to compare against"
                   + (f". Did you mean '{closest_vendor}'? Correct the name or set its vendor ID to compare" if closest_vendor else ""))
    if not has_rate:
        out.append(f"No exchange rate for {r.currency}: amount compared as if it were USD (add one under Exchange rates)")
    return "; ".join(out) or "No issues found against existing records"


def add_to_records(conn: sqlite3.Connection, results: pd.DataFrame, source: str = "receipt-check") -> int:
    """Save checked invoices as company records so future copies of them are caught."""
    rows = results[INPUT_COLUMNS].copy()
    rows["invoice_date"] = pd.to_datetime(rows["invoice_date"]).dt.date.astype(str)
    return import_records(conn, rows, source=source)


def log_checks(conn: sqlite3.Connection, results: pd.DataFrame, source: str, file_name: str | None = None,
               tamper_score: float | None = None, verdicts: list[str] | None = None):
    """Append checked invoices to the receipt_checks log."""
    log = results[["invoice_id", "vendor_name", "invoice_date", "amount", "currency", "final_score", "risk_category"]].copy()
    log["invoice_date"] = log["invoice_date"].astype(str).str[:10]
    log = log.assign(source=source, file_name=file_name, flags=results["all_flags"], tamper_score=tamper_score,
                     verdict=verdicts if verdicts is not None else results["verdict"])
    db.append(conn, "receipt_checks", log)
