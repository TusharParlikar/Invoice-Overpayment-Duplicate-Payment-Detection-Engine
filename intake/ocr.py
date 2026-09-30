"""
Text (and a page image) from an uploaded receipt.
Images go through RapidOCR (pip-only, no system Tesseract needed); PDFs use their
embedded text layer, falling back to OCR when the PDF is a scan.
"""
import io
from functools import cache

import numpy as np
from PIL import Image

PDF_MIN_TEXT_CHARS = 20  # below this a PDF page set is treated as a scan and OCR'd


@cache
def _engine():
    from rapidocr import RapidOCR  # slow import + model load: once per process
    return RapidOCR()


def ocr_image(img: Image.Image) -> str:
    """OCR an image, re-assembling detected text boxes into reading-order lines."""
    result = _engine()(np.asarray(img.convert("RGB"))[:, :, ::-1])  # RapidOCR expects BGR like OpenCV
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
        if len(text.strip()) >= PDF_MIN_TEXT_CHARS:
            return text, first, False
        text = "\n".join(ocr_image(p.render(scale=2).to_pil()) for p in pages)
        return text, first, False

    img = Image.open(io.BytesIO(data))
    img.load()
    return ocr_image(img), img, True
