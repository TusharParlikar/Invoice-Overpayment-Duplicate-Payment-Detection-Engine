"""
"Find it again!" receipt dataset (ICDAR 2023, University of La Rochelle): 988 real scanned
receipts from SROIE with text transcriptions, 163 of them realistically forged.
https://l3i-share.univ-lr.fr/2023Finditagain/index.html  (published for research use)

Downloaded once (~670 MB) into a local cache outside the repo (and outside OneDrive-style
synced folders, which can mangle large files mid-sync). The server throttles each connection,
so the download runs as parallel byte-range requests.

    python -m forgery.dataset --history demo_history.csv   # genuine receipts -> company-history CSV
"""
import argparse
import os
import sys
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from intake.fields import parse_fields

URL = "https://l3i-share.univ-lr.fr/2023Finditagain/findit2.zip"
CACHE_DIR = os.environ.get("INVOICE_DATA_DIR", os.path.join(os.path.expanduser("~"), ".cache", "invoice-engine"))
ROOT = os.path.join(CACHE_DIR, "findit2")
SPLITS = ("train", "val", "test")
CONNECTIONS = 32
CHUNK_RETRIES = 20


def _download(url: str, dest: str):
    size = int(urllib.request.urlopen(urllib.request.Request(url, method="HEAD")).headers["Content-Length"])
    step = -(-size // CONNECTIONS)
    with open(dest, "wb") as f:
        f.truncate(size)

    def fetch(start: int):
        end = min(start + step, size) - 1
        pos = start
        for _ in range(CHUNK_RETRIES):  # resume this chunk from where the last attempt stopped
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

    print(f"Downloading {size / 1e6:.0f} MB with {CONNECTIONS} connections to {dest} ...")
    with ThreadPoolExecutor(CONNECTIONS) as pool:
        list(pool.map(fetch, range(0, size, step)))


def ensure() -> str:
    """Download + extract once; returns the dataset folder."""
    if os.path.exists(os.path.join(ROOT, "test.txt")):
        return ROOT
    os.makedirs(CACHE_DIR, exist_ok=True)
    zpath = os.path.join(CACHE_DIR, "findit2.zip")
    if not (os.path.exists(zpath) and zipfile.is_zipfile(zpath)):
        _download(URL, zpath + ".part")
        os.replace(zpath + ".part", zpath)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(CACHE_DIR, [n for n in z.namelist() if n.startswith("findit2/")])
    return ROOT


def load_split(split: str) -> pd.DataFrame:
    """image path, text path, forged (0/1) for one split."""
    root = ensure()
    df = pd.read_csv(os.path.join(root, f"{split}.txt"))
    df["path"] = [os.path.join(root, split, name) for name in df["image"]]
    df["text_path"] = df["path"].str.replace(r"\.png$", ".txt", regex=True)
    df["forged"] = df["forged"].astype(int)
    df = df[df["path"].map(os.path.exists)]  # val.txt lists one image the archive doesn't contain
    return df[["image", "path", "text_path", "forged"]].reset_index(drop=True)


def history_from_genuine(splits=("train", "val")) -> pd.DataFrame:
    """Company-history rows from the genuine (unforged) receipts' transcriptions, for demos."""
    rows = []
    for split in splits:
        for r in load_split(split).query("forged == 0").itertuples():
            with open(r.text_path, encoding="utf-8", errors="replace") as f:
                fields = parse_fields(f.read())
            # Real records always carry an invoice number; receipts where none could be read are left out
            # (stand-in ids like the file name are sequential and would look like near-duplicates).
            if all(fields[k] for k in ("vendor_name", "invoice_id", "invoice_date", "amount")):
                fields["currency"] = fields["currency"] or "MYR"
                rows.append(fields)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--history", metavar="CSV", help="write a demo company-history CSV from genuine receipts")
    args = ap.parse_args()
    print("Dataset at", ensure())
    if args.history:
        h = history_from_genuine()
        h.to_csv(args.history, index=False)
        print(f"Wrote {len(h):,} receipts ({h['vendor_name'].nunique():,} vendors) to {args.history}. "
              f"Import: python -m intake.importer {args.history}")
