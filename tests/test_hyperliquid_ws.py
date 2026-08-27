import unittest
import json
import logging
from unittest.mock import MagicMock, patch
from src.hyperliquid_ws import HyperliquidWsFeed

class TestHyperliquidWsFeed(unittest.TestCase):

    def setUp(self):
        self.feed = HyperliquidWsFeed(
            symbols=["HYPER"],
            spot_mappings={"HYPE": "@107", "HYPER": "@107"}
        )

    def test_init_and_mappings(self):
        self.assertEqual(self.feed.symbols, ["HYPER"])
        self.assertEqual(self.feed.coin_to_spot_raw.get("HYPE"), "@107")
        self.assertEqual(self.feed.spot_raw_to_coin.get("@107"), "HYPER")
        health = self.feed.get_health()
        self.assertFalse(health["ws_connected"])
        self.assertTrue(health["has_websocket_pkg"])
        self.assertEqual(health["error_count"], 0)

    def test_on_open_sends_subscriptions(self):
        mock_ws = MagicMock()
        self.feed._on_open(mock_ws)
        self.assertTrue(self.feed.is_connected)

        # Verify subscriptions were sent
        calls = mock_ws.send.call_args_list
        sent_payloads = [json.loads(c[0][0]) for c in calls]
        sent_subscriptions = [p.get("subscription", {}).get("type") for p in sent_payloads]

        self.assertIn("allMids", sent_subscriptions)
        self.assertIn("l2Book", sent_subscriptions)
        self.assertIn("activeAssetCtx", sent_subscriptions)

    def test_on_error_logs_and_records(self):
        mock_ws = MagicMock()
        with self.assertLogs("hyperliquid_ws", level="ERROR") as log_cm:
            self.feed._on_error(mock_ws, "Connection timed out")

        self.assertEqual(self.feed.error_count, 1)
        self.assertEqual(self.feed.last_error, "Connection timed out")
        self.assertTrue(any("❌ [Hyperliquid WS] 行情连接发生错误" in r for r in log_cm.output))

    def test_on_close_logs_and_sets_disconnected(self):
        mock_ws = MagicMock()
        self.feed.is_connected = True
        with self.assertLogs("hyperliquid_ws", level="ERROR") as log_cm:
            self.feed._on_close(mock_ws, 1006, "Abnormal closure")

        self.assertFalse(self.feed.is_connected)
        self.assertTrue(any("❌ [Hyperliquid WS] 行情长连接已中断" in r for r in log_cm.output))

    def test_on_message_ctx_and_l2book_triggers_calculation(self):
        listener_mock = MagicMock()
        self.feed.add_listener(listener_mock)

        mock_ws = MagicMock()

        # 1. Receive activeAssetCtx for HYPE
        ctx_msg = json.dumps({
            "channel": "activeAssetCtx",
            "data": {
                "coin": "HYPE",
                "ctx": {
                    "funding": "0.000025",
                    "markPx": "82.50",
                    "midPx": "82.50",
                    "openInterest": "100000"
                }
            }
        })
        self.feed._on_message(mock_ws, ctx_msg)

        # 2. Receive spot l2Book for @107 (mid = 82.00)
        spot_l2_msg = json.dumps({
            "channel": "l2Book",
            "data": {
                "coin": "@107",
                "time": 1787820000000,
                "levels": [
                    [{"px": "81.99", "sz": "100.0", "n": 1}],
                    [{"px": "82.01", "sz": "100.0", "n": 1}]
                ]
            }
        })
        self.feed._on_message(mock_ws, spot_l2_msg)

        # 3. Receive perp l2Book for HYPE (mid = 82.164)
        perp_l2_msg = json.dumps({
            "channel": "l2Book",
            "data": {
                "coin": "HYPE",
                "time": 1787820000000,
                "levels": [
                    [{"px": "82.16", "sz": "50.0", "n": 1}],
                    [{"px": "82.168", "sz": "50.0", "n": 1}]
                ]
            }
        })
        self.feed._on_message(mock_ws, perp_l2_msg)

        # Verify listener was called with computed metrics
        self.assertTrue(listener_mock.called)
        latest_metric = listener_mock.call_args[0][0]
        self.assertEqual(latest_metric["coin"], "HYPE")
        self.assertAlmostEqual(latest_metric["spot_price"], 82.00, places=2)
        self.assertAlmostEqual(latest_metric["perp_price"], 82.164, places=2)
        # Spread = (82.164 - 82.0) / 82.0 * 100 = 0.20%
        self.assertAlmostEqual(latest_metric["spread_pct"], 0.20, places=2)
        # Funding rate = 0.000025 -> APR = 0.000025 * 24 * 365 * 100 = 21.9%
        self.assertAlmostEqual(latest_metric["apr_pct"], 21.9, places=1)
        self.assertEqual(latest_metric["source"], "websocket")

    def test_listener_exception_does_not_crash(self):
        failing_listener = MagicMock(side_effect=RuntimeError("Listener error"))
        self.feed.add_listener(failing_listener)
        self.feed.spot_books["@107"] = {
            "bids": [{"px": "80.0", "sz": "1"}],
            "asks": [{"px": "80.1", "sz": "1"}],
            "time": 1000
        }

        mock_ws = MagicMock()
        msg = json.dumps({
            "channel": "l2Book",
            "data": {
                "coin": "HYPE",
                "time": 1000,
                "levels": [[{"px": "80.0", "sz": "1"}], [{"px": "80.1", "sz": "1"}]]
            }
        })
        # Should not raise exception even if listener fails
        self.feed._on_message(mock_ws, msg)
        self.assertTrue(failing_listener.called)

if __name__ == "__main__":
    unittest.main()
