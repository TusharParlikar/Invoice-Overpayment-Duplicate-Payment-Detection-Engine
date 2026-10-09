"""
Speed on 100,000 synthetic records (2,000 vendors), the numbers quoted in the README.

    python tests/speed.py
"""
import os
import sys
import tempfile
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import checks, db, importer

N, VENDORS = 100_000, 2_000

rng = np.random.default_rng(1)
# Varied names: near-duplicate matching groups vendors by their first 3 letters, so names that all
# start alike would put every record in one group and measure the worst case, not a typical company
names = ["".join(rng.choice(list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"), 7)) + " Supplies Ltd" for _ in range(VENDORS)]
v = rng.integers(0, VENDORS, N)
records = pd.DataFrame({
    "invoice_id": [f"INV-{i:07d}" for i in range(N)],
    "vendor_name": [names[x] for x in v],
    "invoice_date": (pd.Timestamp("2023-01-01") + pd.to_timedelta(rng.integers(0, 700, N), "D")).date.astype(str),
    "amount": np.round(rng.lognormal(7, 0.6, N), 2),
    "currency": "USD",
})


def timed(label, fn):
    t = time.perf_counter()
    fn()
    print(f"{label:<22}{time.perf_counter() - t:6.2f} s")


with tempfile.TemporaryDirectory() as tmp:
    conn = db.connect(os.path.join(tmp, "speed.db"))
    timed("import 100,000", lambda: importer.import_records(conn, records))
    timed("full audit", lambda: checks.audit(conn))
    timed("check 1 invoice", lambda: checks.check(conn, records.sample(1, random_state=2).assign(invoice_id="INV9999999")))
    timed("check batch of 50", lambda: checks.check(conn, records.sample(50, random_state=3)))
    conn.close()
