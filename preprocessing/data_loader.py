"""
Load invoice data from SQLite into Pandas DataFrames.
"""
import sys
import os
import logging
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from database.db_manager import DatabaseManager

logger = logging.getLogger(__name__)


def load_invoices(db_path: str = None) -> pd.DataFrame:
    """Load all invoices from the database."""
    logger.info("Loading invoices from DB")
    with DatabaseManager(db_path or config.DB_PATH) as db:
        df = db.get_all_invoices()
    df["invoice_date"] = pd.to_datetime(df["invoice_date"])
    df["due_date"] = pd.to_datetime(df["due_date"])
    return df


def load_vendors(db_path: str = None) -> pd.DataFrame:
    """Load all vendors from the database."""
    logger.info("Loading vendors from DB")
    with DatabaseManager(db_path or config.DB_PATH) as db:
        df = db.execute_query("SELECT * FROM vendors")
    return df


def load_invoices_with_vendors(db_path: str = None) -> pd.DataFrame:
    """Load invoices joined with vendor info."""
    logger.info("Loading invoices with vendors from DB")
    query = """
        SELECT i.*, v.vendor_name_normalized, v.erp_source AS vendor_erp
        FROM invoices i
        LEFT JOIN vendors v ON i.vendor_id = v.vendor_id
    """
    with DatabaseManager(db_path or config.DB_PATH) as db:
        df = db.execute_query(query)
    df["invoice_date"] = pd.to_datetime(df["invoice_date"])
    df["due_date"] = pd.to_datetime(df["due_date"])
    return df
