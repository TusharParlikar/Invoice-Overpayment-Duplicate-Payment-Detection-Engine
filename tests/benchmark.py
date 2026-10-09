"""
Accuracy benchmark on real records, end to end (import -> audit -> check), 10-fold.

    python tests/benchmark.py [records.csv]

Default records: the public receipt dataset's genuine receipts, as loaded by `python cli.py demo` (downloads
670 MB once), so anyone can reproduce the numbers in the docs. Pass a CSV/Excel file to use your own.

Each fold: the other 9 folds are the company history (own throwaway database).
Negatives (should be OK):
  - genuine:   each held-out real record, checked against the history it is not part of
  - next bill: a history record's vendor sends its next invoice (number + 1, similar amount, a week later)
Positives (should be flagged), made from history records with real-world error types:
  - exact copy; number retyped (dashes/spaces/prefix); OCR confusion in the number (O/0, I/1, S/5, B/8);
    vendor name written differently; "THE " before the name plus a retyped number; date shifted 1-5 days;
    all of these combined; the same number re-billed with tax added (+6%) or rounded up;
  - overpayment: a new invoice for 2x / 3x / 5x / 10x the vendor's median amount.
The error types come from how duplicates arise, not from the rules' thresholds.
"""
import os
import re
import sys
import tempfile

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import checks, db, importer, tamper

SEED = 7
FOLDS = 10
PER_FOLD = 12  # history records per fold used to build positives
OCR_SWAPS = {"O": "0", "0": "O", "I": "1", "1": "I", "S": "5", "5": "S", "B": "8", "8": "B"}


def _retype(inv: str, rng) -> str:
    if "-" in inv or " " in inv or "/" in inv:
        return re.sub(r"[-\s/]", "", inv)
    i = max(1, len(inv) // 2)
    return inv[:i] + "-" + inv[i:]


def _ocr(inv: str, rng) -> str:
    spots = [i for i, c in enumerate(inv) if c in OCR_SWAPS]
    if not spots:
        return inv + "A"
    i = rng.choice(spots)
    return inv[:i] + OCR_SWAPS[inv[i]] + inv[i + 1:]


def _vendor(name: str, rng) -> str:
    out = re.sub(r"\s*\(.*?\)", "", name)  # drop "(519537-X)"-style registration numbers
    out = out.replace("SDN BHD", "SDN. BHD.") if "SDN BHD" in out else out + " LTD"
    return out.title() if out == name else out


def _next_number(inv: str) -> str:
    m = list(re.finditer(r"\d+", inv))
    if not m:
        return inv + "2"
    last = m[-1]
    return inv[:last.start()] + str(int(last.group()) + 1).zfill(len(last.group())) + inv[last.end():]


def make_cases(history: pd.DataFrame, rng) -> pd.DataFrame:
    base = history.sample(min(PER_FOLD, len(history)), random_state=int(rng.integers(1e9)))
    shift = lambda d, k: (pd.Timestamp(d) + pd.Timedelta(days=int(k))).date().isoformat()
    medians = history.groupby("vendor_id")["amount"].median()
    on_record = set(zip(history["vendor_id"], history["invoice_id"]))
    cases = []
    for r in base.itertuples():
        row = dict(invoice_id=r.invoice_id, vendor_name=r.vendor_name, invoice_date=r.invoice_date,
                   amount=r.amount, currency=r.currency)
        k = rng.integers(1, 6) * rng.choice([-1, 1])
        variants = {
            "exact copy": row,
            "number retyped": {**row, "invoice_id": _retype(r.invoice_id, rng)},
            "OCR confusion in number": {**row, "invoice_id": _ocr(r.invoice_id, rng)},
            "vendor name variant": {**row, "vendor_name": _vendor(r.vendor_name, rng)},
            "date shifted 1-5 days": {**row, "invoice_date": shift(r.invoice_date, k)},
            '"THE" + name, number retyped': {**row, "vendor_name": "THE " + r.vendor_name, "invoice_id": _retype(r.invoice_id, rng)},
            "same number, +6% (tax added)": {**row, "amount": round(r.amount * 1.06, 2)},
            "same number, rounded up": {**row, "amount": float(np.floor(r.amount) + 1)},
            "all combined": {**row, "invoice_id": _retype(r.invoice_id, rng), "vendor_name": _vendor(r.vendor_name, rng),
                             "invoice_date": shift(r.invoice_date, k)},
            "next bill (negative)": {**row, "invoice_id": _next_number(r.invoice_id), "invoice_date": shift(r.invoice_date, 7),
                                     "amount": round(r.amount * rng.uniform(0.97, 1.03), 2)},
        }
        for mult in (2, 3, 5, 10):
            variants[f"overpayment {mult}x"] = {**row, "invoice_id": f"BM-{rng.integers(1e6)}", "invoice_date": shift(r.invoice_date, 30),
                                               "amount": round(medians[r.vendor_id] * mult, 2)}
        if (r.vendor_id, _next_number(r.invoice_id)) in on_record:  # that number is a real receipt, not a new bill
            del variants["next bill (negative)"]
        cases += [{**v, "case": name} for name, v in variants.items()]
    return pd.DataFrame(cases)


def run(records: pd.DataFrame) -> pd.DataFrame:
    rng = np.random.default_rng(SEED)
    records = records.sample(frac=1, random_state=SEED).reset_index(drop=True)
    fold_of = np.arange(len(records)) % FOLDS
    results = []
    for f in range(FOLDS):
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(os.path.join(tmp, "b.db"))
            importer.import_records(conn, records[fold_of != f].drop(columns=["vendor_id"]))
            checks.audit(conn)
            history = db.query(conn, "SELECT * FROM invoices")
            held = records[fold_of == f].drop(columns=["vendor_id"])
            for r in held.itertuples():  # one at a time, as a user would
                res, _ = checks.check(conn, pd.DataFrame([r._asdict()]).drop(columns="Index"))
                results.append({"case": "genuine (negative)", "verdict": res["verdict"].item(),
                                "history": res["vendor_history_count"].item()})
            for _, c in make_cases(history, rng).iterrows():
                res, _ = checks.check(conn, pd.DataFrame([c.drop("case")]))
                results.append({"case": c["case"], "verdict": res["verdict"].item(),
                                "history": res["vendor_history_count"].item()})
            conn.close()
    return pd.DataFrame(results)


def report(results: pd.DataFrame) -> pd.DataFrame:
    # Amounts can only be judged against a vendor's history: also report vendors with 10+ past invoices
    over = results[results["case"].str.startswith("overpayment") & (results["history"] >= 10)]
    results = pd.concat([results, over.assign(case=over["case"] + ", vendor has 10+ invoices")])
    t = results.groupby("case")["verdict"].agg(
        n="size", suspicious=lambda v: (v == "SUSPICIOUS").mean(), flagged=lambda v: (v != "OK").mean())
    t["kind"] = np.where(t.index.str.contains("negative"), "false-positive rate", "recall")
    return t.sort_values(["kind", "case"])


if __name__ == "__main__":
    if len(sys.argv) > 1:
        recs = importer.normalize(importer.read_table(sys.argv[1]))[0]
    else:
        recs = tamper.demo_history()
    recs = recs.drop_duplicates(["vendor_name", "invoice_id", "amount"])  # known duplicates would count as false positives
    out = report(run(recs.assign(vendor_id=recs.get("vendor_id"))))
    pd.set_option("display.width", 120)
    print(out.to_string(formatters={"suspicious": "{:.1%}".format, "flagged": "{:.1%}".format}))
