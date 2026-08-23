import unittest
import os
import tempfile
from unittest.mock import patch
from src.env import load_env
from src.hyperliquid_executor import HyperliquidExecutor
from src.bybit_client import BybitClient

class TestEnvLoader(unittest.TestCase):

    def test_load_env_parsing(self):
        """Tests parsing of KEY=VAL, KEY: VAL, comments, and quotes."""
        content = """
        # Comment line
        KEY_EQUALS=hello_world
        KEY_COLON: foo_bar
        KEY_QUOTED="quoted_string"
        KEY_SINGLE_QUOTED='single_quoted'
        # Invalid line without delimiter
        INVALID_LINE
        EMPTY_VALUE=
        """
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write(content)
            temp_path = f.name

        try:
            # Clear any pre-existing keys in os.environ for clean test
            for k in ["KEY_EQUALS", "KEY_COLON", "KEY_QUOTED", "KEY_SINGLE_QUOTED", "EMPTY_VALUE"]:
                os.environ.pop(k, None)

            loaded = load_env(env_path=temp_path, override=False)
            self.assertEqual(loaded.get("KEY_EQUALS"), "hello_world")
            self.assertEqual(loaded.get("KEY_COLON"), "foo_bar")
            self.assertEqual(loaded.get("KEY_QUOTED"), "quoted_string")
            self.assertEqual(loaded.get("KEY_SINGLE_QUOTED"), "single_quoted")
            self.assertEqual(os.environ.get("KEY_EQUALS"), "hello_world")
            self.assertEqual(os.environ.get("KEY_COLON"), "foo_bar")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            for k in ["KEY_EQUALS", "KEY_COLON", "KEY_QUOTED", "KEY_SINGLE_QUOTED", "EMPTY_VALUE"]:
                os.environ.pop(k, None)

    def test_env_precedence_no_override(self):
        """
        Tests that existing os.environ (e.g. from gopass env) takes precedence
        over .env when override=False.
        """
        content = """
        HL_AGENT_PRIVATE_KEY=0x0000000000000000000000000000000000000000000000000000000000000000
        HL_ACCOUNT_ADDRESS=0x87843f3E86B66bC369947aad95c7907cb49cDCB6
        """
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write(content)
            temp_path = f.name

        try:
            # Simulate real key injected via gopass env
            real_key = "0x111122223333444455556666777788889999aaaabbbbccccddddeeeeffff0000"
            os.environ["HL_AGENT_PRIVATE_KEY"] = real_key
            os.environ.pop("HL_ACCOUNT_ADDRESS", None)

            loaded = load_env(env_path=temp_path, override=False)

            # os.environ should RETAIN the injected real key, NOT the decoy
            self.assertEqual(os.environ.get("HL_AGENT_PRIVATE_KEY"), real_key)
            # Missing key should be populated from .env
            self.assertEqual(os.environ.get("HL_ACCOUNT_ADDRESS"), "0x87843f3E86B66bC369947aad95c7907cb49cDCB6")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            os.environ.pop("HL_AGENT_PRIVATE_KEY", None)
            os.environ.pop("HL_ACCOUNT_ADDRESS", None)

    def test_executor_and_bybit_init_from_env(self):
        """Tests that HyperliquidExecutor and BybitClient pick up secrets from os.environ."""
        with patch.dict(os.environ, {
            "HL_ACCOUNT_ADDRESS": "0x1234567890abcdef1234567890abcdef12345678",
            "HL_AGENT_PRIVATE_KEY": "0xabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcd",
            "BYBIT_API_KEY": "my_bybit_key",
            "BYBIT_API_SECRET": "my_bybit_secret"
        }):
            hl_exec = HyperliquidExecutor()
            self.assertEqual(hl_exec.account_address, "0x1234567890abcdef1234567890abcdef12345678")
            self.assertEqual(hl_exec.agent_private_key, "0xabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcd")

            bybit = BybitClient()
            self.assertEqual(bybit.api_key, "my_bybit_key")
            self.assertEqual(bybit.api_secret, "my_bybit_secret")


if __name__ == "__main__":
    unittest.main()
