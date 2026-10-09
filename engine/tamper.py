"""
Image-tamper score for a scanned receipt, and the dataset it is trained on.

Classical document forensics, no deep learning. An edited region (pasted digits, a retyped total)
carries compression, noise and edge statistics that differ from its surroundings. Every 32x32 block
with printed content is described by error-level analysis at three JPEG qualities, a noise residual
and an edge map, each raw, relative to the whole receipt and relative to its 8 neighbours. A gradient-
boosted classifier, trained on the dataset's marked forgery regions, scores each block; the receipt's
score is its most suspicious block, which the app also outlines.

Training data: "Find it again!" (ICDAR 2023, L3i, University of La Rochelle), 988 real scanned
receipts from SROIE, 163 of them realistically forged, with the edited regions marked.
Published for research use. https://l3i-share.univ-lr.fr/2023Finditagain/index.html
"""
import ast
import io
import os
import urllib.request
import zipfile
import zlib
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from functools import cache

import joblib
import numpy as np
import pandas as pd
from PIL import Image, ImageFilter

from engine import config

BLOCK = 32
INK_STD = 12.0           # grayscale std above which a block holds printed content, not blank paper
ELA_QUALITIES = (75, 90, 95)
FEATURE_VERSION = 2      # bump when block_features changes, to invalidate cached training features
JPEG_MIN_QUALITY = 85    # below this a JPEG upload is "can't assess" (see cannot_assess)

DATASET_URL = "https://l3i-share.univ-lr.fr/2023Finditagain/findit2.zip"
# Outside the repo (and outside synced folders, which can mangle a 670 MB file mid-sync)
CACHE_DIR = os.environ.get("INVOICE_DATA_DIR", os.path.join(os.path.expanduser("~"), ".cache", "invoice-engine"))
DATASET_DIR = os.path.join(CACHE_DIR, "findit2")


# ── Scoring ────────────────────────────────────────────────────────────────

def _per_block(m: np.ndarray, f) -> np.ndarray:
    h, w = (m.shape[0] // BLOCK) * BLOCK, (m.shape[1] // BLOCK) * BLOCK
    return f(m[:h, :w].reshape(h // BLOCK, BLOCK, w // BLOCK, BLOCK), axis=(1, 3))


def block_features(img: Image.Image) -> pd.DataFrame:
    """One row per 32x32 block with printed content (bx, by = block column, row).
    Images are used at their own resolution, as in training."""
    img = img.convert("RGB")
    gray = img.convert("L")
    a = np.asarray(gray, np.float32)
    maps = {}
    for q in ELA_QUALITIES:  # error-level analysis: re-save as JPEG, take the difference
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=q)
        maps[f"ela{q}"] = np.abs(a - np.asarray(Image.open(buf).convert("L"), np.float32))
    maps["noise"] = a - np.asarray(gray.filter(ImageFilter.MedianFilter(3)), np.float32)
    maps["edge"] = np.abs(np.asarray(gray.filter(ImageFilter.FIND_EDGES), np.float32))

    ink = _per_block(a, np.std) > INK_STD
    stats = {"gray_std": _per_block(a, np.std), "gray_mean": _per_block(a, np.mean)}
    for k, m in maps.items():
        stats[k + "_mean"] = _per_block(np.abs(m), np.mean)
        stats[k + "_std"] = _per_block(m, np.std)
        stats[k + "_max"] = _per_block(np.abs(m), np.max)
    cols = {}
    for k, v in stats.items():
        med = np.median(v[ink]) if ink.any() else 1.0
        padded = np.pad(v, 1, mode="edge")
        neighbours = sum(padded[i:i + v.shape[0], j:j + v.shape[1]]
                         for i in range(3) for j in range(3) if (i, j) != (1, 1)) / 8
        cols[k] = v.ravel()
        cols[k + "_rel"] = (v / (med + 1e-6)).ravel()  # vs the whole receipt
        cols[k + "_nb"] = (v - neighbours).ravel()     # vs the surrounding blocks
    df = pd.DataFrame(cols)
    by, bx = np.mgrid[0:ink.shape[0], 0:ink.shape[1]]
    df["by"], df["bx"], df["ink_share"] = by.ravel(), bx.ravel(), ink.mean()
    return df[ink.ravel()].reset_index(drop=True)


@cache
def _bundle():
    """The saved model, or None if it is missing or can't be loaded (e.g. saved by another scikit-learn)."""
    try:
        b = joblib.load(config.TAMPER_MODEL_PATH)
    except Exception:
        return None
    return b if isinstance(b, dict) and "thresholds" in b else None


def available() -> bool:
    return _bundle() is not None


def version_warning() -> str | None:
    """Set when the model was saved by a different scikit-learn than the one installed: it may score differently."""
    import sklearn
    b = _bundle()
    if b is None or b.get("sklearn_version") == sklearn.__version__:
        return None
    return (f"The image-tamper model was saved with scikit-learn {b.get('sklearn_version', '(unknown)')}, but "
            f"{sklearn.__version__} is installed, so its scores may differ. Install requirements.txt or run "
            "python cli.py train-tamper.")


# Standard JPEG luminance quantization table (ITU T.81, Annex K), scaled by quality as libjpeg does
_JPEG_LUMA = np.array([16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55, 14, 13, 16, 24, 40, 57, 69, 56,
                       14, 17, 22, 29, 51, 87, 80, 62, 18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92,
                       49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99])


def jpeg_quality(img: Image.Image) -> int | None:
    """Estimated quality (1-100) of a JPEG file, from its luminance table; None if the image isn't a JPEG."""
    tables = getattr(img, "quantization", None)
    if img.format != "JPEG" or not tables:
        return None
    table = np.sort(np.asarray(tables[min(tables)]))  # sorted: writers differ in the order they store it

    def scaled(q):
        f = 5000 / q if q < 50 else 200 - 2 * q
        return np.sort(np.clip((_JPEG_LUMA * f + 50) // 100, 1, 255))
    return min(range(1, 101), key=lambda q: np.abs(scaled(q) - table).sum())


def cannot_assess(img: Image.Image) -> str | None:
    """Why this image can't be scored reliably, or None. On the dataset's test split, JPEGs at quality 75
    hide almost every edit, so "not flagged" would mean nothing."""
    q = jpeg_quality(img)
    if q is not None and q < JPEG_MIN_QUALITY:
        return (f"not checked: the image is a JPEG saved at quality {q}, and compression that strong erases the "
                "traces the model reads. Upload the original scan or a PNG")
    return None


def tamper_score(img: Image.Image) -> tuple[float, bool, tuple[int, int, int, int] | None] | None:
    """(probability the image was edited, flagged at the threshold validated for its format, (x, y, w, h) of the
    most suspicious area), or None if no model is installed. Check cannot_assess() first."""
    b = _bundle()
    if b is None:
        return None
    blocks = block_features(img)
    if blocks.empty:
        return 0.0, False, None
    p = b["model"].predict_proba(blocks[b["features"]])[:, 1]
    top = blocks.iloc[int(p.argmax())]
    threshold = b["thresholds"]["jpeg" if img.format == "JPEG" else "scan"]
    return float(p.max()), bool(p.max() >= threshold), (int(top.bx) * BLOCK, int(top.by) * BLOCK, BLOCK, BLOCK)


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
    """image, path, text path, forged (0/1), forgery annotation for the official train / val / test split."""
    root = ensure_dataset()
    df = pd.read_csv(os.path.join(root, f"{split}.txt"))
    df["path"] = [os.path.join(root, split, name) for name in df["image"]]
    df["text_path"] = df["path"].str.replace(r"\.png$", ".txt", regex=True)
    df["forged"] = df["forged"].astype(int)
    df = df[df["path"].map(os.path.exists)]  # val.txt lists one image the archive doesn't contain
    return df.rename(columns={"forgery annotations": "annotation"})[
        ["image", "path", "text_path", "forged", "annotation"]].reset_index(drop=True)


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

# Uploads are rarely the lossless scans the dataset holds: phone photos and emailed receipts are JPEG. Each training
# receipt is also seen as a JPEG (quality 85-95), and JPEGs get their own threshold. Smaller copies were tried too:
# the model learned nothing from them (test ROC-AUC 0.54 at half size) and they lowered the threshold for scans, so a
# downscaled receipt simply can't be judged. The test split is reported under fixed versions of each condition.
TRAIN_VARIANTS = ("scan", "jpeg")
TEST_CONDITIONS = {"scan": (1.0, None), "jpeg90": (1.0, 90), "jpeg75": (1.0, 75), "half": (0.5, None)}


def _degrade(img: Image.Image, scale: float, quality: int | None) -> Image.Image:
    img = img.convert("RGB")
    if scale != 1.0:
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    if quality:
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality)
        img = Image.open(buf)
    return img


def _train_condition(image: str, variant: str) -> tuple[float, int | None]:
    """Random (scale, JPEG quality) for one training copy, fixed per receipt so cached features stay valid."""
    rng = np.random.default_rng(zlib.crc32(f"{image}|{variant}".encode()))
    if variant == "jpeg":
        return 1.0, int(rng.integers(JPEG_MIN_QUALITY, 96))
    return 1.0, None


def _edited_blocks(annotation, blocks: pd.DataFrame, scale: float = 1.0) -> np.ndarray:
    """Blocks inside regions marked as edited. "Original area: yes" marks where a copied piece came from."""
    edited = np.zeros(len(blocks), bool)
    ann = ast.literal_eval(annotation) if isinstance(annotation, str) else None
    if not isinstance(ann, dict):
        return edited
    for r in ann["regions"]:
        attrs, s = r["region_attributes"], r["shape_attributes"]
        kinds = attrs.get("Modified area", {})
        if attrs.get("Original area") != "no" or (isinstance(kinds, dict) and not any(v for k, v in kinds.items() if k != "None")):
            continue
        x0, y0 = int(s["x"] * scale) // BLOCK, int(s["y"] * scale) // BLOCK
        x1, y1 = int((s["x"] + s["width"]) * scale) // BLOCK, int((s["y"] + s["height"]) * scale) // BLOCK
        edited |= (blocks["bx"].between(x0, x1) & blocks["by"].between(y0, y1)).to_numpy()
    return edited


def _image_blocks(job: tuple[str, float, int | None]) -> pd.DataFrame:
    path, scale, quality = job
    with Image.open(path) as img:
        return block_features(_degrade(img, scale, quality))


def _blocks(split: str, variant: str) -> pd.DataFrame:
    """Labelled blocks for a split under one upload condition (a TRAIN_VARIANTS or TEST_CONDITIONS name),
    cached next to the dataset (extraction takes several minutes)."""
    path = os.path.join(CACHE_DIR, f"blocks_{split}_v{FEATURE_VERSION}.pkl" if variant == "scan"
                        else f"blocks_{split}_{variant}_v{FEATURE_VERSION}.pkl")
    if os.path.exists(path):
        return pd.read_pickle(path)
    meta = load_split(split)
    conds = [TEST_CONDITIONS[variant] if variant in TEST_CONDITIONS else _train_condition(i, variant) for i in meta["image"]]
    with ProcessPoolExecutor(min(6, os.cpu_count() or 1)) as pool:  # each worker holds a full-size scan
        parts = list(pool.map(_image_blocks, [(p, *c) for p, c in zip(meta["path"], conds)], chunksize=4))
    for r, (scale, _), b in zip(meta.itertuples(), conds, parts):
        b["edited"] = _edited_blocks(r.annotation, b, scale) if r.forged else False
        b["image"], b["forged"] = r.image, r.forged
    out = pd.concat(parts, ignore_index=True)
    out.to_pickle(path)
    return out


def _per_image(blocks: pd.DataFrame, p: np.ndarray) -> pd.DataFrame:
    """A receipt's score is its most suspicious block."""
    return blocks.assign(p=p).groupby("image").agg(score=("p", "max"), forged=("forged", "first"))


def train() -> dict:
    """Train on train+val, each receipt as a scan and as a JPEG. One threshold per format, from cross-validated
    predictions, with folds split by receipt so no copy of one receipt sits on both sides. The test split is only
    used for the final report, per upload condition."""
    import sklearn
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score
    from sklearn.model_selection import StratifiedGroupKFold

    print("Extracting block features ...")
    dev = pd.concat([_blocks(s, v).assign(receipt=lambda d: d["image"], image=lambda d, v=v: d["image"] + "|" + v)
                     for s in ("train", "val") for v in TRAIN_VARIANTS], ignore_index=True)
    features = [c for c in dev.columns if c not in ("bx", "by", "edited", "image", "receipt", "forged")]
    make = lambda: HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=31,
                                                  min_samples_leaf=50, l2_regularization=1.0,
                                                  class_weight="balanced", random_state=0)

    print("Cross-validating ...")
    oof = np.zeros(len(dev))
    folds = StratifiedGroupKFold(5, shuffle=True, random_state=0)
    for tr, va in folds.split(dev, dev["forged"], groups=dev["receipt"]):
        oof[va] = make().fit(dev.loc[tr, features], dev.loc[tr, "edited"]).predict_proba(dev.loc[va, features])[:, 1]
    cv = _per_image(dev, oof)
    best_f1 = lambda c: float(max(np.unique(c["score"]), key=lambda t: f1_score(c["forged"], c["score"] >= t)))
    thresholds = {v: best_f1(cv[cv.index.str.endswith("|" + v)]) for v in TRAIN_VARIANTS}

    model = make().fit(dev[features], dev["edited"])
    fit = _per_image(dev, model.predict_proba(dev[features])[:, 1])
    metrics = {"train_roc_auc": roc_auc_score(fit["forged"], fit["score"]),
               "cv_roc_auc": roc_auc_score(cv["forged"], cv["score"]),
               **{f"threshold_{v}": t for v, t in thresholds.items()}, "train_receipts": dev["receipt"].nunique()}
    for cond, (_, quality) in TEST_CONDITIONS.items():
        test = _blocks("test", cond)
        held = _per_image(test, model.predict_proba(test[features])[:, 1])
        flagged = held["score"] >= thresholds["jpeg" if quality else "scan"]
        genuine = held["forged"] == 0
        metrics |= {f"{cond}_roc_auc": roc_auc_score(held["forged"], held["score"]),
                    f"{cond}_recall": recall_score(held["forged"], flagged, zero_division=0),
                    f"{cond}_precision": precision_score(held["forged"], flagged, zero_division=0),
                    f"{cond}_f1": f1_score(held["forged"], flagged, zero_division=0),
                    f"{cond}_genuine_flagged": float(flagged[genuine].mean())}
    metrics |= {"test_receipts": len(held), "test_forged": int(held["forged"].sum())}
    os.makedirs(os.path.dirname(config.TAMPER_MODEL_PATH), exist_ok=True)
    joblib.dump({"model": model, "features": features, "thresholds": thresholds, "metrics": metrics,
                 "sklearn_version": sklearn.__version__}, config.TAMPER_MODEL_PATH)
    return metrics
