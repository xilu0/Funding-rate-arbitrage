import os
import sqlite3
import time
import datetime
from typing import Dict, Any, List, Optional, Tuple, Callable
from src.calculator import safe_float

DEFAULT_DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DEFAULT_DB_PATH = os.path.join(DEFAULT_DB_DIR, "funding_history.db")


class FundingHistoryStorage:
    """
    SQLite-based high-performance local storage for Perpetual Funding Rate History.
    Supports incremental syncing, offline lookup, and multi-exchange normalization.
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._ensure_db_dir()
        self.init_db()

    def _ensure_db_dir(self):
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # Enable WAL mode for high concurrency read/write performance
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def init_db(self):
        """Initializes tables and indices if not exists."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS funding_history (
                    exchange            TEXT NOT NULL,
                    symbol              TEXT NOT NULL,
                    timestamp           INTEGER NOT NULL,
                    datetime_utc        TEXT NOT NULL,
                    period_rate         REAL NOT NULL,
                    funding_interval_hr REAL NOT NULL,
                    hourly_rate         REAL NOT NULL,
                    created_at          INTEGER NOT NULL,
                    PRIMARY KEY (exchange, symbol, timestamp)
                );
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_funding_lookup 
                ON funding_history(exchange, symbol, timestamp DESC);
            """)

    def save_records(self, exchange: str, symbol: str, records: List[Dict[str, Any]]) -> int:
        """
        Saves raw records from Hyperliquid or Bybit into SQLite.
        Skips existing records using INSERT OR IGNORE.
        Returns the number of new records inserted.
        """
        if not records:
            return 0

        exchange_clean = "Hyperliquid" if "hyperliquid" in exchange.lower() or exchange.lower() == "hl" else "Bybit"
        now_ms = int(time.time() * 1000)

        # Detect interval if possible
        is_hl = (exchange_clean == "Hyperliquid")
        default_interval = 1.0 if is_hl else 8.0

        # Sort records chronologically to detect interval
        sorted_recs = sorted(
            records,
            key=lambda r: safe_float(r.get("fundingRateTimestamp") if "fundingRateTimestamp" in r else r.get("time"))
        )

        interval_hr = default_interval
        if len(sorted_recs) >= 2:
            ts0 = safe_float(sorted_recs[0].get("fundingRateTimestamp") if "fundingRateTimestamp" in sorted_recs[0] else sorted_recs[0].get("time"))
            ts1 = safe_float(sorted_recs[1].get("fundingRateTimestamp") if "fundingRateTimestamp" in sorted_recs[1] else sorted_recs[1].get("time"))
            diff_ms = abs(ts1 - ts0)
            if diff_ms > 0:
                detected = round(diff_ms / (3600.0 * 1000.0))
                if detected in [1, 2, 4, 8, 12, 24]:
                    interval_hr = float(detected)

        rows_to_insert = []
        for r in sorted_recs:
            ts_raw = safe_float(r.get("fundingRateTimestamp") if "fundingRateTimestamp" in r else r.get("time"))
            if ts_raw <= 0:
                continue
            ts = int(ts_raw)
            dt_str = datetime.datetime.fromtimestamp(ts / 1000.0, tz=datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")
            p_rate = safe_float(r.get("fundingRate"))
            h_rate = p_rate / interval_hr

            rows_to_insert.append((
                exchange_clean,
                symbol,
                ts,
                dt_str,
                p_rate,
                interval_hr,
                h_rate,
                now_ms
            ))

        if not rows_to_insert:
            return 0

        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.executemany("""
                INSERT OR IGNORE INTO funding_history 
                (exchange, symbol, timestamp, datetime_utc, period_rate, funding_interval_hr, hourly_rate, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """, rows_to_insert)
            inserted = cur.rowcount
            conn.commit()
            return inserted

    def get_latest_timestamp(self, exchange: str, symbol: str) -> Optional[int]:
        """Returns the most recent timestamp (ms) stored for this exchange and symbol."""
        exchange_clean = "Hyperliquid" if "hyperliquid" in exchange.lower() or exchange.lower() == "hl" else "Bybit"
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT MAX(timestamp) as max_ts FROM funding_history
                WHERE exchange = ? AND symbol = ?;
            """, (exchange_clean, symbol))
            row = cur.fetchone()
            if row and row["max_ts"] is not None:
                return int(row["max_ts"])
            return None

    def get_oldest_timestamp(self, exchange: str, symbol: str) -> Optional[int]:
        """Returns the oldest timestamp (ms) stored for this exchange and symbol."""
        exchange_clean = "Hyperliquid" if "hyperliquid" in exchange.lower() or exchange.lower() == "hl" else "Bybit"
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT MIN(timestamp) as min_ts FROM funding_history
                WHERE exchange = ? AND symbol = ?;
            """, (exchange_clean, symbol))
            row = cur.fetchone()
            if row and row["min_ts"] is not None:
                return int(row["min_ts"])
            return None

    def get_records(
        self,
        exchange: str,
        symbol: str,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Retrieves stored funding history records, ordered chronologically (oldest to newest).
        Returns list of dicts with standard field mapping for calculator compatibility.
        """
        exchange_clean = "Hyperliquid" if "hyperliquid" in exchange.lower() or exchange.lower() == "hl" else "Bybit"
        query = "SELECT * FROM funding_history WHERE exchange = ? AND symbol = ?"
        params: List[Any] = [exchange_clean, symbol]

        if start_time is not None and start_time > 0:
            query += " AND timestamp >= ?"
            params.append(start_time)

        if end_time is not None and end_time > 0:
            query += " AND timestamp <= ?"
            params.append(end_time)

        query += " ORDER BY timestamp ASC"

        if limit is not None and limit > 0:
            query += f" LIMIT {int(limit)}"

        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            rows = cur.fetchall()

        results = []
        for row in rows:
            ts = row["timestamp"]
            p_rate = row["period_rate"]
            h_rate = row["hourly_rate"]
            interval_hr = row["funding_interval_hr"]
            results.append({
                "exchange": row["exchange"],
                "symbol": row["symbol"],
                "coin": row["symbol"],
                "timestamp": ts,
                "time": ts,
                "fundingRateTimestamp": str(ts),
                "datetime_utc": row["datetime_utc"],
                "fundingRate": str(p_rate),
                "period_funding_rate": p_rate,
                "funding_interval_hr": interval_hr,
                "hourly_funding_rate": h_rate
            })

        return results

    def get_symbols_summary(self, exchange: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns summary statistics for all tracked symbols in the local database."""
        query = """
            SELECT 
                exchange, 
                symbol, 
                COUNT(*) as count, 
                MIN(timestamp) as min_ts, 
                MAX(timestamp) as max_ts,
                MIN(datetime_utc) as start_time_str,
                MAX(datetime_utc) as end_time_str
            FROM funding_history
        """
        params: List[Any] = []
        if exchange:
            exchange_clean = "Hyperliquid" if "hyperliquid" in exchange.lower() or exchange.lower() == "hl" else "Bybit"
            query += " WHERE exchange = ?"
            params.append(exchange_clean)

        query += " GROUP BY exchange, symbol ORDER BY exchange, symbol;"

        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            rows = cur.fetchall()

        return [dict(row) for row in rows]

    def sync_and_get_history(
        self,
        exchange: str,
        symbol: str,
        days: int = 30,
        fetch_func: Optional[Callable[[str, Optional[int]], list]] = None,
        force_sync: bool = False
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Cache-Aside with High-Water Mark Incremental Sync.
        1. Checks local SQLite for existing records and timestamps.
        2. Determines if incremental or full network fetch is required.
        3. Persists new records into SQLite.
        4. Returns normalized records along with sync metadata.
        """
        exchange_clean = "Hyperliquid" if "hyperliquid" in exchange.lower() or exchange.lower() == "hl" else "Bybit"
        now_ms = int(time.time() * 1000)
        target_start_ts = (now_ms - (days * 24 * 3600 * 1000)) if (days and days > 0) else 0

        latest_ts = self.get_latest_timestamp(exchange_clean, symbol)
        oldest_ts = self.get_oldest_timestamp(exchange_clean, symbol)

        # Expected freshness threshold
        is_hl = (exchange_clean == "Hyperliquid")
        freshness_threshold_ms = (3600 * 1000) if is_hl else (8 * 3600 * 1000)

        need_sync = force_sync
        fetch_start_time: Optional[int] = None

        if latest_ts is None:
            # 1. No local data at all -> Full fetch from target_start_ts
            need_sync = True
            fetch_start_time = target_start_ts
        else:
            # 2. Check if we need older historical data (e.g. user requested 90 days but local only has 30 days)
            if target_start_ts > 0 and oldest_ts is not None and oldest_ts > target_start_ts:
                need_sync = True
                fetch_start_time = target_start_ts

            # 3. Check if local data is stale (e.g. last record is older than 1 cycle)
            if (now_ms - latest_ts) > freshness_threshold_ms:
                need_sync = True
                # If we only need recent records, sync incrementally starting from latest_ts
                if fetch_start_time is None:
                    fetch_start_time = latest_ts

        new_inserted = 0
        sync_error = None

        if need_sync and fetch_func is not None:
            try:
                raw_fetched = fetch_func(symbol, fetch_start_time)
                if raw_fetched:
                    new_inserted = self.save_records(exchange_clean, symbol, raw_fetched)
            except Exception as e:
                sync_error = str(e)

        # Read back requested range from local SQLite
        records = self.get_records(exchange_clean, symbol, start_time=target_start_ts if target_start_ts > 0 else None)

        metadata = {
            "exchange": exchange_clean,
            "symbol": symbol,
            "days_requested": days,
            "source": "sqlite_local" if (new_inserted == 0 and not sync_error) else "incremental_sync",
            "new_records_synced": new_inserted,
            "total_local_records": len(records),
            "sync_error": sync_error
        }

        return records, metadata
