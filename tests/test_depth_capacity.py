import unittest
from src.calculator import FundingRateCalculator, safe_float

class TestDepthCapacity(unittest.TestCase):

    def setUp(self):
        self.calc = FundingRateCalculator(spot_taker_fee=0.0010, perp_taker_fee=0.00055)

    def test_simulate_orderbook_walk(self):
        # Asks for buying: level 1: 100.0, qty 10 ($1000); level 2: 102.0, qty 20 ($2040)
        asks = [(100.0, 10.0), (102.0, 20.0)]
        mid_px = 99.5

        # Target $500: fits in level 1 (price 100.0) -> VWAP = 100.0
        vwap, slip, depth = FundingRateCalculator.simulate_orderbook_walk(asks, 500.0, is_buy=True, mid_price=mid_px)
        self.assertEqual(vwap, 100.0)
        self.assertAlmostEqual(slip, (100.0 - 99.5) / 99.5 * 100.0)
        self.assertEqual(depth, 500.0)

        # Target $2000: walks into level 2
        # $1000 @ 100.0 (qty 10) + $1000 @ 102.0 (qty 9.8039215686) -> VWAP ~ 100.99
        vwap2, slip2, depth2 = FundingRateCalculator.simulate_orderbook_walk(asks, 2000.0, is_buy=True, mid_price=mid_px)
        self.assertIsNotNone(vwap2)
        self.assertGreater(vwap2, 100.0)
        self.assertEqual(depth2, 2000.0)

        # Target $10000: exceeds orderbook depth
        vwap3, slip3, depth3 = FundingRateCalculator.simulate_orderbook_walk(asks, 10000.0, is_buy=True, mid_price=mid_px)
        self.assertIsNone(vwap3)
        self.assertIsNone(slip3)
        self.assertAlmostEqual(depth3, 3040.0)

    def test_evaluate_capital_capacity(self):
        spot_asks = [(100.0, 50.0), (101.0, 100.0)]
        spot_bids = [(99.0, 50.0), (98.0, 100.0)]
        spot_mid = 99.5

        perp_asks = [(100.2, 50.0), (101.2, 100.0)]
        perp_bids = [(99.8, 50.0), (98.8, 100.0)]
        perp_mid = 100.0

        hourly_funding = 0.0001 # 0.01%/h positive funding

        cap_res = self.calc.evaluate_capital_capacity(
            spot_asks=spot_asks,
            spot_bids=spot_bids,
            spot_mid_px=spot_mid,
            perp_asks=perp_asks,
            perp_bids=perp_bids,
            perp_mid_px=perp_mid,
            hourly_funding=hourly_funding,
            custom_target_usd=2500.0
        )

        self.assertTrue(cap_res["is_positive_arbitrage"])
        self.assertIn("max_capacities", cap_res)
        self.assertIn("simulations", cap_res)
        self.assertIsNotNone(cap_res["custom_simulation"])
        self.assertEqual(cap_res["custom_simulation"]["target_usd"], 2500.0)

if __name__ == "__main__":
    unittest.main()
