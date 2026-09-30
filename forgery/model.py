"""
Image-tampering score for a scanned receipt.

Classical document forensics, no deep learning: an edited region (pasted digits, retyped
total) usually carries compression and sensor-noise statistics that differ from the rest of
the scan. Two maps expose that, error-level analysis (re-save as JPEG, diff) and a noise
residual (image minus its median filter), summarized per 32x32 block over text areas only.
A gradient-boosted classifier trained on the "Find it again!" receipts (forgery/train.py)
turns those statistics into a probability.
"""
import io
import os
from functools import cache

import joblib
import numpy as np
from PIL import Image, ImageFilter

MODEL_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "tamper.joblib")
BLOCK = 32
INK_STD = 12.0        # grayscale std above which a block contains printed content, not blank paper
ELA_QUALITY = 90
MAX_SIDE = 2500       # bigger scans are downscaled (keeps features comparable and fast)


def _block_std(m: np.ndarray) -> np.ndarray:
    h, w = (m.shape[0] // BLOCK) * BLOCK, (m.shape[1] // BLOCK) * BLOCK
    return m[:h, :w].reshape(h // BLOCK, BLOCK, w // BLOCK, BLOCK).std(axis=(1, 3))


def _summary(values: np.ndarray) -> list[float]:
    """Spread of per-block statistics; a local edit shows up as a heavy upper tail."""
    if values.size == 0:
        return [0.0] * 7
    med = np.median(values)
    mad = np.median(np.abs(values - med)) + 1e-6
    return [float(values.mean()), float(values.std()), float(med), float(np.percentile(values, 95)),
            float(values.max()), float(values.max() / (med + 1e-6)), float(np.mean(values > med + 4 * mad))]


def image_features(img: Image.Image) -> np.ndarray:
    img = img.convert("RGB")
    if max(img.size) > MAX_SIDE:
        img.thumbnail((MAX_SIDE, MAX_SIDE))
    gray = img.convert("L")
    a = np.asarray(gray, dtype=np.float32)

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=ELA_QUALITY)
    ela = np.abs(a - np.asarray(Image.open(buf).convert("L"), dtype=np.float32))
    noise = a - np.asarray(gray.filter(ImageFilter.MedianFilter(3)), dtype=np.float32)

    ink = _block_std(a) > INK_STD
    feats = [float(ink.mean()), float(ela.mean()), float(np.abs(noise).mean())]
    for m in (ela, noise):
        feats += _summary(_block_std(m)[ink])
    return np.array(feats, dtype=np.float32)


@cache
def _bundle():
    return joblib.load(MODEL_PATH) if os.path.exists(MODEL_PATH) else None


def available() -> bool:
    return _bundle() is not None


def tamper_score(img: Image.Image) -> tuple[float, bool] | None:
    """(probability the image was edited, flagged at the validated threshold), or None if no model."""
    b = _bundle()
    if b is None:
        return None
    p = float(b["model"].predict_proba(image_features(img).reshape(1, -1))[0, 1])
    return p, p >= b["threshold"]
