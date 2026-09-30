"""
Smoke tests for the receipt-check path. Run: python tests/test_core.py (or pytest).
Uses a throwaway database; never touches data/invoices.db or models/.
"""
import os
import sys
import tempfile

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import detection.ml_detector as ml_detector
from database.db_manager import DatabaseManager
from detection.checker import check_invoices
from intake.fields import parse_fields
from intake.importer import import_records, normalize


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


def test_check_invoices():
    with tempfile.TemporaryDirectory() as tmp:
        ml_detector.MODEL_PATH = os.path.join(tmp, "anomaly.joblib")  # isolate from any real trained model
        with DatabaseManager(os.path.join(tmp, "t.db")) as db:
            db.init_db()
            days = pd.date_range("2024-01-01", periods=30, freq="7D").date.astype(str)
            history = pd.DataFrame({
                "invoice_id": [f"INV-{i:04d}" for i in range(30)], "vendor_name": "Acme Supplies Ltd",
                "invoice_date": days, "amount": [1000.0 + 10 * (i % 5) for i in range(30)]})
            assert import_records(normalize(history)[0], db) == 30

            new = pd.DataFrame([
                dict(invoice_id="INV-0003", vendor_name="Acme Supplies Ltd", invoice_date=days[3], amount=1030.0),  # exact copy
                dict(invoice_id="INV0007", vendor_name="ACME SUPPLIES", invoice_date=days[7], amount=1020.0),       # reformatted copy
                dict(invoice_id="INV-9001", vendor_name="Acme Supplies Ltd", invoice_date="2025-01-10", amount=5200.0),  # 5x usual
                dict(invoice_id="INV-9002", vendor_name="Acme Supplies Ltd", invoice_date="2025-01-17", amount=1015.0),  # normal
                dict(invoice_id="X-1", vendor_name="Brand New Vendor", invoice_date="2025-01-17", amount=300.0),    # unknown vendor
            ])
            res, related = check_invoices(new, db)
            v = dict(zip(res["invoice_id"], res["verdict"]))
            flags = dict(zip(res["invoice_id"], res["all_flags"]))
            assert v["INV-0003"] == "SUSPICIOUS" and "exact_dup" in flags["INV-0003"], res
            assert v["INV0007"] == "SUSPICIOUS" and "near_dup" in flags["INV0007"], res
            assert v["INV-9001"] == "SUSPICIOUS" and "overpayment" in flags["INV-9001"], res
            assert v["INV-9002"] == "OK", res
            assert "New vendor" in res.loc[res["invoice_id"] == "X-1", "reasons"].item(), res
            assert set(related["new_id"]) == {-1, -2}, related


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
