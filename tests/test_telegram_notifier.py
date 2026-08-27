import unittest
from unittest.mock import patch, MagicMock
from src.telegram_notifier import TelegramNotifier

class TestTelegramNotifier(unittest.TestCase):

    def test_is_configured(self):
        notifier_empty = TelegramNotifier(bot_token="", chat_id="")
        self.assertFalse(notifier_empty.is_configured())

        notifier_token_only = TelegramNotifier(bot_token="12345:ABC", chat_id="")
        self.assertFalse(notifier_token_only.is_configured())

        notifier_ok = TelegramNotifier(bot_token="12345:ABC", chat_id="987654321")
        self.assertTrue(notifier_ok.is_configured())

    def test_proxy_configuration(self):
        proxy_url = "http://127.0.0.1:7890"
        notifier = TelegramNotifier(
            bot_token="12345:ABC",
            chat_id="987654321",
            proxy=proxy_url
        )
        self.assertEqual(notifier.proxy, proxy_url)
        if notifier._session is not None:
            self.assertEqual(notifier._session.proxies.get("http"), proxy_url)
            self.assertEqual(notifier._session.proxies.get("https"), proxy_url)

    @patch("requests.Session.get")
    def test_verify_bot_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "ok": True,
            "result": {"id": 12345, "is_bot": True, "first_name": "ArbitrageBot", "username": "arb_bot"}
        }
        mock_get.return_value = mock_resp

        notifier = TelegramNotifier(bot_token="12345:ABC", chat_id="987654321")
        success, info = notifier.verify_bot()
        self.assertTrue(success)
        self.assertEqual(info["username"], "arb_bot")

    @patch("requests.Session.get")
    def test_verify_bot_failure(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.json.return_value = {
            "ok": False,
            "description": "Unauthorized: invalid token"
        }
        mock_get.return_value = mock_resp

        notifier = TelegramNotifier(bot_token="invalid_token", chat_id="987654321")
        success, info = notifier.verify_bot()
        self.assertFalse(success)
        self.assertIn("Unauthorized", info.get("error", ""))

    @patch("requests.Session.post")
    def test_send_message_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True, "result": {"message_id": 999}}
        mock_post.return_value = mock_resp

        notifier = TelegramNotifier(bot_token="12345:ABC", chat_id="987654321")
        success, msg = notifier.send_message("Test message", parse_mode="Markdown")
        self.assertTrue(success)
        self.assertEqual(msg, "Message sent successfully")
        self.assertEqual(mock_post.call_count, 1)

    @patch("requests.Session.post")
    def test_send_message_markdown_fallback_to_plain_text(self, mock_post):
        # First call fails with 400 (Markdown entity error), second call succeeds as plain text
        first_resp = MagicMock()
        first_resp.status_code = 400
        first_resp.json.return_value = {"ok": False, "description": "Bad Request: can't parse entities"}

        second_resp = MagicMock()
        second_resp.status_code = 200
        second_resp.json.return_value = {"ok": True, "result": {"message_id": 1000}}

        mock_post.side_effect = [first_resp, second_resp]

        notifier = TelegramNotifier(bot_token="12345:ABC", chat_id="987654321")
        success, msg = notifier.send_message("Test message with invalid *markdown", parse_mode="Markdown")
        self.assertTrue(success)
        self.assertIn("plain text fallback", msg)
        self.assertEqual(mock_post.call_count, 2)

    def test_send_message_unconfigured(self):
        notifier = TelegramNotifier(bot_token="", chat_id="")
        success, msg = notifier.send_message("Hello")
        self.assertFalse(success)
        self.assertIn("not configured", msg)

if __name__ == "__main__":
    unittest.main()
