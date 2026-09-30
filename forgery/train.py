"""
Train the image-tamper model on the "Find it again!" receipts and save models/tamper.joblib.

    python -m forgery.train

Model and decision threshold are picked on the validation split; the reported numbers come
from the test split, which is never used for any choice.
"""
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import joblib
import numpy as np
from PIL import Image
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from forgery import dataset
from forgery.model import MODEL_PATH, image_features

SEED = 42


def _features(path: str) -> np.ndarray:
    with Image.open(path) as img:
        return image_features(img)


def featurize(split: str) -> tuple[np.ndarray, np.ndarray]:
    """Features for a split, cached next to the dataset (extraction is the slow part)."""
    cache = os.path.join(dataset.CACHE_DIR, f"features_{split}.npz")
    df = dataset.load_split(split)
    if os.path.exists(cache) and os.path.getmtime(cache) > os.path.getmtime(sys.modules[image_features.__module__].__file__):
        return np.load(cache)["X"], df["forged"].to_numpy()
    with ProcessPoolExecutor() as pool:
        X = np.stack(list(pool.map(_features, df["path"], chunksize=8)))
    np.savez(cache, X=X)
    return X, df["forged"].to_numpy()


def best_threshold(y: np.ndarray, p: np.ndarray) -> float:
    candidates = np.unique(p)
    return float(max(candidates, key=lambda t: f1_score(y, p >= t, zero_division=0)))


def report(name: str, y: np.ndarray, p: np.ndarray, t: float) -> dict:
    pred = p >= t
    m = {"roc_auc": roc_auc_score(y, p), "precision": precision_score(y, pred, zero_division=0),
         "recall": recall_score(y, pred, zero_division=0), "f1": f1_score(y, pred, zero_division=0),
         "forged": int(y.sum()), "total": len(y)}
    print(f"  {name:<5} ROC-AUC {m['roc_auc']:.3f} | precision {m['precision']:.3f} | recall {m['recall']:.3f} | "
          f"F1 {m['f1']:.3f} | {m['forged']} forged of {m['total']}")
    return m


def main():
    print("Extracting image features (train / val / test) ...")
    (Xtr, ytr), (Xva, yva), (Xte, yte) = (featurize(s) for s in dataset.SPLITS)

    candidates = {
        "random_forest": RandomForestClassifier(n_estimators=500, min_samples_leaf=2, class_weight="balanced_subsample",
                                                random_state=SEED, n_jobs=-1),
        "gradient_boosting": HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=15,
                                                            class_weight="balanced", random_state=SEED),
    }
    scored = {}
    for name, clf in candidates.items():
        clf.fit(Xtr, ytr)
        scored[name] = roc_auc_score(yva, clf.predict_proba(Xva)[:, 1])
        print(f"  {name:<18} validation ROC-AUC {scored[name]:.3f}")
    name = max(scored, key=scored.get)
    model = candidates[name]

    threshold = best_threshold(yva, model.predict_proba(Xva)[:, 1])
    print(f"\nChosen: {name}, threshold {threshold:.3f} (best F1 on validation)")
    report("val", yva, model.predict_proba(Xva)[:, 1], threshold)
    metrics = report("test", yte, model.predict_proba(Xte)[:, 1], threshold)

    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    joblib.dump({"model": model, "threshold": threshold, "algorithm": name, "test_metrics": metrics}, MODEL_PATH)
    print(f"\nSaved {MODEL_PATH}")


if __name__ == "__main__":
    main()
