import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from src.hourly_funding_reporter import HourlyFundingReporter
from src.hyperliquid_client import HyperliquidClient
from src.telegram_notifier import TelegramNotifier


class TestHourlyFundingReporter(unittest.TestCase):

    def setUp(self):
        self.mock_hl_client = MagicMock(spec=HyperliquidClient)
        self.mock_notifier = MagicMock(spec=TelegramNotifier)
        self.mock_notifier.is_configured.return_value = True
        self.mock_notifier.send_message.return_value = (True, "Message sent successfully")

        self.test_address = "0x87843f3E86B66bC369947aad95c7907cb49cDCB6"

        self.reporter = HourlyFundingReporter(
            hl_client=self.mock_hl_client,
            notifier=self.mock_notifier,
            account_address=self.test_address,
            enabled=True,
            report_minute=1,
            show_market=True,
            only_on_payout=False,
            symbols=["HYPE"]
        )

    def test_init_defaults_and_status(self):
        self.assertTrue(self.reporter.is_enabled())
        status = self.reporter.get_status()
        self.assertEqual(status["account_address"], self.test_address)
        self.assertEqual(status["report_minute"], 1)
        self.assertTrue(status["show_market"])
        self.assertFalse(status["only_on_payout"])
        self.assertEqual(status["symbols"], ["HYPE"])
        self.assertEqual(status["total_reports_sent"], 0)

    def test_fetch_recent_settlements_single_coin(self):
        # Mock funding records from Hyperliquid userFunding
        now_ms = 1789995600000
        self.mock_hl_client.get_user_funding.return_value = [
            {
                "time": now_ms,
                "hash": "0x12345",
                "delta": {
                    "type": "funding",
                    "coin": "HYPE",
                    "usdc": "0.019084",
                    "szi": "-16.0",
                    "fundingRate": "0.0000125",
                    "nSamples": None
                }
            },
            {
                "time": now_ms - 3600000,
                "hash": "0x12344",
                "delta": {
                    "type": "funding",
                    "coin": "HYPE",
                    "usdc": "0.019101",
                    "szi": "-16.0",
                    "fundingRate": "0.0000125",
                    "nSamples": None
                }
            }
        ]

        settlements = self.reporter.fetch_recent_settlements()
        self.assertEqual(len(settlements), 1)
        s = settlements[0]
        self.assertEqual(s["coin"], "HYPE")
        self.assertAlmostEqual(s["usdc"], 0.019084, places=6)
        self.assertEqual(s["szi"], -16.0)
        self.assertEqual(s["side"], "Short")
        self.assertEqual(s["abs_size"], 16.0)
        self.assertAlmostEqual(s["funding_rate"], 0.0000125, places=8)
        self.assertAlmostEqual(s["hourly_rate_pct"], 0.00125, places=5)
        self.assertAlmostEqual(s["apr_pct"], 10.95, places=2)

    def test_fetch_recent_settlements_multi_coin(self):
        # Multiple coins settled in the same block/hour
        now_ms = 1789995600000
        self.mock_hl_client.get_user_funding.return_value = [
            {
                "time": now_ms,
                "hash": "0x1",
                "delta": {
                    "coin": "HYPE",
                    "usdc": "0.0200",
                    "szi": "-16.0",
                    "fundingRate": "0.0000125"
                }
            },
            {
                "time": now_ms + 10,
                "hash": "0x2",
                "delta": {
                    "coin": "PURR",
                    "usdc": "0.0150",
                    "szi": "-100.0",
                    "fundingRate": "0.000025"
                }
            },
            {
                "time": now_ms - 3600000,
                "hash": "0x0",
                "delta": {
                    "coin": "HYPE",
                    "usdc": "0.0190",
                    "szi": "-16.0",
                    "fundingRate": "0.0000125"
                }
            }
        ]

        settlements = self.reporter.fetch_recent_settlements()
        self.assertEqual(len(settlements), 2)
        total_usdc = sum(x["usdc"] for x in settlements)
        self.assertAlmostEqual(total_usdc, 0.035, places=4)

    def test_build_report_with_active_position(self):
        # Mock settlements
        self.mock_hl_client.get_user_funding.return_value = [
            {
                "time": 1789995600000,
                "delta": {
                    "coin": "HYPE",
                    "usdc": "0.019084",
                    "szi": "-16.0",
                    "fundingRate": "0.0000125"
                }
            }
        ]

        # Mock Scheme D health
        mock_health = {
            "account_value": 10230.50,
            "spot_cash_usdc": 8545.00,
            "total_margin_used": 151.78,
            "margin_utilization_pct": 1.56,
            "min_liq_distance_pct": 610.2,
            "cum_funding_total": 0.2598,
            "tier_name": "🟢 稳健正常 (Scheme D Invariants Satisfied)",
            "spot_balances": [
                {
                    "coin": "HYPE",
                    "total_qty": 15.99,
                    "valuation_usd": 1516.00,
                    "ltv": 0.65
                }
            ],
            "positions": [
                {
                    "coin": "HYPE",
                    "size": 16.0,
                    "side": "Short",
                    "entry_price": 95.38,
                    "liquidation_price": 673.58,
                    "cum_funding": 0.2598
                }
            ]
        }
        self.reporter.fetch_portfolio_health = MagicMock(return_value=mock_health)
        self.reporter.fetch_market_overview = MagicMock(return_value=[
            {
                "coin": "HYPE",
                "spot_price": 94.80,
                "perp_price": 94.85,
                "spread_pct": 0.052,
                "hourly_funding_pct": 0.00125,
                "apr_pct": 10.95
            }
        ])

        report_data = self.reporter.build_report()
        self.assertTrue(report_data["has_active_position"])
        self.assertAlmostEqual(report_data["total_payout_usdc"], 0.019084, places=6)
        self.assertAlmostEqual(report_data["total_cum_funding_usdc"], 0.2598, places=4)

        md = report_data["markdown"]
        self.assertIn("【资金费率每小时收益播报】", md)
        self.assertIn("HYPE", md)
        self.assertIn("+0.0191 USDC", md)
        self.assertIn("+0.2598 USDC", md)
        self.assertIn("10,230.50 USDC", md)
        self.assertIn("+610.2%", md)
        self.assertIn("方案 D 综合净年化", md)
        self.assertIn("9.86% APR", md)

    def test_build_report_zero_position_fallback(self):
        # Empty settlements and empty positions
        self.mock_hl_client.get_user_funding.return_value = []
        mock_health = {
            "account_value": 10000.00,
            "spot_cash_usdc": 10000.00,
            "total_margin_used": 0.0,
            "margin_utilization_pct": 0.0,
            "min_liq_distance_pct": None,
            "cum_funding_total": 0.0,
            "spot_balances": [],
            "positions": []
        }
        self.reporter.fetch_portfolio_health = MagicMock(return_value=mock_health)
        self.reporter.fetch_market_overview = MagicMock(return_value=[
            {
                "coin": "HYPE",
                "spot_price": 94.80,
                "perp_price": 94.85,
                "spread_pct": 0.052,
                "hourly_funding_pct": 0.00125,
                "apr_pct": 10.95
            }
        ])

        report_data = self.reporter.build_report()
        self.assertFalse(report_data["has_active_position"])

        md = report_data["markdown"]
        self.assertIn("【资金费率每小时市场播报】", md)
        self.assertIn("当前账户暂无活跃", md)
        self.assertIn("测算每 $10,000 本金收益", md)

    def test_send_report_idempotency_and_deduplication(self):
        self.reporter.build_report = MagicMock(return_value={
            "markdown": "Test report",
            "settlements": [{"coin": "HYPE", "usdc": 0.01}],
            "total_payout_usdc": 0.01,
            "total_cum_funding_usdc": 0.25,
            "datetime_utc": "2026-09-21 14:01:00 UTC"
        })

        # 1. First send: succeeds
        success, msg = self.reporter.send_report(force=False)
        self.assertTrue(success)
        self.mock_notifier.send_message.assert_called_once_with("Test report")
        self.assertEqual(self.reporter._total_reports_sent, 1)

        # 2. Duplicate send in same hour: skipped
        success2, msg2 = self.reporter.send_report(force=False)
        self.assertFalse(success2)
        self.assertIn("already sent", msg2)
        # Verify notifier was not called a second time
        self.mock_notifier.send_message.assert_called_once()

        # 3. Forced send in same hour: succeeds
        success3, msg3 = self.reporter.send_report(force=True)
        self.assertTrue(success3)
        self.assertEqual(self.mock_notifier.send_message.call_count, 2)
        self.assertEqual(self.reporter._total_reports_sent, 2)

    def test_only_on_payout_filter(self):
        self.reporter.only_on_payout = True
        self.reporter.build_report = MagicMock(return_value={
            "markdown": "Zero position digest",
            "settlements": [],
            "total_payout_usdc": 0.0,
            "total_cum_funding_usdc": 0.0,
            "datetime_utc": "2026-09-21 14:01:00 UTC"
        })

        success, msg = self.reporter.send_report(force=False)
        self.assertFalse(success)
        self.assertIn("no funding settlement payout", msg)
        self.mock_notifier.send_message.assert_not_called()

    def test_check_and_trigger_timing(self):
        self.reporter.send_report = MagicMock(return_value=(True, "Sent"))

        # Case 1: Inside trigger window (minute=1, report_minute=1)
        dt_in_window = datetime(2026, 9, 21, 14, 1, 10, tzinfo=timezone.utc)
        res1 = self.reporter.check_and_trigger(now=dt_in_window)
        self.assertEqual(res1, (True, "Sent"))
        self.reporter.send_report.assert_called_once_with(force=False)

        # Mark hour as already reported
        self.reporter._last_reported_hour = "2026-09-21-14"

        # Case 2: Already reported in this hour
        dt_same_hour = datetime(2026, 9, 21, 14, 2, 0, tzinfo=timezone.utc)
        res2 = self.reporter.check_and_trigger(now=dt_same_hour)
        self.assertIsNone(res2)

        # Case 3: Outside trigger window for new hour (minute=20, report_minute=1)
        self.reporter._last_reported_hour = "2026-09-21-14"
        dt_outside = datetime(2026, 9, 21, 15, 20, 0, tzinfo=timezone.utc)
        res3 = self.reporter.check_and_trigger(now=dt_outside)
        self.assertIsNone(res3)


if __name__ == "__main__":
    unittest.main()
