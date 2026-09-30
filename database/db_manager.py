"""
Database manager for the Invoice Detection Engine.
Handles database connections and CRUD operations.
"""
import sqlite3
import pandas as pd
import logging
import os
import sys
from contextlib import contextmanager

# Ensure project root is in path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class DatabaseManager:
    def __init__(self, db_path: str = None):
        """Initialize the DB Manager and ensure the DB directory exists."""
        self.db_path = db_path or config.DB_PATH
        
        # Ensure parent directory of db exists
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
            
        self.conn = None
        self._connect()

    def _connect(self):
        """Establish connection and configure SQLite."""
        try:
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            # 64 MB page cache: the default 2 MB thrashes while maintaining 4 indexes on 100K+ inserts (~3x slower)
            self.conn.execute("PRAGMA cache_size=-65536")
            logger.info(f"Connected to database at {self.db_path} with WAL mode enabled.")
        except sqlite3.Error as e:
            logger.error(f"Error connecting to DB: {e}")
            raise

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def init_db(self, schema_path: str = None):
        """Initialize database schema from a SQL file."""
        if not schema_path:
            schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')
        
        try:
            with open(schema_path, 'r') as file:
                schema_script = file.read()
            self.conn.executescript(schema_script)
            self.conn.commit()
            logger.info(f"Database initialized with schema from {schema_path}.")
        except Exception as e:
            logger.error(f"Error initializing DB schema: {e}")
            self.conn.rollback()
            raise

    def bulk_insert_vendors(self, vendors_df: pd.DataFrame):
        """Insert vendors, skipping vendor_ids that already exist."""
        cols = list(vendors_df.columns)
        sql = f"INSERT OR IGNORE INTO vendors ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
        try:
            with self.conn:
                self.conn.executemany(sql, vendors_df.itertuples(index=False, name=None))  # SQLite stores NaN as NULL
            logger.info(f"Upserted {len(vendors_df)} vendors.")
        except Exception as e:
            logger.error(f"Failed to insert vendors: {e}")
            raise

    def bulk_insert_invoices(self, invoices_df: pd.DataFrame):
        """Bulk insert invoices from DataFrame."""
        try:
            invoices_df.to_sql('invoices', self.conn, if_exists='append', index=False, chunksize=500)
            logger.info(f"Successfully inserted {len(invoices_df)} invoices.")
        except Exception as e:
            logger.error(f"Failed to insert invoices: {e}")
            raise

    def get_all_invoices(self) -> pd.DataFrame:
        """Fetch all invoices."""
        query = "SELECT * FROM invoices"
        return self.execute_query(query)

    def get_invoices_by_vendor(self, vendor_id: str) -> pd.DataFrame:
        """Fetch invoices for a specific vendor."""
        query = "SELECT * FROM invoices WHERE vendor_id = ?"
        return self.execute_query(query, params=(vendor_id,))

    def get_invoices_by_date_range(self, start_date: str, end_date: str) -> pd.DataFrame:
        """Fetch invoices within a specific date range."""
        query = "SELECT * FROM invoices WHERE invoice_date BETWEEN ? AND ?"
        return self.execute_query(query, params=(start_date, end_date))

    def count_invoices(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]

    def write_detection_results(self, results_df: pd.DataFrame):
        """Replace detection results with this audit's results."""
        try:
            self.conn.execute("DELETE FROM detection_results")
            results_df.to_sql('detection_results', self.conn, if_exists='append', index=False, chunksize=500)
            logger.info(f"Successfully inserted {len(results_df)} detection results.")
        except Exception as e:
            logger.error(f"Failed to write detection results: {e}")
            raise

    def write_fuzzy_matches(self, matches_df: pd.DataFrame):
        """Replace fuzzy match pairs with this audit's pairs."""
        try:
            self.conn.execute("DELETE FROM fuzzy_match_pairs")
            matches_df.to_sql('fuzzy_match_pairs', self.conn, if_exists='append', index=False, chunksize=500)
            logger.info(f"Successfully inserted {len(matches_df)} fuzzy match pairs.")
        except Exception as e:
            logger.error(f"Failed to write fuzzy matches: {e}")
            raise

    def log_receipt_check(self, row: dict):
        """Record one receipt check (see receipt_checks in schema.sql)."""
        pd.DataFrame([row]).to_sql('receipt_checks', self.conn, if_exists='append', index=False)

    def execute_query(self, query: str, params: tuple = None) -> pd.DataFrame:
        """Execute a query and return results as DataFrame."""
        try:
            return pd.read_sql_query(query, self.conn, params=params)
        except Exception as e:
            logger.error(f"Failed to execute query: {e}")
            raise

    def close(self):
        """Close the database connection."""
        if self.conn:
            self.conn.close()
            logger.info("Database connection closed.")
            self.conn = None
