"""
OCR, field parser and image-tamper model on real receipts, checked against tests/fixtures/expected.csv.
A change to OCR, the parser or the model that breaks any of these fails CI.

Run: python tests/test_receipts.py   (or pytest; the first run downloads the OCR models, about 30 MB)
"""
import io
import os
import sys

import pandas as pd
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import receipts, tamper

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
EXPECTED = pd.read_csv(os.path.join(FIXTURES, "expected.csv"), dtype={"invoice_id": str})


def _open(name: str) -> tuple[str, Image.Image]:
    with open(os.path.join(FIXTURES, name), "rb") as f:
        text, img, checkable = receipts.extract(name, f.read())
    assert checkable
    return text, img


def test_fields_read_from_photos():
    for r in EXPECTED.itertuples():
        f = receipts.parse_fields(_open(r.file)[0])
        got = (f["vendor_name"], f["invoice_id"], f["invoice_date"], f["amount"])
        assert got == (r.vendor_name, r.invoice_id, r.invoice_date, r.amount), (r.file, got)


def test_tamper_verdicts():
    assert tamper.available(), "models/tamper.joblib is missing or can't be loaded"
    for r in EXPECTED.itertuples():
        score, flagged, _ = tamper.tamper_score(_open(r.file)[1])
        assert flagged == bool(r.forged), (r.file, score)


def test_low_quality_jpeg_is_not_assessed():
    img = _open(EXPECTED["file"].iloc[0])[1].convert("RGB")
    for quality, assessable in ((75, False), (92, True)):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality)
        assert (tamper.cannot_assess(Image.open(buf)) is None) == assessable, quality


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
