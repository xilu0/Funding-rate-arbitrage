import unittest
from unittest.mock import MagicMock, patch
from src.hyperliquid_client import HyperliquidClient
from src.hyperliquid_executor import HyperliquidExecutor

class TestHyperliquidClientEndpoints(unittest.TestCase):
    def setUp(self):
        self.client = HyperliquidClient()

    @patch.object(HyperliquidClient, '_post')
    def test_get_clearinghouse_state(self, mock_post):
        mock_post.return_value = {
            "marginSummary": {"accountValue": "10000.0", "totalMarginUsed": "1000.0"},
            "assetPositions": []
        }
        res = self.client.get_clearinghouse_state("0x1234567890abcdef1234567890abcdef12345678")
        self.assertEqual(res["marginSummary"]["accountValue"], "10000.0")
        mock_post.assert_called_once_with({
            "type": "clearinghouseState",
            "user": "0x1234567890abcdef1234567890abcdef12345678"
        })

    @patch.object(HyperliquidClient, '_post')
    def test_get_spot_clearinghouse_state(self, mock_post):
        mock_post.return_value = {
            "balances": [{"coin": "PURR", "total": "100.0"}]
        }
        res = self.client.get_spot_clearinghouse_state("0x1234567890abcdef1234567890abcdef12345678")
        self.assertEqual(len(res["balances"]), 1)
        self.assertEqual(res["balances"][0]["coin"], "PURR")

    @patch.object(HyperliquidClient, '_post')
    def test_get_open_orders(self, mock_post):
        mock_post.return_value = [
            {"coin": "PURR", "side": "B", "limitPx": "0.10", "sz": "10", "oid": 123}
        ]
        res = self.client.get_open_orders("0x1234567890abcdef1234567890abcdef12345678")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["oid"], 123)

    @patch.object(HyperliquidClient, '_post')
    def test_get_extra_agents(self, mock_post):
        mock_post.return_value = [
            {"address": "0xagent123", "name": "OpsAgent"}
        ]
        res = self.client.get_extra_agents("0x1234567890abcdef1234567890abcdef12345678")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["address"], "0xagent123")

    @patch.object(HyperliquidClient, '_post')
    def test_get_funding_rate_history(self, mock_post):
        mock_post.return_value = [
            {"coin": "HYPE", "fundingRate": "0.0001", "premium": "0.0005", "time": 1784653200000}
        ]
        res = self.client.get_funding_rate_history("HYPE", start_time=1784650000000)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["coin"], "HYPE")
        mock_post.assert_called_once_with({
            "type": "fundingHistory",
            "coin": "HYPE",
            "startTime": 1784650000000
        })

    @patch.object(HyperliquidClient, '_post')
    def test_get_user_fees(self, mock_post):
        mock_post.return_value = {
            "userCrossRate": "0.00045",
            "userAddRate": "0.00015",
            "activeReferralDiscount": "0.04"
        }
        res = self.client.get_user_fees("0x1234567890abcdef1234567890abcdef12345678")
        self.assertEqual(res["userCrossRate"], "0.00045")
        self.assertEqual(res["activeReferralDiscount"], "0.04")
        mock_post.assert_called_once_with({
            "type": "userFees",
            "user": "0x1234567890abcdef1234567890abcdef12345678"
        })


class TestHyperliquidExecutor(unittest.TestCase):
    def setUp(self):
        self.mock_client = MagicMock(spec=HyperliquidClient)
        self.executor = HyperliquidExecutor(
            account_address="0x1111111111111111111111111111111111111111",
            agent_private_key="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            hl_client=self.mock_client
        )

    def test_agent_authorization_check_approved(self):
        # Mock get_agent_public_address
        with patch.object(self.executor, 'get_agent_public_address', return_value="0xagentaddress"):
            self.mock_client.get_extra_agents.return_value = [
                {"address": "0xagentaddress", "name": "Agent1"}
            ]
            res = self.executor.check_agent_authorization()
            self.assertTrue(res["authorized"])
            self.assertEqual(res["master_address"], "0x1111111111111111111111111111111111111111")
            self.assertEqual(res["agent_address"], "0xagentaddress")

    def test_agent_authorization_check_not_approved(self):
        with patch.object(self.executor, 'get_agent_public_address', return_value="0xagentaddress"):
            self.mock_client.get_extra_agents.return_value = [
                {"address": "0xotheragent", "name": "Agent2"}
            ]
            res = self.executor.check_agent_authorization()
            self.assertFalse(res["authorized"])
            self.assertIn("not found", res["error"])

    def test_evaluate_scheme_d_health_normal(self):
        self.mock_client.get_clearinghouse_state.return_value = {
            "marginSummary": {
                "accountValue": "10000.0",
                "totalMarginUsed": "1000.0",
                "totalNtlPos": "9000.0",
                "totalRawUsd": "1000.0"
            },
            "withdrawable": "1000.0",
            "assetPositions": [
                {
                    "position": {
                        "coin": "PURR",
                        "szi": "-9000.0",
                        "entryPx": "1.0",
                        "unrealizedPnl": "0.0",
                        "liquidationPx": "2.096",
                        "marginUsed": "1000.0",
                        "cumFunding": {"allTime": "150.0"}
                    }
                }
            ]
        }
        self.mock_client.get_spot_clearinghouse_state.return_value = {
            "balances": [
                {"coin": "USDC", "total": "1000.0", "hold": "0.0"},
                {"coin": "PURR", "total": "9000.0", "hold": "0.0", "entryNtl": "9000.0"}
            ]
        }

        health = self.executor.evaluate_scheme_d_health()
        self.assertEqual(health["tier"], "NORMAL")
        self.assertGreaterEqual(health["min_liq_distance_pct"], 100.0)
        self.assertLessEqual(health["margin_utilization_pct"], 60.0)
        self.assertEqual(len(health["positions"]), 1)
        self.assertEqual(health["spot_cash_usdc"], 1000.0)

    def test_evaluate_scheme_d_health_tier1_buffer(self):
        # High margin utilization triggering Tier 1
        self.mock_client.get_clearinghouse_state.return_value = {
            "marginSummary": {
                "accountValue": "10000.0",
                "totalMarginUsed": "5000.0", # > 60% of effective margin (5000 / 7850 = 63.7%)
                "totalNtlPos": "9000.0",
                "totalRawUsd": "1000.0"
            },
            "withdrawable": "1000.0",
            "assetPositions": [
                {
                    "position": {
                        "coin": "PURR",
                        "szi": "-9000.0",
                        "entryPx": "1.0",
                        "unrealizedPnl": "0.0",
                        "liquidationPx": "2.096",
                        "marginUsed": "4500.0",
                        "cumFunding": {"allTime": "150.0"}
                    }
                }
            ]
        }
        self.mock_client.get_spot_clearinghouse_state.return_value = {
            "balances": [
                {"coin": "USDC", "total": "1000.0", "hold": "0.0"},
                {"coin": "PURR", "total": "9000.0", "hold": "0.0", "entryNtl": "9000.0"}
            ]
        }

        health = self.executor.evaluate_scheme_d_health()
        self.assertEqual(health["tier"], "TIER_1_BUFFER")
        self.assertIn("缓冲注入", health["action_recommended"])

    def test_evaluate_scheme_d_health_tier3_emergency(self):
        # Distance to liq < 8%
        self.mock_client.get_clearinghouse_state.return_value = {
            "marginSummary": {
                "accountValue": "2000.0",
                "totalMarginUsed": "1800.0",
                "totalNtlPos": "9000.0",
                "totalRawUsd": "200.0"
            },
            "withdrawable": "0.0",
            "assetPositions": [
                {
                    "position": {
                        "coin": "PURR",
                        "szi": "-9000.0",
                        "entryPx": "1.0",
                        "unrealizedPnl": "-1000.0",
                        "liquidationPx": "1.05", # distance is 5% < 8%
                        "marginUsed": "1800.0",
                        "cumFunding": {"allTime": "150.0"}
                    }
                }
            ]
        }
        self.mock_client.get_spot_clearinghouse_state.return_value = {
            "balances": []
        }

        health = self.executor.evaluate_scheme_d_health()
        self.assertEqual(health["tier"], "TIER_3_EMERGENCY")
        self.assertIn("紧急熔断", health["action_recommended"])

    def test_internal_usd_transfer_simulation(self):
        res = self.executor.internal_usd_transfer(amount=500.0, to_perp=True, dry_run=True)
        self.assertEqual(res["status"], "SIMULATED")
        self.assertEqual(res["action"], "spotUserToPerp")
        self.assertEqual(res["amount"], 500.0)

    def test_cancel_all_orders_empty(self):
        self.mock_client.get_open_orders.return_value = []
        res = self.executor.cancel_all_orders()
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["canceled_count"], 0)

    def test_build_maker_taker_order_plan(self):
        plan = self.executor.build_maker_taker_order_plan(
            coin="PURR",
            spot_pair="PURR/USDC",
            target_usd=10000.0,
            spot_price=0.10,
            perp_price=0.101,
            multiplier=1.0,
            execution_mode="maker_taker"
        )
        self.assertEqual(plan["execution_mode"], "maker_taker")
        self.assertEqual(plan["coin"], "PURR")
        self.assertEqual(plan["spot_pair"], "PURR/USDC")
        self.assertEqual(plan["target_usd"], 10000.0)
        self.assertAlmostEqual(plan["spot_qty"], 100000.0)
        self.assertAlmostEqual(plan["perp_qty"], 100000.0)

        # Spot order is Alo (Post-Only)
        self.assertEqual(plan["spot_order"]["order_type"], {"limit": {"tif": "Alo"}})
        self.assertTrue(plan["spot_order"]["is_buy"])

        # Perp order is Ioc (Taker)
        self.assertEqual(plan["perp_order"]["order_type"], {"limit": {"tif": "Ioc"}})
        self.assertFalse(plan["perp_order"]["is_buy"])

        # Fee savings
        self.assertAlmostEqual(plan["fee_summary"]["base_fee_pct"], 0.0576)
        self.assertAlmostEqual(plan["fee_summary"]["fee_savings_pct"], 0.0528)
        self.assertAlmostEqual(plan["fee_summary"]["fee_savings_usd"], 5.28)

        # Default should be taker_taker
        default_plan = self.executor.build_maker_taker_order_plan(
            coin="PURR",
            spot_pair="PURR/USDC",
            target_usd=10000.0,
            spot_price=0.10,
            perp_price=0.101,
            multiplier=1.0
        )
        self.assertEqual(default_plan["execution_mode"], "taker_taker")
        self.assertEqual(default_plan["spot_order"]["order_type"], {"limit": {"tif": "Ioc"}})

    def test_resolve_spot_market_pair_hype(self):
        self.mock_client.get_spot_market_data.return_value = (
            [{"name": "USDC", "index": 0}, {"name": "HYPE", "index": 150, "szDecimals": 2}],
            [{"tokens": [150, 0], "name": "@107", "index": 107}],
            [{"midPx": "80.0", "markPx": "80.0", "coin": "@107"}]
        )
        spot_res = self.executor.resolve_spot_market_pair("HYPE")
        self.assertIsNotNone(spot_res)
        self.assertEqual(spot_res["raw_pair_name"], "@107")
        self.assertEqual(spot_res["display_name"], "HYPE/USDC")
        self.assertEqual(spot_res["base_symbol"], "HYPE")
        self.assertEqual(spot_res["quote_symbol"], "USDC")

    def test_build_arbitrage_plan_hype(self):
        self.mock_client.get_perp_market_data.return_value = (
            [{"name": "HYPE", "szDecimals": 2}],
            [{"funding": "0.0001", "midPx": "80.0", "markPx": "80.0"}]
        )
        self.mock_client.get_spot_market_data.return_value = (
            [{"name": "USDC", "index": 0}, {"name": "HYPE", "index": 150, "szDecimals": 2}],
            [{"tokens": [150, 0], "name": "@107", "index": 107}],
            [{"midPx": "80.0", "markPx": "80.0", "coin": "@107"}]
        )
        self.mock_client.get_l2_book.side_effect = lambda c: {
            "levels": [[{"px": "80.0", "sz": "100.0"}], [{"px": "80.1", "sz": "100.0"}]]
        }

        plan = self.executor.build_arbitrage_plan(coin="HYPE", amount_qty=1.0, execution_mode="maker_taker")
        self.assertEqual(plan["coin"], "HYPE")
        self.assertEqual(plan["spot_pair"], "@107")
        self.assertEqual(plan["spot_qty"], 1.0)
        self.assertEqual(plan["perp_qty"], 1.0)
        self.assertEqual(plan["scheme_d"]["capital_efficiency_pct"], 90.0)
        self.assertAlmostEqual(plan["scheme_d"]["collateral_ltv_pct"], 65.0)
        self.assertGreater(plan["scheme_d"]["total_capital_required_usd"], 80.0)
        self.assertAlmostEqual(plan["scheme_d"]["theoretical_liq_price"], 80.0 / 0.342, places=2)
        # Basis and PnL fields
        self.assertIn("basis_spread_pct", plan)
        self.assertIn("basis_status", plan)
        self.assertIn("net_entry_payback_str", plan)
        self.assertIn("net_roundtrip_payback_str", plan)
        self.assertIn("multi_horizon_analysis", plan)
        self.assertIn("30d", plan["multi_horizon_analysis"])

    def test_render_arbitrage_plan(self):
        from scripts.hl_ops import render_arbitrage_plan
        self.mock_client.get_perp_market_data.return_value = (
            [{"name": "HYPE", "szDecimals": 2}],
            [{"funding": "0.0001", "midPx": "80.0", "markPx": "80.0"}]
        )
        self.mock_client.get_spot_market_data.return_value = (
            [{"name": "USDC", "index": 0}, {"name": "HYPE", "index": 150, "szDecimals": 2}],
            [{"tokens": [150, 0], "name": "@107", "index": 107}],
            [{"midPx": "80.0", "markPx": "80.0", "coin": "@107"}]
        )
        self.mock_client.get_l2_book.side_effect = lambda c: {
            "levels": [[{"px": "80.0", "sz": "100.0"}], [{"px": "80.1", "sz": "100.0"}]]
        }
        plan = self.executor.build_arbitrage_plan(coin="HYPE", amount_qty=1.0, execution_mode="taker_taker")
        res = render_arbitrage_plan(plan)
        self.assertEqual(len(res), 4)
        panel, tbl_ord, tbl_pnl, tbl_sch = res
        self.assertIsNotNone(panel)
        self.assertIsNotNone(tbl_ord)
        self.assertIsNotNone(tbl_pnl)
        self.assertIsNotNone(tbl_sch)


if __name__ == "__main__":
    unittest.main()


