import unittest
import json
import io
from unittest.mock import MagicMock
from server import ArbitrageServerHandler
from src.telegram_alert_monitor import ArbitrageAlertMonitor

class DummyWFile:
    def __init__(self):
        self.buf = io.BytesIO()

    def write(self, b):
        self.buf.write(b)

    def getvalue(self):
        return self.buf.getvalue()

class TestServerTelegramAPI(unittest.TestCase):

    def setUp(self):
        self.mock_monitor = MagicMock(spec=ArbitrageAlertMonitor)
        self.mock_monitor.exchange = "hyperliquid"
        self.mock_monitor.symbols = ["HYPER"]
        self.mock_monitor.check_spread = True
        self.mock_monitor.min_spread_pct = 0.10
        self.mock_monitor.check_funding = True
        self.mock_monitor.min_apr_pct = 20.0
        self.mock_monitor.get_status.return_value = {
            "enabled": True,
            "is_configured": True,
            "exchange": "hyperliquid",
            "symbols": ["HYPER"],
            "min_spread_pct": 0.10,
            "min_apr_pct": 20.0
        }
        self.mock_notifier = MagicMock()
        self.mock_notifier.is_configured.return_value = True
        self.mock_notifier.send_message.return_value = (True, "OK")
        self.mock_monitor.notifier = self.mock_notifier

    def test_handle_api_telegram_status(self):
        handler = ArbitrageServerHandler.__new__(ArbitrageServerHandler)
        handler.alert_monitor = self.mock_monitor
        handler.wfile = DummyWFile()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()

        handler.handle_api_telegram_status()

        handler.send_response.assert_called_with(200)
        output = json.loads(handler.wfile.getvalue().decode("utf-8"))
        self.assertEqual(output["status"], "success")
        self.assertTrue(output["data"]["enabled"])
        self.assertEqual(output["data"]["symbols"], ["HYPER"])

    def test_handle_api_telegram_test_success(self):
        handler = ArbitrageServerHandler.__new__(ArbitrageServerHandler)
        handler.alert_monitor = self.mock_monitor
        handler.wfile = DummyWFile()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()

        handler.handle_api_telegram_test()

        handler.send_response.assert_called_with(200)
        output = json.loads(handler.wfile.getvalue().decode("utf-8"))
        self.assertEqual(output["status"], "success")
        self.mock_notifier.send_message.assert_called_once()

    def test_handle_api_telegram_test_not_configured(self):
        self.mock_notifier.is_configured.return_value = False
        handler = ArbitrageServerHandler.__new__(ArbitrageServerHandler)
        handler.alert_monitor = self.mock_monitor
        handler.wfile = DummyWFile()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()

        handler.handle_api_telegram_test()

        handler.send_response.assert_called_with(400)
        output = json.loads(handler.wfile.getvalue().decode("utf-8"))
        self.assertEqual(output["status"], "error")

    def test_handle_api_auto_arbitrage_status(self):
        mock_auto_engine = MagicMock()
        mock_auto_engine.get_status.return_value = {
            "enabled": True,
            "dry_run": True,
            "min_spread_pct": 0.08,
            "capital_policy": {"per_trade_usd": 500.0}
        }
        handler = ArbitrageServerHandler.__new__(ArbitrageServerHandler)
        handler.auto_engine = mock_auto_engine
        handler.wfile = DummyWFile()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()

        handler.handle_api_auto_arbitrage_status()

        handler.send_response.assert_called_with(200)
        output = json.loads(handler.wfile.getvalue().decode("utf-8"))
        self.assertEqual(output["status"], "success")
        self.assertTrue(output["data"]["enabled"])
        self.assertEqual(output["data"]["min_spread_pct"], 0.08)

    def test_handle_api_auto_arbitrage_config(self):
        mock_auto_engine = MagicMock()
        mock_auto_engine.update_config.return_value = {
            "enabled": True,
            "min_spread_pct": 0.12
        }
        handler = ArbitrageServerHandler.__new__(ArbitrageServerHandler)
        handler.auto_engine = mock_auto_engine
        body_bytes = b'{"enabled": true, "min_spread_pct": 0.12}'
        handler.headers = {"Content-Length": str(len(body_bytes))}
        handler.rfile = io.BytesIO(body_bytes)
        handler.wfile = DummyWFile()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()

        handler.handle_api_auto_arbitrage_config()

        handler.send_response.assert_called_with(200)
        output = json.loads(handler.wfile.getvalue().decode("utf-8"))
        self.assertEqual(output["status"], "success")
        self.assertEqual(output["data"]["min_spread_pct"], 0.12)

    def test_handle_api_telegram_hourly_report(self):
        mock_reporter = MagicMock()
        mock_reporter.build_report.return_value = {
            "markdown": "Hourly report mock",
            "total_payout_usdc": 0.019
        }
        handler = ArbitrageServerHandler.__new__(ArbitrageServerHandler)
        handler.hourly_reporter = mock_reporter
        handler.wfile = DummyWFile()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()

        handler.handle_api_telegram_hourly_report()

        handler.send_response.assert_called_with(200)
        output = json.loads(handler.wfile.getvalue().decode("utf-8"))
        self.assertEqual(output["status"], "success")
        self.assertEqual(output["data"]["total_payout_usdc"], 0.019)

    def test_handle_api_telegram_hourly_status(self):
        mock_reporter = MagicMock()
        mock_reporter.get_status.return_value = {
            "enabled": True,
            "report_minute": 1,
            "total_reports_sent": 5
        }
        handler = ArbitrageServerHandler.__new__(ArbitrageServerHandler)
        handler.hourly_reporter = mock_reporter
        handler.wfile = DummyWFile()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()

        handler.handle_api_telegram_hourly_status()

        handler.send_response.assert_called_with(200)
        output = json.loads(handler.wfile.getvalue().decode("utf-8"))
        self.assertEqual(output["status"], "success")
        self.assertTrue(output["data"]["enabled"])
        self.assertEqual(output["data"]["total_reports_sent"], 5)

    def test_handle_api_telegram_hourly_send(self):
        mock_reporter = MagicMock()
        mock_reporter.notifier.is_configured.return_value = True
        mock_reporter.send_report.return_value = (True, "Report sent successfully")

        handler = ArbitrageServerHandler.__new__(ArbitrageServerHandler)
        handler.hourly_reporter = mock_reporter
        handler.wfile = DummyWFile()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()

        handler.handle_api_telegram_hourly_send()

        handler.send_response.assert_called_with(200)
        output = json.loads(handler.wfile.getvalue().decode("utf-8"))
        self.assertEqual(output["status"], "success")
        mock_reporter.send_report.assert_called_once_with(force=True)

if __name__ == "__main__":
    unittest.main()
