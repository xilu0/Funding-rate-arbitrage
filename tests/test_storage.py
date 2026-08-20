import unittest
import tempfile
import os
import time
from src.storage import FundingHistoryStorage

class TestFundingHistoryStorage(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_funding.db")
        self.storage = FundingHistoryStorage(db_path=self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_init_db(self):
        self.assertTrue(os.path.exists(self.db_path))
        with self.storage._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='funding_history';")
            row = cur.fetchone()
            self.assertIsNotNone(row)

    def test_save_and_get_records_hyperliquid(self):
        raw_hl = [
            {"coin": "HYPE", "fundingRate": "0.0001", "time": 1700000000000},
            {"coin": "HYPE", "fundingRate": "0.0002", "time": 1700003600000},
        ]
        inserted = self.storage.save_records("Hyperliquid", "HYPE", raw_hl)
        self.assertEqual(inserted, 2)

        # Re-inserting the same records should insert 0 (idempotent)
        inserted_again = self.storage.save_records("Hyperliquid", "HYPE", raw_hl)
        self.assertEqual(inserted_again, 0)

        # Get records
        records = self.storage.get_records("Hyperliquid", "HYPE")
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["symbol"], "HYPE")
        self.assertEqual(records[0]["funding_interval_hr"], 1.0)
        self.assertAlmostEqual(records[0]["period_funding_rate"], 0.0001)
        self.assertAlmostEqual(records[1]["period_funding_rate"], 0.0002)

    def test_save_and_get_records_bybit(self):
        raw_bybit = [
            {"symbol": "BTCUSDT", "fundingRate": "0.0001", "fundingRateTimestamp": "1700000000000"},
            {"symbol": "BTCUSDT", "fundingRate": "0.00015", "fundingRateTimestamp": "1700028800000"},
        ]
        inserted = self.storage.save_records("Bybit", "BTCUSDT", raw_bybit)
        self.assertEqual(inserted, 2)

        records = self.storage.get_records("Bybit", "BTCUSDT")
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["symbol"], "BTCUSDT")
        self.assertEqual(records[0]["funding_interval_hr"], 8.0)
        self.assertAlmostEqual(records[0]["hourly_funding_rate"], 0.0001 / 8.0)

    def test_get_timestamps(self):
        raw_recs = [
            {"coin": "PURR", "fundingRate": "0.0001", "time": 1700000000000},
            {"coin": "PURR", "fundingRate": "0.0002", "time": 1700007200000},
        ]
        self.storage.save_records("Hyperliquid", "PURR", raw_recs)

        min_ts = self.storage.get_oldest_timestamp("Hyperliquid", "PURR")
        max_ts = self.storage.get_latest_timestamp("Hyperliquid", "PURR")

        self.assertEqual(min_ts, 1700000000000)
        self.assertEqual(max_ts, 1700007200000)

    def test_sync_and_get_history_empty_db(self):
        now_ms = int(time.time() * 1000)
        # When DB is empty, fetch_func should be called
        mock_fetch_called = []
        def mock_fetch(sym, st):
            mock_fetch_called.append((sym, st))
            return [
                {"coin": sym, "fundingRate": "0.00005", "time": now_ms - 7200000},
                {"coin": sym, "fundingRate": "0.00006", "time": now_ms - 3600000}
            ]

        records, meta = self.storage.sync_and_get_history(
            exchange="Hyperliquid",
            symbol="ETH",
            days=30,
            fetch_func=mock_fetch
        )

        self.assertEqual(len(mock_fetch_called), 1)
        self.assertEqual(len(records), 2)
        self.assertEqual(meta["new_records_synced"], 2)
        self.assertEqual(meta["source"], "incremental_sync")

    def test_sync_and_get_history_cached_hit(self):
        now_ms = int(time.time() * 1000)
        # Pre-seed with 3 records: 1 older than 7 days, 2 inside the 7 days window
        fresh_recs = [
            {"coin": "SOL", "fundingRate": "0.0001", "time": now_ms - (8 * 24 * 3600 * 1000)},
            {"coin": "SOL", "fundingRate": "0.0001", "time": now_ms - (3 * 24 * 3600 * 1000)},
            {"coin": "SOL", "fundingRate": "0.0001", "time": now_ms - (5 * 60 * 1000)}
        ]
        self.storage.save_records("Hyperliquid", "SOL", fresh_recs)

        mock_fetch_called = []
        def mock_fetch(sym, st):
            mock_fetch_called.append((sym, st))
            return []

        records, meta = self.storage.sync_and_get_history(
            exchange="Hyperliquid",
            symbol="SOL",
            days=7,
            fetch_func=mock_fetch
        )

        # Fresh cache -> fetch_func should NOT be called
        self.assertEqual(len(mock_fetch_called), 0)
        self.assertEqual(len(records), 2)
        self.assertEqual(meta["source"], "sqlite_local")
        self.assertEqual(meta["new_records_synced"], 0)

    def test_sync_fallback_on_network_error(self):
        # Pre-seed some records
        old_recs = [
            {"symbol": "BTCUSDT", "fundingRate": "0.0001", "fundingRateTimestamp": "1600000000000"}
        ]
        self.storage.save_records("Bybit", "BTCUSDT", old_recs)

        def failing_fetch(sym, st):
            raise RuntimeError("API Timeout / 429")

        records, meta = self.storage.sync_and_get_history(
            exchange="Bybit",
            symbol="BTCUSDT",
            days=0,
            fetch_func=failing_fetch
        )

        # Should not crash, returns existing records and records the error in metadata
        self.assertEqual(len(records), 1)
        self.assertIn("API Timeout", meta["sync_error"])

    def test_get_symbols_summary(self):
        self.storage.save_records("Hyperliquid", "HYPE", [{"coin": "HYPE", "fundingRate": "0.0001", "time": 1700000000000}])
        self.storage.save_records("Bybit", "BTCUSDT", [{"symbol": "BTCUSDT", "fundingRate": "0.0002", "fundingRateTimestamp": "1700000000000"}])

        summary = self.storage.get_symbols_summary()
        self.assertEqual(len(summary), 2)
        symbols = [s["symbol"] for s in summary]
        self.assertIn("HYPE", symbols)
        self.assertIn("BTCUSDT", symbols)

if __name__ == "__main__":
    unittest.main()
