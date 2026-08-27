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
