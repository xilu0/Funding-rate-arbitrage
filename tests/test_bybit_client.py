import unittest
from unittest.mock import patch, MagicMock
from src.bybit_client import BybitClient

class TestBybitClient(unittest.TestCase):

    @patch("urllib.request.urlopen")
    def test_get_spot_market_data(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = b'''
        {
            "retCode": 0,
            "retMsg": "OK",
            "result": {
                "list": [
                    {"symbol": "BTCUSDT", "lastPrice": "60000.0"}
                ]
            }
        }
        '''
        mock_urlopen.return_value.__enter__.return_value = mock_response

        client = BybitClient()
        tickers, instruments = client.get_spot_market_data()

        self.assertEqual(len(tickers), 1)
        self.assertEqual(tickers[0]["symbol"], "BTCUSDT")

    @patch("urllib.request.urlopen")
    def test_get_linear_market_data(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = b'''
        {
            "retCode": 0,
            "retMsg": "OK",
            "result": {
                "list": [
                    {"symbol": "BTCUSDT", "lastPrice": "60050.0", "fundingRate": "0.0001", "fundingIntervalHour": "8"}
                ]
            }
        }
        '''
        mock_urlopen.return_value.__enter__.return_value = mock_response

        client = BybitClient()
        tickers, instruments = client.get_linear_market_data()

        self.assertEqual(len(tickers), 1)
        self.assertEqual(tickers[0]["symbol"], "BTCUSDT")
        self.assertEqual(tickers[0]["fundingRate"], "0.0001")

    @patch("urllib.request.urlopen")
    def test_get_funding_rate_history(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = b'''
        {
            "retCode": 0,
            "retMsg": "OK",
            "result": {
                "category": "linear",
                "list": [
                    {"symbol": "BTCUSDT", "fundingRate": "0.0001", "fundingRateTimestamp": "1786492800000"}
                ]
            }
        }
        '''
        mock_urlopen.return_value.__enter__.return_value = mock_response

        client = BybitClient()
        history = client.get_funding_rate_history("BTCUSDT", limit=10)

        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["symbol"], "BTCUSDT")
        self.assertEqual(history[0]["fundingRate"], "0.0001")

if __name__ == "__main__":
    unittest.main()

