import unittest
import time
from unittest.mock import MagicMock
from src.basis_auditor import BasisAuditor
from src.auto_arbitrage_engine import AutoArbitrageEngine

class TestBasisAuditor(unittest.TestCase):

    def setUp(self):
        self.auditor = BasisAuditor(
            window_seconds=30.0,
            min_spread_pct=0.08,
            min_p10_floor_pct=0.02,
            min_depth_multiple=2.5,
            min_apr_pct=12.0,
            min_reliability_score=80.0
        )

    def test_vwap_spread_calculation(self):
        # Target notional $820 USD -> 10 HYPE at $82.00
        spot_book = {
            "asks": [
                {"px": "82.00", "sz": "4.0"},
                {"px": "82.10", "sz": "6.0"}
            ]
        }
        # Spot VWAP: (4 * 82.00 + 6 * 82.10) / 10 = (328 + 492.6) / 10 = 82.06

        perp_book = {
            "bids": [
                {"px": "82.30", "sz": "5.0"},
                {"px": "82.20", "sz": "10.0"}
            ]
        }
        # Perp VWAP: (5 * 82.30 + 5 * 82.20) / 10 = (411.5 + 411) / 10 = 82.25

        vwap_spot, vwap_perp, exec_spread, depth_mult = BasisAuditor.calculate_vwap_spread(
            target_notional_usd=820.0,
            spot_price=82.0,
            spot_book=spot_book,
            perp_book=perp_book
        )

        self.assertAlmostEqual(vwap_spot, 82.06, places=2)
        self.assertAlmostEqual(vwap_perp, 82.25, places=2)
        expected_spread = (82.25 - 82.06) / 82.06 * 100.0
        self.assertAlmostEqual(exec_spread, expected_spread, places=2)
        # Total perp bids in top levels = 5*82.30 + 10*82.20 = 411.5 + 822 = 1233.5 USD
        # depth_mult = 1233.5 / 820.0 = ~1.50
        self.assertAlmostEqual(depth_mult, 1233.5 / 820.0, places=2)

    def test_streak_and_p10_floor_tracking(self):
        t0 = 1000.0
        # Feed 30 seconds of high spread
        for i in range(31):
            t = t0 + i
            self.auditor.record_tick(
                coin="HYPE",
                spot_price=82.0,
                perp_price=82.10,
                spread_pct=0.12,
                apr_pct=25.0,
                now=t
            )

        audit = self.auditor.audit_basis(
            coin="HYPE",
            current_spread_pct=0.12,
            current_apr_pct=25.0,
            spot_price=82.0,
            now=t0 + 30.0
        )
        self.assertGreaterEqual(audit["metrics"]["duration_seconds"], 30.0)
        self.assertGreaterEqual(audit["metrics"]["p10_floor_pct"], 0.10)
        self.assertEqual(audit["grade"], "A")
        self.assertTrue(audit["is_reliable"])
        self.assertGreaterEqual(audit["reliability_score"], 80.0)

    def test_transient_spike_rejected_as_grade_c(self):
        t0 = 2000.0
        # Only 1 single tick just occurred (duration 0s)
        self.auditor.record_tick(
            coin="HYPE",
            spot_price=82.0,
            perp_price=82.15,
            spread_pct=0.18,  # Huge spike
            apr_pct=5.0,      # Low funding support
            now=t0
        )

        audit = self.auditor.audit_basis(
            coin="HYPE",
            current_spread_pct=0.18,
            current_apr_pct=5.0,
            spot_price=82.0,
            now=t0
        )
        # Duration is 0s, funding is low -> score must be < 60, Grade C
        self.assertFalse(audit["is_reliable"])
        self.assertEqual(audit["grade"], "C")
        self.assertIn("瞬态", audit["grade_badge"])

    def test_negative_spread_resets_streak(self):
        t0 = 3000.0
        self.auditor.record_tick("HYPE", 82.0, 82.10, 0.12, 20.0, now=t0)
        self.auditor.record_tick("HYPE", 82.0, 82.10, 0.12, 20.0, now=t0 + 10.0)
        self.assertIn("HYPE", self.auditor._streak_start)

        # Spread collapses into backwardation
        self.auditor.record_tick("HYPE", 82.0, 81.90, -0.12, 20.0, now=t0 + 11.0)
        self.assertNotIn("HYPE", self.auditor._streak_start)

    def test_giant_order_whale_supported(self):
        t0 = 4000.0
        # Continuous for 16 seconds (>= 15s)
        self.auditor.record_tick("HYPE", 82.0, 82.20, 0.24, 25.0, now=t0)
        self.auditor.record_tick("HYPE", 82.0, 82.20, 0.24, 25.0, now=t0 + 16.0)

        # Huge bid: 500 HYPE at $82.20 = $41,100 (> $20,000 threshold)
        perp_book = {
            "bids": [{"px": "82.20", "sz": "500.0"}],
            "asks": [{"px": "82.30", "sz": "50.0"}]
        }
        spot_book = {
            "bids": [{"px": "81.90", "sz": "50.0"}],
            "asks": [{"px": "82.00", "sz": "50.0"}]
        }

        audit = self.auditor.audit_basis(
            coin="HYPE",
            current_spread_pct=0.24,
            current_apr_pct=25.0,
            spot_price=82.0,
            spot_book=spot_book,
            perp_book=perp_book,
            now=t0 + 16.0
        )
        cause = audit["cause_analysis"]
        self.assertEqual(cause["cause_type"], "WHALE_SUPPORTED")
        self.assertTrue(cause["has_giant_orders"])
        self.assertTrue(cause["safe_for_manual"])
        self.assertTrue(audit["is_reliable"])
        self.assertEqual(audit["grade"], "A")
        self.assertIn("巨单资金真实驱动", cause["cause_title"])

    def test_giant_order_verifying_before_15s(self):
        t0 = 5000.0
        # Only 5 seconds (not yet 15s)
        self.auditor.record_tick("HYPE", 82.0, 82.20, 0.24, 25.0, now=t0)
        self.auditor.record_tick("HYPE", 82.0, 82.20, 0.24, 25.0, now=t0 + 5.0)

        perp_book = {
            "bids": [{"px": "82.20", "sz": "500.0"}],
            "asks": [{"px": "82.30", "sz": "50.0"}]
        }
        spot_book = {
            "bids": [{"px": "81.90", "sz": "50.0"}],
            "asks": [{"px": "82.00", "sz": "50.0"}]
        }

        audit = self.auditor.audit_basis(
            coin="HYPE",
            current_spread_pct=0.24,
            current_apr_pct=25.0,
            spot_price=82.0,
            spot_book=spot_book,
            perp_book=perp_book,
            now=t0 + 5.0
        )
        cause = audit["cause_analysis"]
        self.assertEqual(cause["cause_type"], "WHALE_SUPPORTED")
        self.assertFalse(cause["safe_for_manual"])
        self.assertFalse(audit["is_reliable"])
        self.assertEqual(audit["grade"], "B")
        self.assertIn("防撤单", audit["grade_badge"])

    def test_thin_spike_rejection(self):
        t0 = 6000.0
        self.auditor.record_tick("HYPE", 82.0, 82.50, 0.60, 30.0, now=t0)

        # Extremely thin perp book: only 5 HYPE at $82.50 = $412.5 (< $5,000)
        perp_book = {
            "bids": [{"px": "82.50", "sz": "5.0"}],
            "asks": [{"px": "82.60", "sz": "5.0"}]
        }
        spot_book = {
            "bids": [{"px": "81.90", "sz": "50.0"}],
            "asks": [{"px": "82.00", "sz": "50.0"}]
        }

        audit = self.auditor.audit_basis(
            coin="HYPE",
            current_spread_pct=0.60,
            current_apr_pct=30.0,
            spot_price=82.0,
            spot_book=spot_book,
            perp_book=perp_book,
            now=t0
        )
        cause = audit["cause_analysis"]
        self.assertEqual(cause["cause_type"], "THIN_SPIKE")
        self.assertFalse(cause["safe_for_manual"])
        self.assertFalse(audit["is_reliable"])
        self.assertEqual(audit["grade"], "C")
        self.assertIn("薄盘口", cause["cause_title"])

    def test_aggressive_taker_buy_flow_whale(self):
        t0 = 7000.0
        # Continuous for 18 seconds
        self.auditor.record_tick("HYPE", 82.0, 82.20, 0.24, 25.0, now=t0)
        self.auditor.record_tick("HYPE", 82.0, 82.20, 0.24, 25.0, now=t0 + 18.0)

        # Inject aggressive taker buy trades ($35,000 buy vs $5,000 sell)
        self.auditor.record_trade("HYPE", side="B", px=82.20, sz=400.0, now=t0 + 10.0)
        self.auditor.record_trade("HYPE", side="B", px=82.20, sz=50.0, now=t0 + 12.0)
        self.auditor.record_trade("HYPE", side="A", px=82.10, sz=20.0, now=t0 + 14.0)

        perp_book = {
            "bids": [{"px": "82.20", "sz": "100.0"}],
            "asks": [{"px": "82.30", "sz": "80.0"}]
        }
        spot_book = {
            "bids": [{"px": "81.90", "sz": "50.0"}],
            "asks": [{"px": "82.00", "sz": "50.0"}]
        }

        audit = self.auditor.audit_basis(
            coin="HYPE",
            current_spread_pct=0.24,
            current_apr_pct=25.0,
            spot_price=82.0,
            spot_book=spot_book,
            perp_book=perp_book,
            now=t0 + 18.0
        )
        cause = audit["cause_analysis"]
        self.assertEqual(cause["cause_type"], "WHALE_SUPPORTED")
        self.assertTrue(cause["safe_for_manual"])
        self.assertTrue(audit["is_reliable"])
        self.assertGreaterEqual(cause["recent_taker_buy_usd"], 30000.0)


class TestAutoArbitrageEngineWithAuditor(unittest.TestCase):

    def test_engine_blocks_unreliable_spike(self):
        mock_executor = MagicMock()
        mock_hl = MagicMock()
        mock_notifier = MagicMock()
        mock_notifier.is_configured.return_value = True

        engine = AutoArbitrageEngine(
            executor=mock_executor,
            hl_client=mock_hl,
            notifier=mock_notifier,
            enabled=True,
            dry_run=True,
            symbols=["HYPE"],
            min_spread_pct=0.08,
            min_apr_pct=10.0,
            require_reliable_basis=True,
            min_reliability_score=80.0,
            persistence_ticks=1,
            persistence_ms=0.0
        )

        # Single transient spike
        tick = {
            "coin": "HYPE",
            "spot_price": 82.0,
            "perp_price": 82.10,
            "spread_pct": 0.12,
            "apr_pct": 8.0 # Low funding, duration 0s -> not reliable
        }

        res = engine.on_market_tick(tick)
        self.assertIsNone(res)
        mock_executor.execute_dual_ioc_arbitrage.assert_not_called()

if __name__ == "__main__":
    unittest.main()
