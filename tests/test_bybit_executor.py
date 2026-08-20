import unittest
from src.bybit_executor import BybitArbitrageExecutor

class TestBybitArbitrageExecutor(unittest.TestCase):

    def setUp(self):
        self.executor = BybitArbitrageExecutor()

    def test_parse_execution_size_by_usd(self):
        # Normal coin BTCUSDT, ref_price = 60000
        size = self.executor.parse_execution_size("BTCUSDT", amount_usd=12000.0, spot_price=60000.0)
        self.assertEqual(size["symbol"], "BTCUSDT")
        self.assertEqual(size["spot_symbol"], "BTCUSDT")
        self.assertEqual(size["multiplier"], 1.0)
        self.assertAlmostEqual(size["target_usd"], 12000.0)
        self.assertAlmostEqual(size["spot_qty"], 0.2)
        self.assertAlmostEqual(size["perp_contracts_qty"], 0.2)

        # Multiplier coin 1000PEPEUSDT, ref_price = 0.0030 (1000x multiplier scale)
        size_pepe = self.executor.parse_execution_size("1000PEPEUSDT", amount_usd=3000.0, perp_price=0.0030)
        self.assertEqual(size_pepe["multiplier"], 1000.0)
        self.assertEqual(size_pepe["spot_symbol"], "PEPEUSDT")
        self.assertAlmostEqual(size_pepe["spot_qty"], 1000000.0) # 1,000,000 PEPE
        self.assertAlmostEqual(size_pepe["perp_contracts_qty"], 1000.0) # 1,000 contracts of 1000PEPE

    def test_parse_execution_size_by_qty(self):
        # By token qty for 1000PEPEUSDT
        size_pepe_qty = self.executor.parse_execution_size("1000PEPEUSDT", amount_qty=2000000.0, perp_price=0.0030)
        self.assertAlmostEqual(size_pepe_qty["spot_qty"], 2000000.0)
        self.assertAlmostEqual(size_pepe_qty["perp_contracts_qty"], 2000.0)
        self.assertAlmostEqual(size_pepe_qty["target_usd"], 6000.0)

    def test_check_risk_guard_passed(self):
        res = self.executor.check_risk_guard(
            hourly_funding=0.0001, # Positive
            combined_slippage_pct=0.10, # Low
            spot_mid_px=60000.0,
            perp_mid_px=60050.0, # Low spread
            payback_hours=12.0, # Fast payback
            exceeds_depth=False,
            force=False
        )
        self.assertEqual(res["decision"], "PASSED")
        self.assertFalse(res["has_risks"])
        self.assertEqual(len(res["warnings"]), 0)

    def test_check_risk_guard_blocked_by_risks(self):
        res = self.executor.check_risk_guard(
            hourly_funding=-0.0001, # Negative funding!
            combined_slippage_pct=0.85, # High slippage > 0.50%!
            spot_mid_px=60000.0,
            perp_mid_px=63000.0, # High spread > 2%!
            payback_hours=120.0, # Slow payback > 72h!
            exceeds_depth=True,
            force=False
        )
        self.assertEqual(res["decision"], "BLOCKED")
        self.assertTrue(res["has_risks"])
        self.assertGreaterEqual(len(res["warnings"]), 4)

    def test_check_risk_guard_forced_override(self):
        res = self.executor.check_risk_guard(
            hourly_funding=-0.0001,
            combined_slippage_pct=0.85,
            spot_mid_px=60000.0,
            perp_mid_px=63000.0,
            payback_hours=120.0,
            exceeds_depth=True,
            force=True # Force override!
        )
        self.assertEqual(res["decision"], "FORCED_OVERRIDE")
        self.assertTrue(res["has_risks"])
        self.assertTrue(res["force_enabled"])

    def test_generate_try_run_plan(self):
        spot_asks = [(60000.0, 1.0)]
        spot_bids = [(59990.0, 1.0)]
        perp_asks = [(60010.0, 1.0)]
        perp_bids = [(60000.0, 1.0)]

        # 1. Default Maker-Taker plan
        plan = self.executor.generate_try_run_plan(
            symbol="BTCUSDT",
            spot_symbol="BTCUSDT",
            multiplier=1.0,
            target_usd=10000.0,
            spot_qty=0.166666,
            perp_contracts_qty=0.166666,
            hourly_funding=0.0001,
            spot_asks=spot_asks,
            spot_bids=spot_bids,
            spot_mid_px=59995.0,
            perp_asks=perp_asks,
            perp_bids=perp_bids,
            perp_mid_px=60005.0,
            force=False,
            execution_mode="maker_taker"
        )

        self.assertEqual(plan["symbol"], "BTCUSDT")
        self.assertEqual(plan["target_usd"], 10000.0)
        self.assertEqual(plan["execution_mode"], "maker_taker")
        self.assertEqual(len(plan["orders_plan"]), 2)
        self.assertEqual(plan["risk_guard"]["decision"], "PASSED")

        spot_order = plan["orders_plan"][0]
        perp_order = plan["orders_plan"][1]
        self.assertEqual(spot_order["order_type"], "Limit (Post-Only / Maker)")
        self.assertEqual(spot_order["time_in_force"], "PostOnly")
        self.assertEqual(spot_order["target_price"], 59990.0) # Best Bid
        self.assertEqual(spot_order["slippage_pct"], 0.0) # Post-Only Maker 0 slippage

        self.assertEqual(perp_order["order_type"], "Market / IOC (Taker)")
        self.assertEqual(perp_order["time_in_force"], "IOC")

        self.assertGreater(plan["fee_savings_pct"], 0.0)
        self.assertGreater(plan["fee_savings_usd"], 0.0)

        # 2. Taker-Taker plan
        plan_tt = self.executor.generate_try_run_plan(
            symbol="BTCUSDT",
            spot_symbol="BTCUSDT",
            multiplier=1.0,
            target_usd=10000.0,
            spot_qty=0.166666,
            perp_contracts_qty=0.166666,
            hourly_funding=0.0001,
            spot_asks=spot_asks,
            spot_bids=spot_bids,
            spot_mid_px=59995.0,
            perp_asks=perp_asks,
            perp_bids=perp_bids,
            perp_mid_px=60005.0,
            force=False,
            execution_mode="taker_taker"
        )
        self.assertEqual(plan_tt["execution_mode"], "taker_taker")
        self.assertEqual(plan_tt["orders_plan"][0]["order_type"], "Market (Taker)")
        self.assertEqual(plan_tt["orders_plan"][1]["order_type"], "Market (Taker)")
        self.assertAlmostEqual(plan_tt["fee_savings_pct"], 0.0)

if __name__ == "__main__":
    unittest.main()
