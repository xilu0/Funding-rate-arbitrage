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
        self.assertEqual(res[0]["exchange"], "Hyperliquid")
        self.assertEqual(res[1]["coin"], "BTC")
        self.assertEqual(res[1]["spot_symbol"], "UBTC")

    def test_parse_base_multiplier(self):
        from src.calculator import parse_base_multiplier
        self.assertEqual(parse_base_multiplier("1000PEPE"), (1000.0, "PEPE"))
        self.assertEqual(parse_base_multiplier("1000000BABYDOGE"), (1000000.0, "BABYDOGE"))
        self.assertEqual(parse_base_multiplier("0G"), (1.0, "0G"))
        self.assertEqual(parse_base_multiplier("1INCH"), (1.0, "1INCH"))
        self.assertEqual(parse_base_multiplier("BTC"), (1.0, "BTC"))

    def test_match_and_calculate_bybit(self):
        bybit_calc = FundingRateCalculator(spot_taker_fee=0.0010, perp_taker_fee=0.00055)
        
        linear_tickers = [
            {
                "symbol": "1000PEPEUSDT",
                "lastPrice": "0.0030",
                "fundingRate": "0.0004",
                "fundingIntervalHour": "4",
                "turnover24h": "1000000"
            },
            {
                "symbol": "BTCUSDT",
                "lastPrice": "60000.0",
                "fundingRate": "0.0001",
                "fundingIntervalHour": "8",
                "turnover24h": "5000000"
            }
        ]
        spot_tickers = [
            {
                "symbol": "PEPEUSDT",
                "lastPrice": "0.0000029",
                "turnover24h": "500000"
            },
            {
                "symbol": "BTCUSDT",
                "lastPrice": "59900.0",
                "turnover24h": "2000000"
            }
        ]
        linear_instruments = [
            {"symbol": "1000PEPEUSDT", "baseCoin": "1000PEPE", "quoteCoin": "USDT"},
            {"symbol": "BTCUSDT", "baseCoin": "BTC", "quoteCoin": "USDT"}
        ]
        spot_instruments = [
            {"symbol": "PEPEUSDT", "baseCoin": "PEPE", "quoteCoin": "USDT"},
            {"symbol": "BTCUSDT", "baseCoin": "BTC", "quoteCoin": "USDT"}
        ]

        res = bybit_calc.match_and_calculate_bybit(linear_tickers, spot_tickers, linear_instruments, spot_instruments)

        self.assertEqual(len(res), 2)
        # 1000PEPE funding: 0.0004 / 4h = 0.0001 / h -> 0.01%/h
        # BTC funding: 0.0001 / 8h = 0.0000125 / h -> 0.00125%/h
        self.assertEqual(res[0]["coin"], "1000PEPEUSDT")
        self.assertEqual(res[0]["spot_symbol"], "PEPEUSDT")
        self.assertEqual(res[0]["exchange"], "Bybit")
        self.assertAlmostEqual(res[0]["spot_price"], 0.0029)  # 0.0000029 * 1000
        self.assertAlmostEqual(res[0]["hourly_funding"], 0.0001)

        self.assertEqual(res[1]["coin"], "BTCUSDT")
        self.assertEqual(res[1]["exchange"], "Bybit")
        self.assertAlmostEqual(res[1]["hourly_funding"], 0.0000125)

    def test_calculate_funding_history_stats(self):
        raw_history = [
            {"symbol": "BTCUSDT", "fundingRate": "0.0001", "fundingRateTimestamp": "1000000000000"},
            {"symbol": "BTCUSDT", "fundingRate": "0.0002", "fundingRateTimestamp": "1000028800000"},
            {"symbol": "BTCUSDT", "fundingRate": "-0.0001", "fundingRateTimestamp": "1000057600000"}
        ]

        hist = FundingRateCalculator.calculate_funding_history_stats(raw_history)

        self.assertEqual(hist["symbol"], "BTCUSDT")
        self.assertEqual(hist["total_periods"], 3)
        self.assertEqual(hist["funding_interval_hr"], 8.0)

        stats = hist["stats"]
        # Cumulative = 0.0001 + 0.0002 - 0.0001 = 0.0002 = 0.02%
        self.assertAlmostEqual(stats["cumulative_funding_pct"], 0.02)
        # Pos count = 2, Neg count = 1
        self.assertEqual(stats["pos_count"], 2)
        self.assertEqual(stats["neg_count"], 1)
        self.assertAlmostEqual(stats["pos_pct"], 66.66666666666666)
        # Max rate = 0.02%, Min rate = -0.01%
        self.assertAlmostEqual(stats["max_rate_pct"], 0.02)
        self.assertAlmostEqual(stats["min_rate_pct"], -0.01)

if __name__ == "__main__":
    unittest.main()


