"""
Image-tamper score for a scanned receipt, and the dataset it is trained on.

Classical document forensics, no deep learning: an edited region (pasted digits, a retyped total)
usually carries compression and noise statistics that differ from the rest of the scan. Two maps
expose that, error-level analysis (re-save as JPEG, diff) and a noise residual (image minus its
median filter), summarized per 32x32 block over text areas only. A logistic regression on those
17 numbers gives the probability.

Training data: "Find it again!" (ICDAR 2023, L3i, University of La Rochelle), 988 real scanned
receipts from SROIE, 163 of them realistically forged. Published for research use.
https://l3i-share.univ-lr.fr/2023Finditagain/index.html
"""
import io
import os
import urllib.request
import zipfile
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from functools import cache

import joblib
import numpy as np
import pandas as pd
from PIL import Image, ImageFilter

from engine import config

BLOCK = 32
INK_STD = 12.0        # grayscale std above which a block holds printed content, not blank paper
ELA_QUALITY = 90
MAX_SIDE = 2500       # bigger scans are downscaled (keeps features comparable and fast)
FEATURE_VERSION = 1   # bump when image_features changes, to invalidate cached training features

DATASET_URL = "https://l3i-share.univ-lr.fr/2023Finditagain/findit2.zip"
# Outside the repo (and outside synced folders, which can mangle a 670 MB file mid-sync)
CACHE_DIR = os.environ.get("INVOICE_DATA_DIR", os.path.join(os.path.expanduser("~"), ".cache", "invoice-engine"))
DATASET_DIR = os.path.join(CACHE_DIR, "findit2")


# ── Scoring ────────────────────────────────────────────────────────────────

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
    return joblib.load(config.TAMPER_MODEL_PATH) if os.path.exists(config.TAMPER_MODEL_PATH) else None


def available() -> bool:
    return _bundle() is not None


def tamper_score(img: Image.Image) -> tuple[float, bool] | None:
    """(probability the image was edited, flagged at the validated threshold), or None if no model."""
    b = _bundle()
    if b is None:
        return None
    p = float(b["model"].predict_proba(image_features(img).reshape(1, -1))[0, 1])
    return p, p >= b["threshold"]


# ── Dataset ────────────────────────────────────────────────────────────────

def _download(url: str, dest: str, connections: int = 32, retries: int = 20):
    """The server throttles each connection, so download as parallel byte ranges, resuming each on error."""
    size = int(urllib.request.urlopen(urllib.request.Request(url, method="HEAD")).headers["Content-Length"])
    step = -(-size // connections)
    with open(dest, "wb") as f:
        f.truncate(size)

    def fetch(start: int):
        end = min(start + step, size) - 1
        pos = start
        for _ in range(retries):
            try:
                req = urllib.request.Request(url, headers={"Range": f"bytes={pos}-{end}"})
                with urllib.request.urlopen(req, timeout=60) as r, open(dest, "r+b") as f:
                    f.seek(pos)
                    while block := r.read(1 << 16):
                        f.write(block)
                        pos += len(block)
                if pos > end:
                    return
            except OSError:
                pass
        raise RuntimeError(f"download failed for bytes {start}-{end}")

    print(f"Downloading {size / 1e6:.0f} MB to {dest} ...")
    with ThreadPoolExecutor(connections) as pool:
        list(pool.map(fetch, range(0, size, step)))


def ensure_dataset() -> str:
    """Download (~670 MB) and extract once; returns the dataset folder."""
    if os.path.exists(os.path.join(DATASET_DIR, "test.txt")):
        return DATASET_DIR
    os.makedirs(CACHE_DIR, exist_ok=True)
    zpath = os.path.join(CACHE_DIR, "findit2.zip")
    if not (os.path.exists(zpath) and zipfile.is_zipfile(zpath)):
        _download(DATASET_URL, zpath + ".part")
        os.replace(zpath + ".part", zpath)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(CACHE_DIR, [n for n in z.namelist() if n.startswith("findit2/")])
    return DATASET_DIR


def load_split(split: str) -> pd.DataFrame:
    """image path, text path, forged (0/1) for the official train / val / test split."""
    root = ensure_dataset()
    df = pd.read_csv(os.path.join(root, f"{split}.txt"))
    df["path"] = [os.path.join(root, split, name) for name in df["image"]]
    df["text_path"] = df["path"].str.replace(r"\.png$", ".txt", regex=True)
    df["forged"] = df["forged"].astype(int)
    df = df[df["path"].map(os.path.exists)]  # val.txt lists one image the archive doesn't contain
    return df[["image", "path", "text_path", "forged"]].reset_index(drop=True)


def demo_history() -> pd.DataFrame:
    """Company-history rows from the genuine receipts' transcriptions, to try the app without real data."""
    from engine.receipts import parse_fields
    rows = []
    for split in ("train", "val"):
        for r in load_split(split).query("forged == 0").itertuples():
            with open(r.text_path, encoding="utf-8", errors="replace") as f:
                fields = parse_fields(f.read())
            # Real records always carry an invoice number; receipts where none could be read are left out
            # (stand-ins like the file name are sequential and would look like near-duplicates).
            if all(fields[k] for k in ("vendor_name", "invoice_id", "invoice_date", "amount")):
                fields["currency"] = fields["currency"] or "MYR"
                rows.append(fields)
    return pd.DataFrame(rows)


# ── Training ───────────────────────────────────────────────────────────────

def _features(path: str) -> np.ndarray:
    with Image.open(path) as img:
        return image_features(img)


def _featurize(split: str) -> tuple[np.ndarray, np.ndarray]:
    """Features for a split, cached next to the dataset (extraction takes a few minutes)."""
    df = load_split(split)
    path = os.path.join(CACHE_DIR, f"features_{split}_v{FEATURE_VERSION}.npz")
    if not os.path.exists(path):
        with ProcessPoolExecutor(min(4, os.cpu_count() or 1)) as pool:  # each worker holds a full-size scan
            np.savez(path, X=np.stack(list(pool.map(_features, df["path"], chunksize=8))))
    return np.load(path)["X"], df["forged"].to_numpy()


def train() -> dict:
    """Train on train+val, pick the threshold from cross-validated predictions, report on the untouched test split.

    A random forest scored 0.999 ROC-AUC on its own training data but 0.73 on unseen receipts: it
    memorized ~600 examples. Regularized forests and this logistic regression reach the same accuracy
    on unseen data, and the logistic regression shows almost no train/test gap.
    """
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score
    from sklearn.model_selection import StratifiedKFold, cross_val_predict, cross_val_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    print("Extracting image features ...")
    (Xtr, ytr), (Xva, yva), (Xte, yte) = (_featurize(s) for s in ("train", "val", "test"))
    X, y = np.vstack([Xtr, Xva]), np.concatenate([ytr, yva])

    model = make_pipeline(StandardScaler(), LogisticRegression(C=0.3, class_weight="balanced", max_iter=2000))
    folds = StratifiedKFold(5, shuffle=True, random_state=42)
    cv_auc = cross_val_score(model, X, y, cv=folds, scoring="roc_auc")
    oof = cross_val_predict(model, X, y, cv=folds, method="predict_proba")[:, 1]
    threshold = float(max(np.unique(oof), key=lambda t: f1_score(y, oof >= t, zero_division=0)))
    model.fit(X, y)

    p_test = model.predict_proba(Xte)[:, 1]
    metrics = {"train_roc_auc": roc_auc_score(y, model.predict_proba(X)[:, 1]),
               "cv_roc_auc": cv_auc.mean(), "cv_roc_auc_std": cv_auc.std(),
               "test_roc_auc": roc_auc_score(yte, p_test),
               "test_precision": precision_score(yte, p_test >= threshold, zero_division=0),
               "test_recall": recall_score(yte, p_test >= threshold, zero_division=0),
               "test_f1": f1_score(yte, p_test >= threshold, zero_division=0),
               "threshold": threshold, "train_size": len(y), "test_size": len(yte), "test_forged": int(yte.sum())}
    os.makedirs(os.path.dirname(config.TAMPER_MODEL_PATH), exist_ok=True)
    joblib.dump({"model": model, "threshold": threshold, "algorithm": "logistic_regression", "metrics": metrics},
                config.TAMPER_MODEL_PATH)
    return metrics
