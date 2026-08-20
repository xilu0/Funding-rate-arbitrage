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
                "totalMarginUsed": "4500.0", # > 60% of effective margin
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


if __name__ == "__main__":
    unittest.main()
