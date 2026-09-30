"""
Synthetic invoice and vendor data generator.
Generates 100,000+ realistic invoice records with known, injected anomalies
for duplicate and overpayment detection evaluation.
"""
import sys
import os
import random
import datetime
from typing import Tuple

import pandas as pd
import numpy as np
from faker import Faker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

fake = Faker()


def generate_name_variants(name: str):
    """Generate typical ERP formatting variations for vendor names."""
    variants = [name]
    variants.append(name.upper())
    variants.append(name.lower())

    # Abbreviations
    abbr = name.replace("Corporation", "Corp").replace("Incorporated", "Inc").replace("Company", "Co")
    if abbr != name:
        variants.append(abbr)
    else:
        variants.append(name + " Corp")

    variants.append(name + " LLC")
    variants.append(name.replace(" ", ""))
    # dict.fromkeys dedupes in insertion order; set order changes per process (hash randomization),
    # which made random.choice(variants) pick different names on every run despite RANDOM_SEED.
    return list(dict.fromkeys(variants))


def generate_all_data() -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Generate realistic vendors and invoices with ground-truth anomalies."""
    np.random.seed(config.RANDOM_SEED)
    random.seed(config.RANDOM_SEED)
    Faker.seed(config.RANDOM_SEED)

    num_vendors = 2500
    vendors = []
    for i in range(num_vendors):
        v_id = f"V-{i:04d}"
        v_name = fake.company()
        erp = random.choice(["SAP", "Oracle", "NetSuite"])
        variants = generate_name_variants(v_name)
        # Each vendor has a typical pricing tier (lognormal) and low intra-vendor variance
        base_amt = round(float(np.random.lognormal(mean=np.log(4500), sigma=1.1)), 2)
        base_amt = max(200.0, min(base_amt, 250000.0))
        base_sigma = round(float(np.random.uniform(0.10, 0.20)), 3)

        vendors.append({
            "vendor_id": v_id,
            "vendor_name": v_name,
            "erp_source": erp,
            "variants": variants,
            "base_amt": base_amt,
            "base_sigma": base_sigma,
        })

    vendors_df = pd.DataFrame([{
        "vendor_id": v["vendor_id"],
        "vendor_name": v["vendor_name"],
        "vendor_name_normalized": v["vendor_name"].lower().strip(),
        "erp_source": v["erp_source"]
    } for v in vendors])

    num_exact = int(config.NUM_RECORDS * config.EXACT_DUP_RATE)
    num_near = int(config.NUM_RECORDS * config.NEAR_DUP_RATE)
    num_over = int(config.NUM_RECORDS * config.OVERPAYMENT_RATE)
    num_rapid = int(config.NUM_RECORDS * config.RAPID_FIRE_RATE)
    num_normal = config.NUM_RECORDS - num_exact - num_near - num_over - num_rapid

    start_date = datetime.date(2024, 1, 1)
    end_date = datetime.date(2025, 12, 31)
    total_days = (end_date - start_date).days

    departments = ["Engineering", "Marketing", "Sales", "Operations", "HR", "Finance", "IT", "Legal"]
    statuses = ["paid", "pending", "approved", "processing"]

    invoices = []

    # 1. Normal Invoices
    # Distribute normal invoices across vendors
    # .tolist(): numpy scalar indexing is slow inside the per-row loop below
    vendor_indices = np.random.choice(len(vendors), size=num_normal).tolist()
    day_offsets = np.random.randint(0, total_days, size=num_normal).tolist()
    dept_choices = np.random.choice(departments, size=num_normal).tolist()
    status_choices = np.random.choice(statuses, size=num_normal).tolist()
    curr_choices = np.random.choice(["USD", "EUR", "GBP"], size=num_normal, p=[0.95, 0.03, 0.02]).tolist()

    for i in range(num_normal):
        v = vendors[vendor_indices[i]]
        v_name = random.choice(v["variants"])
        erp = v["erp_source"]

        if erp == "SAP":
            inv_id = f"SAP-{random.randint(2024, 2025)}-{random.randint(100000, 999999)}"
        elif erp == "Oracle":
            inv_id = f"ORC-{random.randint(100000, 999999)}"
        else:
            inv_id = f"NS-{random.randint(2024, 2025)}-{random.randint(100000, 999999)}"

        inv_date = start_date + datetime.timedelta(days=int(day_offsets[i]))
        due_date = inv_date + datetime.timedelta(days=random.randint(30, 60))

        # Amount according to vendor's typical baseline distribution
        noise = np.random.normal(0, v["base_sigma"])
        amount = round(float(v["base_amt"] * np.exp(noise)), 2)
        amount = max(50.0, min(amount, 500000.0))

        invoices.append({
            "invoice_id": inv_id,
            "vendor_id": v["vendor_id"],
            "vendor_name": v_name,
            "invoice_date": inv_date,
            "due_date": due_date,
            "amount": amount,
            "currency": curr_choices[i],
            "department": dept_choices[i],
            "po_number": f"PO-{random.randint(100000, 999999)}",
            "payment_status": status_choices[i],
            "erp_source": erp,
            "is_anomaly": 0,
            "anomaly_type": None,
        })

    # 2. Exact Duplicates (~1.5%)
    # Exact clone of existing normal invoice (same vendor, same ID, same amount, same date)
    exact_sample = random.choices(invoices, k=num_exact)
    for base in exact_sample:
        dup = base.copy()
        dup["is_anomaly"] = 1
        dup["anomaly_type"] = "exact_duplicate"
        invoices.append(dup)

    # 3. Near Duplicates (~1.5%)
    # Same vendor but different name variant (across ERPs), slightly varied ID, date within 1-5 days, same amount
    near_sample = random.choices([inv for inv in invoices if inv["is_anomaly"] == 0], k=num_near)
    vendors_by_id = {item["vendor_id"]: item for item in vendors}
    for base in near_sample:
        dup = base.copy()
        v = vendors_by_id[base["vendor_id"]]
        other_variants = [var for var in v["variants"] if var != base["vendor_name"]]
        if other_variants:
            dup["vendor_name"] = random.choice(other_variants)

        # Vary invoice ID format across ERP
        if "-" in dup["invoice_id"]:
            dup["invoice_id"] = dup["invoice_id"].replace("-", "")
        else:
            dup["invoice_id"] = dup["invoice_id"] + "-A"

        dup["invoice_date"] = base["invoice_date"] + datetime.timedelta(days=random.randint(1, 5))
        dup["due_date"] = dup["invoice_date"] + datetime.timedelta(days=30)
        dup["erp_source"] = random.choice(["SAP", "Oracle", "NetSuite"])
        dup["is_anomaly"] = 1
        dup["anomaly_type"] = "near_duplicate"
        invoices.append(dup)

    # 4. Overpayments (~1.5%)
    # Amount is 3.5x to 6.5x the vendor's typical baseline amount
    for _ in range(num_over):
        v = random.choice(vendors)
        v_name = random.choice(v["variants"])
        inv_date = start_date + datetime.timedelta(days=random.randint(0, total_days))
        due_date = inv_date + datetime.timedelta(days=random.randint(30, 60))

        multiplier = random.uniform(3.5, 6.5)
        amount = round(float(v["base_amt"] * multiplier), 2)

        invoices.append({
            "invoice_id": f"OVP-{random.randint(100000, 999999)}",
            "vendor_id": v["vendor_id"],
            "vendor_name": v_name,
            "invoice_date": inv_date,
            "due_date": due_date,
            "amount": amount,
            "currency": "USD",
            "department": random.choice(departments),
            "po_number": f"PO-{random.randint(100000, 999999)}",
            "payment_status": random.choice(statuses),
            "erp_source": v["erp_source"],
            "is_anomaly": 1,
            "anomaly_type": "overpayment",
        })

    # 5. Rapid-fire Invoices (~1.0%)
    # 3-5 invoices submitted by the same vendor within 24 hours on the same PO
    clusters = num_rapid // 4
    for _ in range(clusters):
        v = random.choice(vendors)
        base_d = start_date + datetime.timedelta(days=random.randint(0, total_days - 2))
        po_shared = f"PO-{random.randint(100000, 999999)}"
        dept_shared = random.choice(departments)
        cluster_size = random.randint(3, 5)

        for j in range(cluster_size):
            amt = round(float(v["base_amt"] * np.exp(np.random.normal(0, v["base_sigma"]))), 2)
            invoices.append({
                "invoice_id": f"RPD-{random.randint(100000, 999999)}",
                "vendor_id": v["vendor_id"],
                "vendor_name": random.choice(v["variants"]),
                "invoice_date": base_d,
                "due_date": base_d + datetime.timedelta(days=30),
                "amount": amt,
                "currency": "USD",
                "department": dept_shared,
                "po_number": po_shared,
                "payment_status": random.choice(statuses),
                "erp_source": v["erp_source"],
                "is_anomaly": 1,
                "anomaly_type": "rapid_fire",
            })

    invoices_df = pd.DataFrame(invoices)
    return vendors_df, invoices_df


if __name__ == "__main__":
    v_df, i_df = generate_all_data()
    print(f"Generated {len(v_df)} vendors and {len(i_df)} invoices.")
    print(i_df["anomaly_type"].value_counts(dropna=False))
