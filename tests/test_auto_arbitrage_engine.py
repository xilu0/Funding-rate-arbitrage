import unittest
import time
from unittest.mock import MagicMock, patch
from src.auto_arbitrage_engine import AutoArbitrageEngine

class TestAutoArbitrageEngine(unittest.TestCase):

    def setUp(self):
        self.mock_executor = MagicMock()
        self.mock_hl_client = MagicMock()
        self.mock_notifier = MagicMock()
        self.mock_notifier.is_configured.return_value = True

        # Default mock balances: 10,000 USDC in spot
        self.mock_hl_client.get_spot_clearinghouse_state.return_value = {
            "balances": [{"coin": "USDC", "total": "10000.0"}]
        }

        self.engine = AutoArbitrageEngine(
            executor=self.mock_executor,
            hl_client=self.mock_hl_client,
            notifier=self.mock_notifier,
            enabled=True,
            dry_run=True,
            symbols=["HYPER"],
            min_spread_pct=0.08,
            min_apr_pct=10.0,
            max_total_capital_usd=5000.0,
            per_trade_usd=500.0,
            min_cash_reserve_usd=1000.0,
            max_slippage_pct=0.30,
            cooldown_seconds=60.0,
            persistence_ticks=2,
            persistence_ms=50.0,
            require_reliable_basis=False
        )

    def test_init_and_symbol_matching(self):
        self.assertTrue(self.engine.enabled)
        self.assertTrue(self.engine.dry_run)
        self.assertEqual(self.engine.symbols, ["HYPER"])
        self.assertTrue(self.engine.matches_symbol("HYPE"))
        self.assertTrue(self.engine.matches_symbol("HYPER"))
        self.assertTrue(self.engine.matches_symbol("hype"))
        self.assertFalse(self.engine.matches_symbol("BTC"))

    def test_capital_sizing_scheme_d(self):
        # 10,000 spot USDC, reserve 1,000 -> usable cash = 9,000
        # remaining quota = 5,000 -> allocated = min(500, 9000, 5000) = 500
        # 90% to spot = 450 USD. Spot px = 82.0 -> qty = 450 / 82 = 5.4878 -> 5.49 HYPE
        ok, qty, msg, details = self.engine.calculate_trade_sizing("HYPE", 82.0)
        self.assertTrue(ok)
        self.assertAlmostEqual(qty, 5.49, places=2)
        self.assertEqual(details["allocated_usd"], 500.0)
        self.assertAlmostEqual(details["spot_notional_usd"], 5.49 * 82.0, places=1)

    def test_capital_sizing_rejections(self):
        # Insufficient usable cash
        self.mock_hl_client.get_spot_clearinghouse_state.return_value = {
            "balances": [{"coin": "USDC", "total": "1005.0"}]  # reserve is 1000 -> usable is 5
        }
        self.engine.dry_run = False
        ok, qty, msg, _ = self.engine.calculate_trade_sizing("HYPE", 82.0)
        self.assertFalse(ok)
        self.assertIn("usable cash", msg)

        # Max quota reached
        self.mock_hl_client.get_spot_clearinghouse_state.return_value = {
            "balances": [
                {"coin": "USDC", "total": "10000.0"},
                {"coin": "HYPE", "total": "65.0"}  # 65 * 80 = 5200 USD >= 5000 limit
            ]
        }
        ok, qty, msg, _ = self.engine.calculate_trade_sizing("HYPE", 82.0)
        self.assertFalse(ok)
        self.assertIn("ceiling reached", msg)

    def test_anti_flicker_persistence_filter(self):
        self.mock_executor.execute_dual_ioc_arbitrage.return_value = {
            "status": "SIMULATED_SUCCESS",
            "exec_spread_pct": 0.12,
            "latency_ms": 2
        }

        tick1 = {
            "coin": "HYPE",
            "spot_price": 82.00,
            "perp_price": 82.10,
            "spread_pct": 0.122,  # > 0.08%
            "apr_pct": 15.0
        }

        # First tick: persistence requirement (2 ticks / 50ms) not yet met
        res1 = self.engine.on_market_tick(tick1)
        self.assertIsNone(res1)
        self.assertEqual(self.engine._total_trades_attempted, 0)

        # Simulate 60ms elapsed
        time.sleep(0.06)

        # Second tick: condition met and persisted!
        res2 = self.engine.on_market_tick(tick1)
        self.assertIsNotNone(res2)
        self.assertEqual(self.engine._total_trades_attempted, 1)
        self.assertEqual(self.engine._total_trades_succeeded, 1)
        self.mock_executor.execute_dual_ioc_arbitrage.assert_called_once()
        self.mock_notifier.send_message.assert_called_once()

    def test_cooldown_blocks_immediate_retask(self):
        self.mock_executor.execute_dual_ioc_arbitrage.return_value = {
            "status": "SIMULATED_SUCCESS",
            "exec_spread_pct": 0.12,
            "latency_ms": 2
        }

        tick = {
            "coin": "HYPE",
            "spot_price": 82.00,
            "perp_price": 82.10,
            "spread_pct": 0.122,
            "apr_pct": 15.0
        }

        # Fire trade
        self.engine.on_market_tick(tick)
        time.sleep(0.06)
        res1 = self.engine.on_market_tick(tick)
        self.assertIsNotNone(res1)

        # Immediate next tick within 60s cooldown -> must be blocked
        res2 = self.engine.on_market_tick(tick)
        self.assertIsNone(res2)
        self.assertEqual(self.engine._total_trades_attempted, 1)

    def test_update_config_and_get_status(self):
        status1 = self.engine.get_status()
        self.assertTrue(status1["enabled"])
        self.assertEqual(status1["min_spread_pct"], 0.08)

        new_status = self.engine.update_config(
            enabled=False,
            min_spread_pct=0.15,
            per_trade_usd=1000.0
        )
        self.assertFalse(new_status["enabled"])
        self.assertEqual(new_status["min_spread_pct"], 0.15)
        self.assertEqual(new_status["capital_policy"]["per_trade_usd"], 1000.0)

if __name__ == "__main__":
    unittest.main()
