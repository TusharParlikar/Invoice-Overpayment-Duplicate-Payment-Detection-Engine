"""
Mock data for demonstrating the engine. Every vendor is fictional.

    python demo/make_demo_data.py

Writes into demo/:
  company_records.csv     a year of paid invoices from 8 vendors (dd/mm/yyyy dates, so date detection shows),
                          with 4 problems already hidden in it for the audit to find
  invoices_to_check.csv   16 invoices due for payment, one per case; `expected` says what the engine should answer
  receipt_duplicate.png   a receipt that copies a paid invoice (Check one invoice > Photo / scan)
  receipt_duplicate_low_quality.jpg   the same receipt as a strongly compressed JPEG (tamper check: "not checked")
"""
import os

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

OUT = os.path.dirname(os.path.abspath(__file__))
rng = np.random.default_rng(42)

# name, invoice prefix, typical amount, invoices per year, currency
VENDORS = [
    ("Northwind Office Supplies Ltd", "NWO", 1_800, 24, "INR"),
    ("Bluepeak Logistics Pvt Ltd", "BPL", 42_000, 12, "INR"),
    ("Greenleaf Catering Services", "GLC", 9_500, 52, "INR"),
    ("Apex Industrial Parts Inc", "AIP", 3_200, 12, "USD"),
    ("Sunrise Facility Management LLC", "SFM", 2_750, 12, "USD"),
    ("Metro Print & Pack", "MPP", 14_000, 18, "INR"),
    ("Orbit IT Solutions GmbH", "OIT", 1_150, 12, "EUR"),
    ("Harbor Cleaning Co", "HCC", 26_000, 12, "INR"),
]


def records() -> pd.DataFrame:
    rows = []
    for name, prefix, typical, per_year, currency in VENDORS:
        days = np.sort(rng.choice(np.arange(365), per_year, replace=False))
        for i, d in enumerate(days, start=1):
            rows.append(dict(invoice_number=f"{prefix}-2025-{i:04d}", vendor=name,
                             invoice_date=pd.Timestamp("2025-01-01") + pd.Timedelta(days=int(d)),
                             amount=round(typical * rng.lognormal(0, 0.12), 2), currency=currency,
                             po_number=f"PO-{prefix}-{1 + i // 6:03d}"))
    df = pd.DataFrame(rows)
    get = lambda num: df[df["invoice_number"] == num].iloc[0].to_dict()
    # Problems already paid, for the audit (Highest-risk records) to find
    df = pd.concat([df, pd.DataFrame([
        get("GLC-2025-0010"),                                                      # paid twice: exact duplicate
        {**get("BPL-2025-0005"), "invoice_number": "BPL20250005",                  # paid twice: retyped in another system
         "invoice_date": get("BPL-2025-0005")["invoice_date"] + pd.Timedelta(days=2)},
        {**get("MPP-2025-0009"), "invoice_number": "MPP-2025-0090", "amount": 92_500.00},   # 6.6x the usual
        {**get("NWO-2025-0012"), "vendor": "NORTHWIND OFFICE SUPPLIES"},           # same invoice, name spelled differently
    ])], ignore_index=True)
    return df.sort_values("invoice_date").reset_index(drop=True)


def to_check(rec: pd.DataFrame) -> pd.DataFrame:
    get = lambda num: rec[rec["invoice_number"] == num].iloc[0]
    day = lambda r, k=0: (r["invoice_date"] + pd.Timedelta(days=k)).date().isoformat()
    a, b, c, d = get("NWO-2025-0020"), get("SFM-2025-0007"), get("AIP-2025-0004"), get("HCC-2025-0006")
    e, f = get("OIT-2025-0003"), get("GLC-2025-0030")
    row = lambda r, **kw: {"invoice_number": r["invoice_number"], "vendor": r["vendor"], "invoice_date": day(r),
                           "amount": r["amount"], "currency": r["currency"], "po_number": "", **kw}
    rows = [
        row(a, invoice_number="NWO-2026-0001", invoice_date="2026-01-12", amount=1_850.00,
            expected="OK: a normal new invoice"),
        row(a, expected="SUSPICIOUS: exact copy of an invoice already paid"),
        row(b, invoice_number=b["invoice_number"].replace("-", ""), vendor=b["vendor"].upper(),
            expected="SUSPICIOUS: same invoice, number retyped without dashes, name in capitals"),
        row(c, invoice_number=c["invoice_number"].replace("-", " "), vendor="The " + c["vendor"],
            expected='SUSPICIOUS: "The" added to the name, number retyped with spaces'),
        row(e, invoice_number=e["invoice_number"].replace("0", "O", 1), invoice_date=day(e, 3),
            expected="SUSPICIOUS: OCR read a 0 as O, date 3 days off"),
        row(d, amount=round(d["amount"] * 1.06, 2), expected="REVIEW: same invoice number re-billed with 6% tax added"),
        row(f, invoice_number="GLC-2026-0001", invoice_date="2026-01-09", amount=61_000.00,
            expected="SUSPICIOUS: about 6x what this caterer usually charges"),
        row(f, invoice_number="GLC-2026-0002", invoice_date="2026-01-16", amount=21_500.00,
            expected="REVIEW: about 2.3x the usual, worth a second look"),
        {"invoice_number": "HCC-2026-0001", "vendor": "Harbor Cleaning Co", "invoice_date": "2026-01-20",
         "amount": 150_000.00, "currency": "INR", "po_number": "", "expected": "SUSPICIOUS: large round amount, 5.8x the usual"},
        *[{"invoice_number": f"AIP-2026-00{n}", "vendor": "Apex Industrial Parts Inc", "invoice_date": "2026-01-15",
           "amount": amt, "currency": "USD", "po_number": "PO-AIP-777",
           "expected": "SUSPICIOUS: burst, 3 invoices on one PO and day (all three are held)"}
          for n, amt in ((1, 3100.0), (2, 3350.0), (3, 2980.0))],
        {"invoice_number": "ZX-1001", "vendor": "Zenith Event Rentals", "invoice_date": "2026-01-18", "amount": 7_400.00,
         "currency": "INR", "po_number": "", "expected": "OK: new vendor, no history to compare (the reason says so)"},
        {"invoice_number": "MPP-2026-0001", "vendor": "Metro Prnt and Pack", "invoice_date": "2026-01-19", "amount": 13_800.00,
         "currency": "INR", "po_number": "", "expected": "OK: misspelled vendor, the reason suggests 'Metro Print & Pack'"},
        {"invoice_number": "EV-77", "vendor": "=HYPERLINK(\"http://example.com\",\"click\")", "invoice_date": "2026-01-21",
         "amount": 500.00, "currency": "INR", "po_number": "",
         "expected": "OK: a formula as vendor name; in the downloaded CSV it starts with ' so Excel won't run it"},
    ]
    return pd.DataFrame(rows)


def receipt_image(r: pd.Series) -> Image.Image:
    """A plain printed receipt for one record, readable by the OCR."""
    try:
        font, big = ImageFont.truetype("arial.ttf", 26), ImageFont.truetype("arialbd.ttf", 32)
    except OSError:  # not Windows: Pillow's built-in font, scaled
        font, big = ImageFont.load_default(26), ImageFont.load_default(32)
    lines = [(r["vendor"].upper(), big), ("12 Harbour Road, Mumbai 400001", font), ("TAX INVOICE", big),
             (f"Invoice No: {r['invoice_number']}", font), (f"Date: {r['invoice_date']:%d/%m/%Y}", font), ("", font),
             ("A4 paper, 80 gsm (box)      6", font), ("Toner cartridge              2", font), ("", font),
             (f"Sub Total          {r['amount'] / 1.18:,.2f}", font), (f"GST 18%            {r['amount'] - r['amount'] / 1.18:,.2f}", font),
             (f"TOTAL ({r['currency']})        {r['amount']:,.2f}", big), ("", font), ("Thank you for your business", font)]
    img = Image.new("RGB", (720, 60 + 48 * len(lines)), "white")
    draw = ImageDraw.Draw(img)
    for i, (text, f) in enumerate(lines):
        draw.text((40, 30 + 48 * i), text, fill="black", font=f)
    return img


if __name__ == "__main__":
    rec = records()
    out = rec.assign(invoice_date=rec["invoice_date"].dt.strftime("%d/%m/%Y"))
    out.rename(columns={"invoice_number": "Invoice No", "vendor": "Supplier", "invoice_date": "Invoice Date",
                        "amount": "Amount", "currency": "Currency", "po_number": "PO Number"}).to_csv(
        os.path.join(OUT, "company_records.csv"), index=False)
    to_check(rec).to_csv(os.path.join(OUT, "invoices_to_check.csv"), index=False)
    img = receipt_image(rec[rec["invoice_number"] == "NWO-2025-0020"].iloc[0])
    img.save(os.path.join(OUT, "receipt_duplicate.png"))
    img.save(os.path.join(OUT, "receipt_duplicate_low_quality.jpg"), "JPEG", quality=60)
    print(f"Wrote {len(rec)} company records, {len(to_check(rec))} invoices to check and 2 receipt images to {OUT}")
