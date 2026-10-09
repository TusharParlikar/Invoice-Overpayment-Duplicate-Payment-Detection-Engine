"""
Run: python tests/test_engine.py   (or pytest)
Uses a throwaway database; never touches data/ or models/.
"""
import os
import sys
import tempfile

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import checks, config, db
from engine.importer import detect_dayfirst, import_records, merge_split_vendors, normalize, read_table
from engine.receipts import parse_fields, parse_invoice_id


def test_parse_fields():
    f = parse_fields("TAX INVOICE\nSANYU STATIONERY SHOP\nCASH SALES INVOICE NO : CS-SA-0120436\n"
                     "DATE: 27/10/2017 17:21\nSUB-TOTAL 7.00\nTOTAL GST 0.42\nTOTAL (RM) 7.42\nCASH 50.00\nCHANGE 42.58")
    assert f == {"vendor_name": "SANYU STATIONERY SHOP", "invoice_id": "CS-SA-0120436",
                 "invoice_date": "2017-10-27", "amount": 7.42, "currency": "MYR"}, f
    f = parse_fields("Globex Corporation\nInvoice #: 10045-B\nInvoice Date: March 5, 2024\nSubtotal $1,200.00\nTotal Due $1,296.00")
    assert (f["invoice_id"], f["invoice_date"], f["amount"], f["currency"]) == ("10045-B", "2024-03-05", 1296.0, "USD"), f
    # Layouts from the receipt dataset: a label word on the line above, the number a line below its label,
    # a space read after a dash, an order number above the bill number, an address line above the number
    assert parse_invoice_id("TAX INVOICE\nINV NO.: 1187070") == "1187070"
    assert parse_invoice_id("DOC NO. :\nSO00022185\nTABLE") == "SO00022185"
    assert parse_invoice_id("ORDER#: 116455\nBILL#: V001- 515592") == "V001-515592"
    assert parse_invoice_id("NO: 1-1&2 GROUND FLOOR,\nNO. : CS-20322") == "CS-20322"
    assert parse_fields("LOT 3, JALAN PELABUR 23/1,\nKEDAI ABC\nTOTAL 5.00")["vendor_name"] == "KEDAI ABC"


def test_date_order():
    assert detect_dayfirst(pd.Series(["03/01/2024", "13/01/2024"])) == (True, None)
    assert detect_dayfirst(pd.Series(["03/01/2024", "01/13/2024"])) == (False, None)
    assert detect_dayfirst(pd.Series(["2024-01-13"])) == (False, None)
    dayfirst, warning = detect_dayfirst(pd.Series(["03/01/2024", "04/02/2024"]))
    assert not dayfirst and "month-first" in warning
    df, _ = normalize(pd.DataFrame({"invoice": ["A", "B"], "vendor": "V", "date": ["03/01/2024", "13/01/2024"], "amount": 1}))
    assert list(df["invoice_date"]) == ["2024-01-03", "2024-01-13"], df


def test_csv_formulas_are_neutralized():
    out = checks.to_csv(pd.DataFrame({"vendor_name": ["=HYPERLINK(1)", "@SUM(A1)", "Acme"], "amount": [-5.0, 1.0, 2.0]}))
    assert out.splitlines()[1:] == ["'=HYPERLINK(1),-5.0", "'@SUM(A1),1.0", "Acme,2.0"], out


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
        summary = checks.audit(conn)
        assert summary["records"] == 60 and summary.get("HIGH", 0) == 0 and summary.get("MEDIUM", 0) == 0, summary
        assert import_records(conn, normalize(history)[0]) == 0  # importing the same file again adds nothing

        new = pd.DataFrame([
            dict(invoice_id="INV-0003", vendor_name="Acme Supplies Ltd", invoice_date=days[3], amount=1030.0),     # exact copy
            dict(invoice_id="INV0007", vendor_name="ACME SUPPLIES", invoice_date=days[7], amount=1020.0),          # reformatted copy
            dict(invoice_id="INV-9001", vendor_name="Acme Supplies Ltd", invoice_date="2025-03-10", amount=5200.0),  # 5x usual
            dict(invoice_id="INV-9002", vendor_name="Acme Supplies Ltd", invoice_date="2025-03-17", amount=1015.0),  # normal
            dict(invoice_id="X-1", vendor_name="Brand New Vendor", invoice_date="2025-03-17", amount=300.0),       # unknown vendor
            dict(invoice_id="INV-0011", vendor_name="Acme Supplies Ltd", invoice_date=days[11], amount=1070.0),    # re-billed +7%
            dict(invoice_id="INV 0012", vendor_name="THE ACME SUPPLIES", invoice_date=days[12], amount=1020.0),    # "THE" + retyped
        ])
        res, related = checks.check(conn, new)
        v = dict(zip(res["invoice_id"], res["verdict"]))
        flags = dict(zip(res["invoice_id"], res["flags"]))
        assert v["INV-0003"] == "SUSPICIOUS" and "exact_dup" in flags["INV-0003"], res
        assert v["INV0007"] == "SUSPICIOUS" and "near_dup" in flags["INV0007"], res
        assert v["INV-9001"] == "SUSPICIOUS" and "overpayment" in flags["INV-9001"], res
        assert v["INV-9002"] == "OK", res
        assert "New vendor" in res.loc[res["invoice_id"] == "X-1", "reasons"].item(), res
        assert v["INV-0011"] == "REVIEW" and "same_number" in flags["INV-0011"], res
        assert "Same invoice number as record #12" in res.loc[res["invoice_id"] == "INV-0011", "reasons"].item(), res
        assert v["INV 0012"] == "SUSPICIOUS" and "near_dup" in flags["INV 0012"], res
        assert set(related["new_id"]) == {-1, -2, -6, -7}, related
        checks.log_checks(conn, res, source="test", checked_by="asha")
        assert set(db.query(conn, "SELECT checked_by FROM receipt_checks")["checked_by"]) == {"asha"}

        # The vendor's next invoice (sequential number, same amount, a week later) is not a duplicate
        nxt = pd.DataFrame([dict(invoice_id="INV-0060", vendor_name="Acme Supplies Ltd", invoice_date=str(
            pd.Timestamp(str(days[-1])) + pd.Timedelta(days=7))[:10], amount=1000.0)])
        assert checks.check(conn, nxt)[0]["verdict"].item() == "OK"

        # Schema defaults apply to columns the file doesn't have
        assert db.query(conn, "SELECT DISTINCT payment_status FROM invoices")["payment_status"].tolist() == ["pending"]

        # A misspelled vendor gets a suggestion; a currency without a rate is called out
        odd = pd.DataFrame([dict(invoice_id="Z-1", vendor_name="Acme Supply House", invoice_date="2025-03-17",
                                 amount=50.0, currency="XYZ")])
        reasons = checks.check(conn, odd)[0]["reasons"].item()
        assert "Did you mean 'Acme Supplies Ltd'" in reasons and "No exchange rate for XYZ" in reasons, reasons

        # One vendor under two generated IDs (made before name normalization improved) is merged at audit
        conn.execute("INSERT INTO vendors (vendor_id, vendor_name) VALUES ('V-ACME-SUPPLIES-LTD-OLD', 'ACME SUPPLIES (M) LTD')")
        conn.execute("UPDATE invoices SET vendor_id = 'V-ACME-SUPPLIES-LTD-OLD' WHERE invoice_id = 'INV-0059'")
        conn.commit()
        assert merge_split_vendors(conn) == 1
        assert db.query(conn, "SELECT DISTINCT vendor_id FROM invoices")["vendor_id"].tolist() == ["V-ACME-SUPPLIES"]

        path = db.backup(conn, folder=os.path.join(tmp, "backups"))
        with db.connect(path) as copy:
            assert db.count_invoices(copy) == 60
        copy.close()
        conn.close()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
