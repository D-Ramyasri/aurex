"""
Database layer and connection pool for Payment Gateway Test API.
Provides a real SQLite database with a real connection pool, concurrency controls,
and transaction management.
"""

import os
import sqlite3
import logging
import threading
import time
from pathlib import Path
from typing import Optional, Generator
from contextlib import contextmanager

logger = logging.getLogger("payment-gateway-test.database")

CURRENT_DIR = Path(__file__).resolve().parent
DATA_DIR = CURRENT_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "payments.db"


class ConnectionPoolTimeoutError(Exception):
    """Raised when a database connection cannot be acquired within the timeout period."""
    pass


class DatabasePool:
    """
    A real database connection pool for SQLite.
    Maintains a fixed pool of connections with real concurrency controls,
    connection timeouts, and exhaustion tracking.
    """

    def __init__(self, db_path: Path, max_connections: int = 5, acquire_timeout: float = 2.0):
        self.db_path = db_path
        self.max_connections = max_connections
        self.acquire_timeout = acquire_timeout
        self._lock = threading.RLock()
        self._available_connections: list[sqlite3.Connection] = []
        self._all_connections: list[sqlite3.Connection] = []
        self._active_count = 0
        self._semaphore = threading.Semaphore(max_connections)
        self._is_initialized = False

    def initialize(self):
        """Initializes the database schema and seeds base merchant accounts."""
        with self._lock:
            if self._is_initialized:
                return

            # Ensure parent dir exists
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

            # Create initial connection to setup tables
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Enable WAL mode for realistic concurrent read/write
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA busy_timeout=5000;")

            # Table: Accounts / Merchants
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                account_id TEXT PRIMARY KEY,
                merchant_name TEXT NOT NULL,
                balance REAL NOT NULL DEFAULT 0.0,
                currency TEXT NOT NULL DEFAULT 'USD',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)

            # Table: Payments / Transactions
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                payment_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                amount REAL NOT NULL,
                currency TEXT NOT NULL,
                status TEXT NOT NULL,
                card_last4 TEXT NOT NULL,
                idempotency_key TEXT UNIQUE,
                error_code TEXT,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (account_id) REFERENCES accounts(account_id)
            );
            """)

            # Table: Webhook Events
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS webhook_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL,
                signature TEXT,
                retry_count INTEGER DEFAULT 0,
                error_message TEXT,
                received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                processed_at TIMESTAMP
            );
            """)

            # Seed default merchant account if missing
            cursor.execute("SELECT COUNT(*) FROM accounts WHERE account_id = 'acc_merch_001';")
            if cursor.fetchone()[0] == 0:
                cursor.execute("""
                INSERT INTO accounts (account_id, merchant_name, balance, currency, status)
                VALUES ('acc_merch_001', 'Acme Store International', 25000.00, 'USD', 'active');
                """)
                cursor.execute("""
                INSERT INTO accounts (account_id, merchant_name, balance, currency, status)
                VALUES ('acc_frozen_002', 'Suspended Merchant Ltd', 0.00, 'USD', 'frozen');
                """)

            conn.commit()
            conn.close()

            # Pre-populate pool connections
            for _ in range(self.max_connections):
                c = sqlite3.connect(str(self.db_path), check_same_thread=False)
                c.row_factory = sqlite3.Row
                self._available_connections.append(c)
                self._all_connections.append(c)

            self._is_initialized = True
            logger.info(
                "Database pool initialized for '%s' (max_connections=%d, timeout=%.1fs)",
                self.db_path.name, self.max_connections, self.acquire_timeout
            )

    @property
    def active_connections(self) -> int:
        return self._active_count

    @property
    def available_connections(self) -> int:
        with self._lock:
            return len(self._available_connections)

    def acquire(self, timeout: Optional[float] = None) -> sqlite3.Connection:
        """
        Acquires a real connection from the pool.
        Blocks up to timeout seconds. If the pool is exhausted, raises ConnectionPoolTimeoutError.
        """
        if not self._is_initialized:
            self.initialize()

        acquire_t = self.acquire_timeout if timeout is None else timeout
        acquired = self._semaphore.acquire(blocking=True, timeout=acquire_t)
        if not acquired:
            logger.error(
                "Connection pool timeout: pool='payment_db' exhausted (active=%d/%d, timeout=%.1fs). Connection request rejected.",
                self._active_count, self.max_connections, acquire_t
            )
            raise ConnectionPoolTimeoutError(
                f"Database connection pool 'payment_db' exhausted (active={self._active_count}/{self.max_connections}, acquire_timeout={acquire_t}s)"
            )

        with self._lock:
            conn = self._available_connections.pop()
            self._active_count += 1
            return conn

    def release(self, conn: sqlite3.Connection):
        """Returns an acquired connection back to the pool."""
        with self._lock:
            if conn not in self._all_connections:
                conn.close()
                return
            self._available_connections.append(conn)
            self._active_count = max(0, self._active_count - 1)
        self._semaphore.release()

    @contextmanager
    def connection(self, timeout: Optional[float] = None) -> Generator[sqlite3.Connection, None, None]:
        """Context manager to safely acquire and release a connection."""
        conn = self.acquire(timeout=timeout)
        try:
            yield conn
        finally:
            self.release(conn)

    def close_all(self):
        """Closes all connections in the pool."""
        with self._lock:
            for conn in self._all_connections:
                try:
                    conn.close()
                except Exception:
                    pass
            self._available_connections.clear()
            self._all_connections.clear()
            self._active_count = 0
            self._is_initialized = False

    def reinitialize(self):
        """Re-initializes all pool connections and releases any leaked/hung connections."""
        with self._lock:
            self.close_all()
            self.initialize()
            logger.info("Database pool reinitialized: all connections reset.")

    def scale_pool(self, new_max: int):
        """Scales pool capacity to new_max connections."""
        with self._lock:
            if new_max < 1:
                new_max = 5
            old_max = self.max_connections
            self.close_all()
            self.max_connections = new_max
            self._semaphore = threading.Semaphore(new_max)
            self.initialize()
            logger.info("Database pool scaled from %d to %d max connections.", old_max, new_max)


# Global pool instance
pool = DatabasePool(DB_PATH, max_connections=5, acquire_timeout=2.0)

