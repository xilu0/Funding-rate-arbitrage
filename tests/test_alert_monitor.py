import unittest
import time
from unittest.mock import MagicMock, patch
from src.telegram_alert_monitor import ArbitrageAlertMonitor
from src.telegram_notifier import TelegramNotifier

class TestArbitrageAlertMonitor(unittest.TestCase):

    def setUp(self):
        self.mock_notifier = MagicMock(spec=TelegramNotifier)
        self.mock_notifier.is_configured.return_value = True
        self.mock_notifier.send_message.return_value = (True, "Message sent successfully")

    def test_symbol_matching(self):
        monitor = ArbitrageAlertMonitor(
            notifier=self.mock_notifier,
            symbols=["HYPER"]
        )
        # Should match HYPER
        self.assertTrue(monitor._matches_target_symbol("HYPER"))
        self.assertTrue(monitor._matches_target_symbol("hyper"))
        # Should automatically match HYPE on Hyperliquid
        self.assertTrue(monitor._matches_target_symbol("HYPE"))
        self.assertTrue(monitor._matches_target_symbol("hype"))
        # Should not match unrelated symbols
        self.assertFalse(monitor._matches_target_symbol("BTC"))
        self.assertFalse(monitor._matches_target_symbol("SOL"))

        # Monitor configured for HYPE matches HYPER as well
        monitor_hype = ArbitrageAlertMonitor(notifier=self.mock_notifier, symbols=["HYPE"])
        self.assertTrue(monitor_hype._matches_target_symbol("HYPER"))
        self.assertTrue(monitor_hype._matches_target_symbol("HYPE"))

        # Multi-symbol matching with Bybit USDT suffix
        monitor_multi = ArbitrageAlertMonitor(notifier=self.mock_notifier, symbols=["BTC", "ETH"])
        self.assertTrue(monitor_multi._matches_target_symbol("BTC"))
        self.assertTrue(monitor_multi._matches_target_symbol("BTCUSDT"))
        self.assertTrue(monitor_multi._matches_target_symbol("ETHUSDT"))
        self.assertFalse(monitor_multi._matches_target_symbol("SOLUSDT"))

        # Wildcard ALL
        monitor_all = ArbitrageAlertMonitor(notifier=self.mock_notifier, symbols=["ALL"])
        self.assertTrue(monitor_all._matches_target_symbol("DOGE"))

    def test_evaluate_conditions_spread_only(self):
        """Test separated evaluation: check_spread=True, check_funding=False (仅参考基差)."""
        monitor = ArbitrageAlertMonitor(
            notifier=self.mock_notifier,
            check_spread=True,
            min_spread_pct=0.10,
            check_funding=False,
            min_apr_pct=20.0
        )

        # 1. Spread exceeds threshold -> Triggers
        metric_spread_high = {
            "coin": "HYPE",
            "spread_pct": 0.15,
            "apr_pct": 5.0,  # Funding is low, but should still trigger due to spread
            "hourly_funding_pct": 0.0005
        }
        triggered, reasons, key = monitor.evaluate_conditions(metric_spread_high)
        self.assertTrue(triggered)
        self.assertEqual(key, "spread")
        self.assertTrue(any("基差扩大突破阈值" in r for r in reasons))

        # 2. Funding exceeds threshold, but spread does not -> DOES NOT Trigger (since check_funding is False)
        metric_funding_high = {
            "coin": "HYPE",
            "spread_pct": 0.02,
            "apr_pct": 50.0,
            "hourly_funding_pct": 0.005
        }
        triggered, reasons, key = monitor.evaluate_conditions(metric_funding_high)
        self.assertFalse(triggered)
        self.assertEqual(key, "none")

    def test_evaluate_conditions_funding_only(self):
        """Test separated evaluation: check_spread=False, check_funding=True (仅参考资金费率)."""
        monitor = ArbitrageAlertMonitor(
            notifier=self.mock_notifier,
            check_spread=False,
            min_spread_pct=0.10,
            check_funding=True,
            min_apr_pct=20.0
        )

        # 1. Spread is high, but funding is low -> DOES NOT Trigger (since check_spread is False)
        metric_spread_high = {
            "coin": "HYPE",
            "spread_pct": 0.25,
            "apr_pct": 5.0,
            "hourly_funding_pct": 0.0005
        }
        triggered, reasons, key = monitor.evaluate_conditions(metric_spread_high)
        self.assertFalse(triggered)
        self.assertEqual(key, "none")

        # 2. Funding is high -> Triggers
        metric_funding_high = {
            "coin": "HYPE",
            "spread_pct": 0.02,
            "apr_pct": 35.0,
            "hourly_funding_pct": 0.004
        }
        triggered, reasons, key = monitor.evaluate_conditions(metric_funding_high)
        self.assertTrue(triggered)
        self.assertEqual(key, "funding")
        self.assertTrue(any("资金费率突破阈值" in r for r in reasons))

    def test_evaluate_conditions_both_enabled(self):
        """Test both enabled: OR logic (either triggers, or both triggers)."""
        monitor = ArbitrageAlertMonitor(
            notifier=self.mock_notifier,
            check_spread=True,
            min_spread_pct=0.10,
            check_funding=True,
            min_apr_pct=20.0
        )

        # Spread high only -> Triggers as spread
        metric1 = {"spread_pct": 0.12, "apr_pct": 10.0}
        t1, r1, k1 = monitor.evaluate_conditions(metric1)
        self.assertTrue(t1)
        self.assertEqual(k1, "spread")

        # Funding high only -> Triggers as funding
        metric2 = {"spread_pct": 0.03, "apr_pct": 25.0}
        t2, r2, k2 = monitor.evaluate_conditions(metric2)
        self.assertTrue(t2)
        self.assertEqual(k2, "funding")

        # Both high -> Triggers as both
        metric3 = {"spread_pct": 0.15, "apr_pct": 30.0}
        t3, r3, k3 = monitor.evaluate_conditions(metric3)
        self.assertTrue(t3)
        self.assertEqual(k3, "both")
        self.assertTrue(any("双重共振" in r for r in r3))

        # Neither high -> Does not trigger
        metric4 = {"spread_pct": 0.02, "apr_pct": 5.0}
        t4, r4, k4 = monitor.evaluate_conditions(metric4)
        self.assertFalse(t4)
        self.assertEqual(k4, "none")

    def test_format_alert_message_contains_funding_even_when_spread_triggered(self):
        """Verifies that the alert message always includes current funding rate and APR even when triggered by basis spread."""
        monitor = ArbitrageAlertMonitor(notifier=self.mock_notifier)
        metric = {
            "exchange": "Hyperliquid",
            "coin": "HYPE",
            "spot_pair": "HYPE/USDC",
            "spot_price": 82.50,
            "perp_price": 82.65,
            "spread_pct": 0.1818,
            "hourly_funding_pct": 0.00125,
            "funding_interval_hr": 1.0,
            "apr_pct": 10.95,
            "apy_pct": 11.57,
            "roundtrip_payback_str": "12.5h"
        }
        reasons = ["⚡ 基差扩大突破阈值 (当前基差 +0.182% >= 阈值 +0.100%)"]
        msg = monitor.format_alert_message(metric, reasons, trigger_key="spread")

        # Must contain basis spread info
        self.assertIn("+0.182%", msg)
        self.assertIn("82.5000", msg)
        self.assertIn("82.6500", msg)
        # Must also contain current funding rate and APR information as requested by user
        self.assertIn("0.00125%", msg)
        self.assertIn("10.95%", msg)
        self.assertIn("11.57%", msg)
        self.assertIn("hl-ops arbitrage", msg)

    @patch("src.telegram_alert_monitor.ArbitrageAlertMonitor.fetch_market_metrics")
    def test_poll_once_and_cooldown(self, mock_fetch):
        mock_fetch.return_value = [
            {
                "exchange": "Hyperliquid",
                "coin": "HYPE",
                "spot_pair": "HYPE/USDC",
                "spot_price": 82.50,
                "perp_price": 82.65,
                "spread_pct": 0.18,
                "hourly_funding_pct": 0.00125,
                "funding_interval_hr": 1.0,
                "apr_pct": 25.0,
                "roundtrip_payback_str": "8.0h"
            }
        ]

        monitor = ArbitrageAlertMonitor(
            notifier=self.mock_notifier,
            symbols=["HYPER"],  # Matches HYPE automatically
            min_spread_pct=0.10,
            min_apr_pct=20.0,
            cooldown_minutes=30.0
        )

        # 1. First poll: should dispatch alert
        alerts1 = monitor.poll_once()
        self.assertEqual(len(alerts1), 1)
        self.assertEqual(alerts1[0]["coin"], "HYPE")
        self.assertEqual(self.mock_notifier.send_message.call_count, 1)

        # 2. Second poll immediately: within 30m cooldown -> should NOT dispatch
        alerts2 = monitor.poll_once()
        self.assertEqual(len(alerts2), 0)
        self.assertEqual(self.mock_notifier.send_message.call_count, 1)

        # 3. Simulate passage of 31 minutes
        cooldown_key = "Hyperliquid:HYPE:both"
        monitor._last_alerts[cooldown_key] = time.time() - (31 * 60)

        # 4. Third poll: cooldown expired -> should dispatch again
        alerts3 = monitor.poll_once()
        self.assertEqual(len(alerts3), 1)
        self.assertEqual(self.mock_notifier.send_message.call_count, 2)

    def test_get_status(self):
        monitor = ArbitrageAlertMonitor(
            notifier=self.mock_notifier,
            enabled=True,
            symbols=["HYPER"],
            min_spread_pct=0.15,
            min_apr_pct=25.0
        )
        status = monitor.get_status()
        self.assertTrue(status["enabled"])
        self.assertTrue(status["is_configured"])
        self.assertEqual(status["symbols"], ["HYPER"])
        self.assertEqual(status["min_spread_pct"], 0.15)
        self.assertEqual(status["min_apr_pct"], 25.0)
        self.assertIn("ws_feed", status)

    def test_ws_market_update_triggers_realtime_alert(self):
        """Verifies that _on_ws_market_update dispatches Telegram alert in real-time."""
        monitor = ArbitrageAlertMonitor(
            notifier=self.mock_notifier,
            symbols=["HYPER"],
            min_spread_pct=0.10,
            min_apr_pct=20.0
        )
        ws_metric = {
            "exchange": "Hyperliquid",
            "coin": "HYPE",
            "spot_pair": "HYPE/USDC",
            "spot_price": 82.00,
            "perp_price": 82.164,
            "spread_pct": 0.20,
            "taker_spread_pct": 0.19,
            "hourly_funding_pct": 0.00125,
            "apr_pct": 10.95,
            "source": "websocket"
        }
        monitor._on_ws_market_update(ws_metric)
        self.assertEqual(self.mock_notifier.send_message.call_count, 1)
        self.assertEqual(monitor._total_alerts_sent, 1)

    def test_ws_market_update_calls_automated_callback(self):
        """Verifies that on_arbitrage_callback hook is invoked for automated trade execution."""
        auto_callback = MagicMock()
        monitor = ArbitrageAlertMonitor(
            notifier=self.mock_notifier,
            symbols=["HYPER"],
            min_spread_pct=0.10,
            on_arbitrage_callback=auto_callback
        )
        ws_metric = {
            "exchange": "Hyperliquid",
            "coin": "HYPE",
            "spot_pair": "HYPE/USDC",
            "spot_price": 82.00,
            "perp_price": 82.164,
            "spread_pct": 0.20,
            "hourly_funding_pct": 0.00125,
            "apr_pct": 10.95,
            "source": "websocket"
        }
        monitor._on_ws_market_update(ws_metric)
        auto_callback.assert_called_once()
        called_metric, trigger_key = auto_callback.call_args[0]
        self.assertEqual(called_metric["coin"], "HYPE")
        self.assertEqual(trigger_key, "spread")

    def test_ws_market_update_ignores_untracked_symbol(self):
        """Verifies that _on_ws_market_update ignores symbols not in monitor.symbols."""
        monitor = ArbitrageAlertMonitor(
            notifier=self.mock_notifier,
            symbols=["HYPER"],
            min_spread_pct=0.10,
        )
        ws_metric = {
            "exchange": "Hyperliquid",
            "coin": "SOL",
            "spot_price": 150.00,
            "perp_price": 155.00,
            "spread_pct": 3.33,
            "apr_pct": 50.0,
            "source": "websocket"
        }
        monitor._on_ws_market_update(ws_metric)
        self.assertEqual(self.mock_notifier.send_message.call_count, 0)
        self.assertEqual(monitor._total_alerts_sent, 0)
        self.assertNotIn("Hyperliquid:SOL", monitor._last_metrics)

if __name__ == "__main__":
    unittest.main()
