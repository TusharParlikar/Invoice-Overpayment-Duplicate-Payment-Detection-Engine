"""
Run: python tests/test_engine.py   (or pytest)
Uses a throwaway database and model path; never touches data/ or models/.
"""
import os
import sys
import tempfile

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import checks, config, db
from engine.importer import import_records, normalize, read_table
from engine.receipts import parse_fields


def test_parse_fields():
    f = parse_fields("TAX INVOICE\nSANYU STATIONERY SHOP\nCASH SALES INVOICE NO : CS-SA-0120436\n"
                     "DATE: 27/10/2017 17:21\nSUB-TOTAL 7.00\nTOTAL GST 0.42\nTOTAL (RM) 7.42\nCASH 50.00\nCHANGE 42.58")
    assert f == {"vendor_name": "SANYU STATIONERY SHOP", "invoice_id": "CS-SA-0120436",
                 "invoice_date": "2017-10-27", "amount": 7.42, "currency": "MYR"}, f
    f = parse_fields("Globex Corporation\nInvoice #: 10045-B\nInvoice Date: March 5, 2024\nSubtotal $1,200.00\nTotal Due $1,296.00")
    assert (f["invoice_id"], f["invoice_date"], f["amount"], f["currency"]) == ("10045-B", "2024-03-05", 1296.0, "USD"), f


def test_normalize_columns():
    raw = pd.DataFrame({"Invoice No": ["A1", "A2", ""], "Supplier": ["Acme", "Acme", "Acme"],
                        "Posting Date": ["2024-01-05", "2024-02-05", "2024-03-05"], "Total": ["$1,200.50", "99", "5"]})
    df, problems = normalize(raw)
    assert list(df["invoice_id"]) == ["A1", "A2"] and list(df["amount"]) == [1200.5, 99.0], df
    assert problems and "Skipped 1" in problems[0], problems


def test_template_imports_cleanly():
    df, problems = normalize(read_table(os.path.join(config.ROOT, "templates", "company_records_template.csv")))
    assert len(df) == 3 and not problems, problems
    assert {"invoice_id", "vendor_name", "invoice_date", "amount", "currency", "vendor_id", "po_number"} <= set(df.columns)


def test_audit_and_check():
    with tempfile.TemporaryDirectory() as tmp:
        conn = db.connect(os.path.join(tmp, "t.db"))
        days = pd.date_range("2024-01-01", periods=60, freq="7D").date.astype(str)
        history = pd.DataFrame({"invoice_id": [f"INV-{i:04d}" for i in range(60)], "vendor_name": "Acme Supplies Ltd",
                                "invoice_date": days, "amount": [1000.0 + 10 * (i % 5) for i in range(60)]})
        assert import_records(conn, normalize(history)[0]) == 60
        summary = checks.audit(conn, model_path=os.path.join(tmp, "anomaly.joblib"))
        assert summary["records"] == 60 and summary["model_trained"] and summary.get("HIGH", 0) == 0, summary

        new = pd.DataFrame([
            dict(invoice_id="INV-0003", vendor_name="Acme Supplies Ltd", invoice_date=days[3], amount=1030.0),     # exact copy
            dict(invoice_id="INV0007", vendor_name="ACME SUPPLIES", invoice_date=days[7], amount=1020.0),          # reformatted copy
            dict(invoice_id="INV-9001", vendor_name="Acme Supplies Ltd", invoice_date="2025-03-10", amount=5200.0),  # 5x usual
            dict(invoice_id="INV-9002", vendor_name="Acme Supplies Ltd", invoice_date="2025-03-17", amount=1015.0),  # normal
            dict(invoice_id="X-1", vendor_name="Brand New Vendor", invoice_date="2025-03-17", amount=300.0),       # unknown vendor
        ])
        res, related = checks.check(conn, new, model=None)  # rules only: deterministic
        v = dict(zip(res["invoice_id"], res["verdict"]))
        flags = dict(zip(res["invoice_id"], res["all_flags"]))
        assert v["INV-0003"] == "SUSPICIOUS" and "exact_dup" in flags["INV-0003"], res
        assert v["INV0007"] == "SUSPICIOUS" and "near_dup" in flags["INV0007"], res
        assert v["INV-9001"] == "SUSPICIOUS" and "overpayment" in flags["INV-9001"], res
        assert v["INV-9002"] == "OK", res
        assert "New vendor" in res.loc[res["invoice_id"] == "X-1", "reasons"].item(), res
        assert set(related["new_id"]) == {-1, -2}, related
        conn.close()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
