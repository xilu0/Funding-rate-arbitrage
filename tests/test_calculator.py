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
        self.assertEqual(res[0]["raw_spot_pair"], "HYPE/USDC")
        self.assertEqual(res[1]["coin"], "BTC")
        self.assertEqual(res[1]["spot_symbol"], "UBTC")

    def test_match_and_calculate_with_non_contiguous_coin_ctx(self):
        # Hyperliquid real API has hundreds of spot_ctxs where indices don't match universe order
        perp_universe = [{"name": "HYPE"}]
        perp_ctxs = [{"funding": "0.0000125", "markPx": "72.0", "midPx": "72.0", "dayNtlVlm": "1000000"}]
        spot_tokens = [
            {"name": "USDC", "index": 0},
            {"name": "DUMMY", "index": 1},
            {"name": "HYPE", "index": 150}
        ]
        spot_universe = [
            {"tokens": [150, 0], "name": "@107", "isCanonical": False}
        ]
        spot_ctxs = [
            {"coin": "@1", "midPx": "0.5", "dayNtlVlm": "100"},
            {"coin": "@107", "midPx": "72.02", "dayNtlVlm": "2000000"},
            {"coin": "@200", "midPx": "1.0", "dayNtlVlm": "500"}
        ]

        res = self.calc.match_and_calculate(perp_universe, perp_ctxs, spot_tokens, spot_universe, spot_ctxs)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["coin"], "HYPE")
        self.assertEqual(res[0]["spot_symbol"], "HYPE")
        self.assertEqual(res[0]["raw_spot_pair"], "@107")
        self.assertEqual(res[0]["spot_pair"], "HYPE/USDC")
        self.assertAlmostEqual(res[0]["spot_price"], 72.02)

    def test_match_and_calculate_with_k_multiplier_and_bridged_aliases(self):
        perp_universe = [{"name": "kBONK"}, {"name": "LINK"}, {"name": "XMR"}]
        perp_ctxs = [
            {"funding": "0.0001", "markPx": "0.0026", "midPx": "0.0026", "dayNtlVlm": "100000"},
            {"funding": "0.00005", "markPx": "10.60", "midPx": "10.60", "dayNtlVlm": "500000"},
            {"funding": "0.00008", "markPx": "417.0", "midPx": "417.0", "dayNtlVlm": "200000"}
        ]
        spot_tokens = [
            {"name": "USDC", "index": 0},
            {"name": "UBONK", "index": 1},
            {"name": "LINK0", "index": 2},
            {"name": "XMR1", "index": 3}
        ]
        spot_universe = [
            {"tokens": [1, 0], "name": "@10", "isCanonical": False},
            {"tokens": [2, 0], "name": "@20", "isCanonical": False},
            {"tokens": [3, 0], "name": "@30", "isCanonical": False}
        ]
        spot_ctxs = [
            {"coin": "@10", "midPx": "0.0000026", "dayNtlVlm": "50000"},
            {"coin": "@20", "midPx": "10.60", "dayNtlVlm": "100000"},
            {"coin": "@30", "midPx": "417.0", "dayNtlVlm": "150000"}
        ]

        res = self.calc.match_and_calculate(perp_universe, perp_ctxs, spot_tokens, spot_universe, spot_ctxs)
        self.assertEqual(len(res), 3)
        coins = {r["coin"]: r for r in res}
        self.assertIn("kBONK", coins)
        self.assertEqual(coins["kBONK"]["spot_symbol"], "UBONK")
        self.assertEqual(coins["kBONK"]["multiplier"], 1000.0)
        self.assertAlmostEqual(coins["kBONK"]["spread_pct"], 0.0)

        self.assertIn("LINK", coins)
        self.assertEqual(coins["LINK"]["spot_symbol"], "LINK0")

        self.assertIn("XMR", coins)
        self.assertEqual(coins["XMR"]["spot_symbol"], "XMR1")

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

    def test_calculate_funding_history_stats_hyperliquid(self):
        # Hyperliquid format: 'coin', 'time' (1h interval: 3600000 ms)
        raw_history = [
            {"coin": "HYPE", "fundingRate": "0.00005", "time": 1700000000000},
            {"coin": "HYPE", "fundingRate": "0.00010", "time": 1700003600000},
            {"coin": "HYPE", "fundingRate": "-0.00002", "time": 1700007200000},
        ]

        hist = FundingRateCalculator.calculate_funding_history_stats(raw_history)

        self.assertEqual(hist["symbol"], "HYPE")
        self.assertEqual(hist["total_periods"], 3)
        self.assertEqual(hist["funding_interval_hr"], 1.0)

        stats = hist["stats"]
        self.assertAlmostEqual(stats["cumulative_funding_pct"], 0.013)
        self.assertEqual(stats["pos_count"], 2)
        self.assertEqual(stats["neg_count"], 1)
        self.assertAlmostEqual(stats["max_rate_pct"], 0.01)
        self.assertAlmostEqual(stats["min_rate_pct"], -0.002)

    def test_calculate_funding_history_stats_empty(self):
        hist = FundingRateCalculator.calculate_funding_history_stats([])
        self.assertEqual(hist["total_periods"], 0)
        self.assertEqual(hist["records"], [])
        self.assertEqual(hist["stats"], {})

    def test_maker_taker_fee_rates(self):
        # Hyperliquid fees: spot_taker=0.07%, perp_taker=0.035%, spot_maker=0.015%, perp_maker=0.015%
        calc = FundingRateCalculator(
            spot_taker_fee=0.0007,
            perp_taker_fee=0.00035,
            spot_maker_fee=0.00015,
            perp_maker_fee=0.00015
        )
        # Maker-Taker: Spot Maker (0.015%) + Perp Taker (0.035%) = 0.050% = 0.00050
        self.assertAlmostEqual(calc.get_entry_fee_rate("maker_taker"), 0.00050)
        self.assertAlmostEqual(calc.get_roundtrip_fee_rate("maker_taker"), 0.00100)

        # Taker-Taker: Spot Taker (0.070%) + Perp Taker (0.035%) = 0.105% = 0.00105
        self.assertAlmostEqual(calc.get_entry_fee_rate("taker_taker"), 0.00105)
        self.assertAlmostEqual(calc.get_roundtrip_fee_rate("taker_taker"), 0.00210)

        # Maker-Maker: Spot Maker (0.015%) + Perp Maker (0.015%) = 0.030% = 0.00030
        self.assertAlmostEqual(calc.get_entry_fee_rate("maker_maker"), 0.00030)
        self.assertAlmostEqual(calc.get_roundtrip_fee_rate("maker_maker"), 0.00060)

    def test_evaluate_capital_capacity_modes(self):
        calc = FundingRateCalculator(
            spot_taker_fee=0.0007,
            perp_taker_fee=0.00035,
            spot_maker_fee=0.00015,
            perp_maker_fee=0.00015
        )
        spot_asks = [(100.0, 100.0), (101.0, 100.0)]
        spot_bids = [(99.0, 100.0), (98.0, 100.0)]
        perp_asks = [(100.0, 100.0), (101.0, 100.0)]
        perp_bids = [(99.0, 100.0), (98.0, 100.0)]

        # Mode: maker_taker
        res_mt = calc.evaluate_capital_capacity(
            spot_asks=spot_asks,
            spot_bids=spot_bids,
            spot_mid_px=99.5,
            perp_asks=perp_asks,
            perp_bids=perp_bids,
            perp_mid_px=99.5,
            hourly_funding=0.0001,
            custom_target_usd=5000.0,
            execution_mode="maker_taker"
        )
        self.assertEqual(res_mt["execution_mode"], "maker_taker")
        self.assertAlmostEqual(res_mt["fees"]["base_fee_pct"], 0.050)
        self.assertAlmostEqual(res_mt["fees"]["fee_savings_pct"], 0.055)
        self.assertIsNotNone(res_mt["custom_simulation"])
        self.assertAlmostEqual(res_mt["custom_simulation"]["fee_savings_usd"], 2.75) # 0.055% of $5000 = $2.75

        # Mode: taker_taker
        res_tt = calc.evaluate_capital_capacity(
            spot_asks=spot_asks,
            spot_bids=spot_bids,
            spot_mid_px=99.5,
            perp_asks=perp_asks,
            perp_bids=perp_bids,
            perp_mid_px=99.5,
            hourly_funding=0.0001,
            custom_target_usd=5000.0,
            execution_mode="taker_taker"
        )
        self.assertEqual(res_tt["execution_mode"], "taker_taker")
        self.assertAlmostEqual(res_tt["fees"]["base_fee_pct"], 0.105)
        self.assertAlmostEqual(res_tt["fees"]["fee_savings_pct"], 0.0)

    def test_classify_hyperliquid_tokens(self):
        from src.calculator import classify_hyperliquid_token

        # 1. Canonical (PURR)
        purr_token = {
            "name": "PURR", "isCanonical": True, "tokenId": "0xc1fb593a",
            "evmContract": {"address": "0x9b498c3c8a0b8cd8ba1d9851d40d186f1872b44e"},
            "deployerTradingFeeShare": "0.0"
        }
        purr_pair = {"name": "PURR/USDC", "raw_pair_name": "PURR/USDC", "isCanonical": True}
        c_purr = classify_hyperliquid_token(purr_token, purr_pair, "PURR", 1.0)
        self.assertEqual(c_purr["origin_type"], "OFFICIAL_CANONICAL")
        self.assertTrue(c_purr["is_canonical"])
        self.assertTrue(c_purr["has_evm"])
        self.assertIn("官方 Canonical", c_purr["origin_badge"])
        self.assertIn("65% LTV", c_purr["collateral_status"])

        # 2. Native HYPE
        hype_token = {"name": "HYPE", "isCanonical": False, "tokenId": "0x0d01", "evmContract": {"address": "0x0d0167"}}
        hype_pair = {"name": "@107", "raw_pair_name": "@107"}
        c_hype = classify_hyperliquid_token(hype_token, hype_pair, "HYPE", 1.0)
        self.assertEqual(c_hype["origin_type"], "NATIVE_HYPE")
        self.assertIn("原生 HYPE", c_hype["origin_badge"])
        self.assertTrue(c_hype["has_evm"])

        # 3. Unit Bridged (UBTC)
        ubtc_token = {"name": "UBTC", "isCanonical": False}
        ubtc_pair = {"name": "@142", "raw_pair_name": "@142"}
        c_ubtc = classify_hyperliquid_token(ubtc_token, ubtc_pair, "BTC", 1.0)
        self.assertEqual(c_ubtc["origin_type"], "UNIT_BRIDGED")
        self.assertIn("Unit 映射", c_ubtc["origin_badge"])
        self.assertIn("全款现货对冲", c_ubtc["collateral_status"])

        # 4. HyBridge (XMR1)
        xmr_token = {"name": "XMR1", "isCanonical": False}
        xmr_pair = {"name": "@260", "raw_pair_name": "@260"}
        c_xmr = classify_hyperliquid_token(xmr_token, xmr_pair, "XMR", 1.0)
        self.assertEqual(c_xmr["origin_type"], "HYBRIDGE_BRIDGED")
        self.assertIn("跨链桥接", c_xmr["origin_badge"])

        # 5. HIP-1 Community Permissionless (AZTEC)
        aztec_token = {"name": "AZTEC", "isCanonical": False, "deployerTradingFeeShare": "0.05"}
        aztec_pair = {"name": "@285", "raw_pair_name": "@285"}
        c_aztec = classify_hyperliquid_token(aztec_token, aztec_pair, "AZTEC", 1.0)
        self.assertEqual(c_aztec["origin_type"], "HIP1_PERMISSIONLESS")
        self.assertIn("HIP-1 无许可", c_aztec["origin_badge"])
        self.assertEqual(c_aztec["deployer_fee_share_pct"], 5.0)

        # 6. Multiplier Contract (kBONK)
        kbonk_token = {"name": "UBONK", "isCanonical": False}
        kbonk_pair = {"name": "@194", "raw_pair_name": "@194"}
        c_kbonk = classify_hyperliquid_token(kbonk_token, kbonk_pair, "kBONK", 1000.0)
        self.assertIn("🔢 1000x 乘数", c_kbonk["origin_tags"])

    def test_classify_bybit_tokens(self):
        from src.calculator import classify_bybit_token

        # 1. Official Linear
        c_btc = classify_bybit_token("BTCUSDT", "BTCUSDT", 1.0)
        self.assertEqual(c_btc["origin_type"], "BYBIT_OFFICIAL_LINEAR")
        self.assertIn("官方正向", c_btc["origin_badge"])

        # 2. Multiplier
        c_pepe = classify_bybit_token("1000PEPEUSDT", "PEPEUSDT", 1000.0)
        self.assertEqual(c_pepe["origin_type"], "BYBIT_MULTIPLIER")
        self.assertIn("1000x 乘数", c_pepe["origin_badge"])

    def test_match_and_calculate_metadata_injection(self):
        perp_universe = [{"name": "PURR"}, {"name": "BTC"}]
        perp_ctxs = [
            {"funding": "0.0001", "markPx": "0.20", "midPx": "0.20", "dayNtlVlm": "500000"},
            {"funding": "0.00005", "markPx": "60000.0", "midPx": "60000.0", "dayNtlVlm": "5000000"}
        ]
        spot_tokens = [
            {"name": "USDC", "index": 0, "isCanonical": True},
            {"name": "PURR", "index": 1, "isCanonical": True, "evmContract": {"address": "0xpurr"}},
            {"name": "UBTC", "index": 2, "isCanonical": False}
        ]
        spot_universe = [
            {"tokens": [1, 0], "name": "PURR/USDC", "isCanonical": True},
            {"tokens": [2, 0], "name": "@142", "isCanonical": False}
        ]
        spot_ctxs = [
            {"midPx": "0.20", "dayNtlVlm": "100000"},
            {"midPx": "60000.0", "dayNtlVlm": "300000"}
        ]

        res = self.calc.match_and_calculate(perp_universe, perp_ctxs, spot_tokens, spot_universe, spot_ctxs)
        self.assertEqual(len(res), 2)
        purr_item = next(r for r in res if r["coin"] == "PURR")
        self.assertEqual(purr_item["origin_type"], "OFFICIAL_CANONICAL")
        self.assertTrue(purr_item["has_evm"])
        self.assertEqual(purr_item["evm_address"], "0xpurr")
        self.assertIn("官方 Canonical", purr_item["origin_badge"])

        btc_item = next(r for r in res if r["coin"] == "BTC")
        self.assertEqual(btc_item["origin_type"], "UNIT_BRIDGED")
        self.assertEqual(btc_item["raw_spot_pair"], "@142")

if __name__ == "__main__":
    unittest.main()



