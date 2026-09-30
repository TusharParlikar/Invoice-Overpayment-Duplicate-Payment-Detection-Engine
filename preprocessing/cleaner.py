import sys
import os
import re
import pandas as pd
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)

def normalize_vendor_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    name = name.lower().strip()
    name = re.sub(r'[.,]', '', name)
    suffixes = [r'\bllc\b', r'\binc\b', r'\bincorporated\b', r'\bcorp\b', 
                r'\bcorporation\b', r'\bltd\b', r'\blimited\b', r'\bco\b', r'\bcompany\b']
    for suffix in suffixes:
        name = re.sub(suffix, '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name

def standardize_dates(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if 'invoice_date' in df.columns:
        df['invoice_date'] = pd.to_datetime(df['invoice_date'])
    if 'due_date' in df.columns:
        df['due_date'] = pd.to_datetime(df['due_date'])
    return df

def normalize_currency(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    rates = {'USD': 1.0, 'EUR': 1.08, 'GBP': 1.27}
    
    def convert_to_usd(row):
        curr = row.get('currency', 'USD')
        amt = row.get('amount', 0)
        return amt * rates.get(curr, 1.0)
        
    if 'currency' in df.columns and 'amount' in df.columns:
        df['amount_usd'] = df.apply(convert_to_usd, axis=1)
    return df

def clean_invoice_data(df: pd.DataFrame) -> pd.DataFrame:
    logger.info("Cleaning invoice data")
    df = standardize_dates(df)
    df = normalize_currency(df)
    
    if 'vendor_name' in df.columns:
        df['vendor_name_clean'] = df['vendor_name'].apply(normalize_vendor_name)
        
    df.fillna({'amount': 0, 'amount_usd': 0}, inplace=True)
    return df
