import unittest
from src.calculator import FundingRateCalculator, DEFAULT_SPOT_TAKER_FEE, DEFAULT_PERP_TAKER_FEE

class TestFundingRateCalculator(unittest.TestCase):

    def setUp(self):
        self.calc = FundingRateCalculator(spot_taker_fee=0.0007, perp_taker_fee=0.00035, enable_aliases=True)

    def test_fee_rates(self):
        # Entry fee = 0.07% + 0.035% = 0.105% = 0.00105
        self.assertAlmostEqual(self.calc.entry_fee_rate, 0.00105)
        # Roundtrip fee = 2 * 0.00105 = 0.0021
        self.assertAlmostEqual(self.calc.roundtrip_fee_rate, 0.0021)

    def test_apr_and_apy_calculation(self):
        # Test rate: 0.01% per hour = 0.0001
        hourly_rate = 0.0001
        apr = hourly_rate * 24 * 365 * 100.0
        self.assertAlmostEqual(apr, 87.6)

    def test_payback_hours(self):
        # Hourly rate: 0.01% per hour = 0.0001
        # Entry fee: 0.00105 (0.105%)
        # Payback = 0.00105 / 0.0001 = 10.5 hours
        entry_hrs = self.calc.calculate_payback_hours(self.calc.entry_fee_rate, 0.0001)
        self.assertAlmostEqual(entry_hrs, 10.5)

        # Roundtrip fee: 0.0021 (0.21%)
        # Payback = 0.0021 / 0.0001 = 21.0 hours
        rt_hrs = self.calc.calculate_payback_hours(self.calc.roundtrip_fee_rate, 0.0001)
        self.assertAlmostEqual(rt_hrs, 21.0)

    def test_negative_or_zero_funding_payback(self):
        self.assertIsNone(self.calc.calculate_payback_hours(self.calc.entry_fee_rate, 0.0))
        self.assertIsNone(self.calc.calculate_payback_hours(self.calc.entry_fee_rate, -0.0001))

    def test_format_hours(self):
        self.assertEqual(FundingRateCalculator.format_hours(None), "N/A")
        self.assertEqual(FundingRateCalculator.format_hours(0.5), "30.0 min")
        self.assertEqual(FundingRateCalculator.format_hours(10.5), "10.5 hrs")
        self.assertEqual(FundingRateCalculator.format_hours(72.0), "3.0 days")

    def test_match_and_calculate(self):
        perp_universe = [{"name": "HYPE"}, {"name": "BTC"}]
        perp_ctxs = [
            {"funding": "0.0001", "markPx": "25.0", "midPx": "25.0", "dayNtlVlm": "1000000"},
            {"funding": "0.00005", "markPx": "60000.0", "midPx": "60000.0", "dayNtlVlm": "5000000"}
        ]
        spot_tokens = [
            {"name": "USDC", "index": 0},
            {"name": "HYPE", "index": 1},
            {"name": "UBTC", "index": 2}
        ]
        spot_universe = [
            {"tokens": [1, 0], "name": "HYPE/USDC", "isCanonical": True},
            {"tokens": [2, 0], "name": "@1", "isCanonical": True}
        ]
        spot_ctxs = [
            {"midPx": "24.8", "dayNtlVlm": "500000"},
            {"midPx": "59900.0", "dayNtlVlm": "300000"}
        ]

        res = self.calc.match_and_calculate(perp_universe, perp_ctxs, spot_tokens, spot_universe, spot_ctxs)

        self.assertEqual(len(res), 2)
        # Should be sorted by funding rate descending (HYPE: 0.0001 > BTC: 0.00005)
        self.assertEqual(res[0]["coin"], "HYPE")
        self.assertEqual(res[1]["coin"], "BTC")
        self.assertEqual(res[1]["spot_symbol"], "UBTC")

if __name__ == "__main__":
    unittest.main()
