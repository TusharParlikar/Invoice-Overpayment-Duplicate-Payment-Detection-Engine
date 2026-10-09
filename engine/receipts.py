"""
Read an uploaded receipt (photo, scan or PDF) and pull out its invoice fields.

OCR runs locally with RapidOCR (pip-only, no system Tesseract). PDFs use their text layer, falling back
to OCR for scanned PDFs. Field parsing is heuristic: it pre-fills a form the user confirms before a check.
"""
import io
import re
import threading
from functools import cache

import numpy as np
import pandas as pd
from PIL import Image

PDF_MIN_TEXT_CHARS = 20  # below this a PDF page set is treated as a scan and OCR'd


_engine_lock = threading.Lock()


@cache
def _load():
    from rapidocr import RapidOCR  # slow import + model load (20-30 s): once per process
    return RapidOCR()


def load_engine():
    with _engine_lock:  # the app preloads it in a background thread; an early upload waits instead of loading twice
        return _load()


def ocr_image(img: Image.Image) -> str:
    """OCR an image, re-assembling detected text boxes into reading-order lines."""
    result = load_engine()(np.asarray(img.convert("RGB"))[:, :, ::-1])  # RapidOCR expects BGR like OpenCV
    if result.boxes is None:
        return ""
    # Boxes come per text fragment; "TOTAL" and "12.50" on one printed line are often separate.
    # Group fragments whose vertical centres are within half a line height, then order left to right.
    items = sorted(zip(result.boxes, result.txts), key=lambda bt: bt[0][:, 1].mean())
    lines, cur, cur_y, cur_h = [], [], 0.0, 0.0
    for box, txt in items:
        y, h = box[:, 1].mean(), np.ptp(box[:, 1])
        if cur and abs(y - cur_y) > max(cur_h, h) / 2:
            lines.append(cur)
            cur = []
        if not cur:
            cur_y, cur_h = y, h
        cur.append((box[:, 0].min(), txt))
    lines.append(cur)
    return "\n".join(" ".join(t for _, t in sorted(line)) for line in lines)


def extract(file_name: str, data: bytes) -> tuple[str, Image.Image | None, bool]:
    """Return (text, first-page image, tamper_checkable) for an uploaded image or PDF.

    Only an uploaded image file keeps the pixel-level traces the tamper model reads. A PDF page
    is re-rendered (resampled), which erases them and caused false tamper flags, so PDFs,
    scanned or digital, are OCR'd/read but never tamper-scored.
    """
    if file_name.lower().endswith(".pdf"):
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(data)
        pages = [pdf[i] for i in range(len(pdf))]
        text = "\n".join(p.get_textpage().get_text_range() for p in pages)
        first = pages[0].render(scale=2).to_pil() if pages else None
        if len(text.strip()) < PDF_MIN_TEXT_CHARS:  # scanned PDF: no text layer
            text = "\n".join(ocr_image(p.render(scale=2).to_pil()) for p in pages)
        return text, first, False

    img = Image.open(io.BytesIO(data))
    img.load()
    return ocr_image(img), img, True


# ── Field parsing ──────────────────────────────────────────────────────────

_AMOUNT_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:,\d{3})+\.\d{2}|\d+\.\d{2})(?![\d])")
_TOTAL_WORDS = re.compile(r"total|amount\s*due|balance\s*due|amount\s*payable", re.I)
_NOT_FINAL_TOTAL = re.compile(
    r"sub\s*-?\s*total|total\s*(qty|quantity|items?|discount|savings|tax|gst|sst|vat)|"
    r"(tax|gst|sst|vat)\s*total|total\s*excl", re.I)
_DATE_RES = [
    re.compile(r"\b(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})\b"),                        # 2024-03-12
    re.compile(r"\b(\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})\b"),                      # 12/03/2024, 12-03-24
    re.compile(r"\b(\d{1,2}\s*[-/ ]?\s*[A-Za-z]{3,9}\.?\s*[-/ ,]?\s*\d{2,4})\b"),  # 12 Mar 2024
    re.compile(r"\b([A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})\b"),                 # March 12, 2024
]
# Lookaheads, so every position is tried: a consumed non-match ("TAX INVOICE" over a line break) used to hide
# the real "INV NO.: 1187070" after it. Label, colon and number are often on separate lines. The number starts
# after a separator ("CS00031383" keeps its prefix); one followed by "," or "&" is an address ("NO: 1-1&2 ...").
# The document's own number (invoice, receipt, bill) wins over order, slip and terminal numbers; a bare "NO :"
# or a line starting with ":" (its label lines above) is the last resort.
_ID_LABELS = [r"\b(?:tax\s+)?(?:invoice|inv|receipt|rcpt|bill|doc(?:ument)?|c/n)[ \t\-]*(?:no|num|number|#|id)?",
              r"\b(?:ref(?:erence)?|trans(?:action)?|trn|order|cs|slip|cb|check|chk|ticket)[ \t\-]*(?:no|num|number|#|id)?"
              r"|^[ \t]*no[ \t]*\.?(?=\s*[:#])|^[ \t]*(?=:)"]
_INVOICE_ID_RES = [re.compile(rf"(?=(?:{labels})\s*[.:#]?\s*[:#]?\s*(?<![A-Z0-9])([A-Z0-9](?:[A-Z0-9/]|-[ \t]?){{2,}})"
                              r"(?![A-Z0-9\-/]|[ \t]*[,&]))", re.I | re.M) for labels in _ID_LABELS]
_VENDOR_SKIP = re.compile(
    r"^(tax\s+invoice|invoice|receipt|official\s+receipt|cash\s+(sale|bill)|bill|welcome|thank)|"
    r"reg(istration)?\.?\s*no|\bco(mpany)?\.?\s*no|gst\s*(id|no|reg)|\btel\b|\bfax\b|tax\s*id|:|"
    r"^(lot|no)\b\W*\d|\bjalan\b|\bjln\b|\b\d{5}\b", re.I)  # addresses: "LOT 3, JALAN PELABUR 23/1,", postcodes
_BUSINESS = re.compile(
    r"\b(sdn\.?\s*bhd|bhd|s/b|enterprise|trading|ltd|limited|llc|l\.l\.c|inc|corp(oration)?|co\.|company|gmbh|"
    r"pvt|plt|llp|store|stores|mart|supermarket|restaurant|cafe|hardware|pharmacy|services|industries|holdings)\b", re.I)
_CURRENCIES = [
    ("MYR", r"\bRM\b|\bMYR\b"), ("INR", r"₹|\bINR\b|\bRs\.?\s"), ("EUR", r"€|\bEUR\b"),
    ("GBP", r"£|\bGBP\b"), ("USD", r"\$|\bUSD\b"),
]


def _amounts(s: str) -> list[float]:
    return [float(a.replace(",", "")) for a in _AMOUNT_RE.findall(s)]


def parse_amount(lines: list[str]) -> float | None:
    """Largest amount on a 'total'-like line (or the line after it); else the largest amount seen."""
    totals = []
    for i, line in enumerate(lines):
        if _TOTAL_WORDS.search(line) and not _NOT_FINAL_TOTAL.search(line):
            totals += _amounts(line) or (_amounts(lines[i + 1]) if i + 1 < len(lines) else [])
    if totals:
        return max(totals)
    everything = _amounts("\n".join(lines))
    return max(everything) if everything else None


def parse_date(text: str, dayfirst: bool = True) -> str | None:
    """First date-looking string in the text, as ISO yyyy-mm-dd."""
    for rx in _DATE_RES:
        for m in rx.finditer(text):
            d = pd.to_datetime(m.group(1), dayfirst=dayfirst and not rx.pattern.startswith(r"\b(\d{4}"),
                               errors="coerce")
            if pd.notna(d) and 1990 <= d.year <= 2100:
                return d.date().isoformat()
    return None


def parse_invoice_id(text: str) -> str | None:
    for id_re in _INVOICE_ID_RES:
        for m in id_re.finditer(text):
            candidate = re.sub(r"\s", "", m.group(1)).strip("-/")  # "V001- 515592": a space read after a dash
            if any(c.isdigit() for c in candidate) and not any(rx.fullmatch(candidate) for rx in _DATE_RES):
                return candidate
    return None


def parse_vendor(lines: list[str]) -> str | None:
    """Receipts put the seller's name at the top. Prefer a top line that looks like a business name
    (handwritten notes or a cashier's name can sit above it); else the first line with real words."""
    top = [ln.strip() for ln in lines[:8]  # bracketed registration numbers don't make a line a field or address
           if sum(c.isalpha() for c in ln) >= 3 and not _VENDOR_SKIP.search(re.sub(r"\(.*?\)", "", ln))]
    return next((ln for ln in top if _BUSINESS.search(ln)), top[0] if top else None)


def parse_currency(text: str) -> str | None:
    for code, rx in _CURRENCIES:
        if re.search(rx, text, re.I):
            return code
    return None


def parse_fields(text: str, dayfirst: bool = True) -> dict:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return {
        "vendor_name": parse_vendor(lines),
        "invoice_id": parse_invoice_id(text),
        "invoice_date": parse_date(text, dayfirst),
        "amount": parse_amount(lines),
        "currency": parse_currency(text),
    }
