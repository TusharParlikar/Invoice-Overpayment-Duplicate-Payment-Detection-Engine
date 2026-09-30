"""
Pull invoice fields (vendor, invoice number, date, total, currency) out of receipt text.
Heuristic by design: it pre-fills the review form, and the user confirms before a check runs.
"""
import re

import pandas as pd

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
_INVOICE_ID_RE = re.compile(
    r"\b(?:tax\s+)?(?:invoice|inv|receipt|rcpt|bill|doc(?:ument)?|ref(?:erence)?|trans(?:action)?|order|cs|slip)"
    r"\s*(?:no|num|number|#|id)?\s*[.:#]?\s*[:#]?\s*([A-Z0-9][A-Z0-9\-/]{2,})", re.I)
_VENDOR_SKIP = re.compile(
    r"^(tax\s+invoice|invoice|receipt|official\s+receipt|cash\s+(sale|bill)|bill|welcome|thank)|"
    r"reg(istration)?\.?\s*no|gst\s*(id|no|reg)|\btel\b|\bfax\b|tax\s*id", re.I)
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
    for m in _INVOICE_ID_RE.finditer(text):
        candidate = m.group(1)
        if any(c.isdigit() for c in candidate) and not any(rx.fullmatch(candidate) for rx in _DATE_RES):
            return candidate.strip("-/")
    return None


def parse_vendor(lines: list[str]) -> str | None:
    """Receipts put the seller's name at the top. Prefer a top line that looks like a business name
    (handwritten notes or a cashier's name can sit above it); else the first line with real words."""
    top = [ln.strip() for ln in lines[:8] if sum(c.isalpha() for c in ln) >= 3 and not _VENDOR_SKIP.search(ln)]
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
