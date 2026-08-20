import json
import urllib.request
import urllib.error
import urllib.parse
import hmac
import hashlib
import time
import os
from typing import Dict, Any, List, Tuple, Optional

BYBIT_API_BASE_URL = "https://api.bybit.com"

class BybitClient:
    """Client for fetching market metadata and submitting private requests to Bybit V5 API."""

    def __init__(self,
                 base_url: str = BYBIT_API_BASE_URL,
                 api_key: Optional[str] = None,
                 api_secret: Optional[str] = None,
                 gopass_secret: Optional[str] = None,
                 timeout: int = 10):
        # Auto-load from gopass secret if specified
        gopass_secret = gopass_secret or os.getenv("GOPASS_BYBIT_SECRET") or os.getenv("GOPASS_SECRET")
        if gopass_secret:
            gopass_data = self.load_gopass_credentials(gopass_secret)
            if not api_key and "BYBIT_API_KEY" in gopass_data:
                api_key = gopass_data["BYBIT_API_KEY"]
            if not api_secret and "BYBIT_API_SECRET" in gopass_data:
                api_secret = gopass_data["BYBIT_API_SECRET"]

        self.base_url = base_url
        self.api_key = api_key or os.getenv("BYBIT_API_KEY")
        self.api_secret = api_secret or os.getenv("BYBIT_API_SECRET")
        self.timeout = timeout

    @staticmethod
    def load_gopass_credentials(secret_path: str) -> Dict[str, str]:
        """Loads Key-Value pairs from a multi-line gopass secret."""
        import subprocess
        try:
            res = subprocess.run(["gopass", "show", "-n", secret_path], capture_output=True, text=True, check=True)
            creds = {}
            for line in res.stdout.splitlines():
                line = line.strip()
                if ":" in line and not line.startswith("---"):
                    k, v = line.split(":", 1)
                    creds[k.strip()] = v.strip()
            return creds
        except Exception:
            return {}

    def _get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}{endpoint}"
        if params:
            query_string = urllib.parse.urlencode(params)
            url = f"{url}?{query_string}"
        
        req = urllib.request.Request(
            url,
            headers={
                'Content-Type': 'application/json',
                'User-Agent': 'BybitFundingMonitor/1.0'
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode('utf-8'))
                    if data.get("retCode") == 0:
                        return data.get("result", {})
                    else:
                        raise RuntimeError(f"Bybit API returned error code {data.get('retCode')}: {data.get('retMsg')}")
                else:
                    raise RuntimeError(f"Bybit API request failed with HTTP status {response.status}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"Failed to connect to Bybit API: {e}")

    def _post_private(self, endpoint: str, body: Dict[str, Any]) -> Dict[str, Any]:
        if not self.api_key or not self.api_secret:
            raise RuntimeError("Bybit API Key and Secret are required for private API endpoints")

        url = f"{self.base_url}{endpoint}"
        timestamp = str(int(time.time() * 1000))
        recv_window = "5000"
        json_body_str = json.dumps(body)

        param_str = timestamp + self.api_key + recv_window + json_body_str
        signature = hmac.new(bytes(self.api_secret, 'utf-8'), param_str.encode('utf-8'), hashlib.sha256).hexdigest()

        headers = {
            'Content-Type': 'application/json',
            'X-BAPI-API-KEY': self.api_key,
            'X-BAPI-SIGN': signature,
            'X-BAPI-TIMESTAMP': timestamp,
            'X-BAPI-RECV-WINDOW': recv_window,
            'User-Agent': 'BybitFundingMonitor/1.0'
        }

        req = urllib.request.Request(url, data=json_body_str.encode('utf-8'), headers=headers, method='POST')
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode('utf-8'))
                    if data.get("retCode") == 0:
                        return data.get("result", {})
                    else:
                        raise RuntimeError(f"Bybit Private API error {data.get('retCode')}: {data.get('retMsg')}")
                else:
                    raise RuntimeError(f"Bybit Private API failed with HTTP {response.status}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"Failed to connect to Bybit Private API: {e}")

    def create_order(self,
                     category: str,
                     symbol: str,
                     side: str,
                     order_type: str = "Market",
                     qty: float = 0.0,
                     price: Optional[float] = None) -> Dict[str, Any]:
        """
        Submits order to Bybit V5 /v5/order/create
        """
        body: Dict[str, Any] = {
            "category": category,
            "symbol": symbol,
            "side": side.capitalize(),
            "orderType": order_type.capitalize(),
            "qty": str(qty)
        }
        if order_type.capitalize() == "Limit" and price is not None:
            body["price"] = str(price)

        return self._post_private("/v5/order/create", body)


    def get_spot_market_data(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Fetches Bybit spot tickers and instruments info.
        Returns (tickers_list, instruments_list)
        """
        tickers_res = self._get("/v5/market/tickers", {"category": "spot"})
        instruments_res = self._get("/v5/market/instruments-info", {"category": "spot"})

        tickers = tickers_res.get("list", [])
        instruments = instruments_res.get("list", [])
        return tickers, instruments

    def get_linear_market_data(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Fetches Bybit linear perpetual tickers and instruments info.
        Returns (tickers_list, instruments_list)
        """
        tickers_res = self._get("/v5/market/tickers", {"category": "linear"})
        instruments_res = self._get("/v5/market/instruments-info", {"category": "linear", "limit": "1000"})

        tickers = tickers_res.get("list", [])
        instruments = instruments_res.get("list", [])
        return tickers, instruments

    def get_funding_rate_history(self, 
                                 symbol: str, 
                                 limit: int = 200, 
                                 start_time: Optional[int] = None, 
                                 end_time: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Fetches historical funding rates for a specified linear perpetual contract symbol on Bybit.
        Returns list of funding rate history objects.
        """
        params = {
            "category": "linear",
            "symbol": symbol,
            "limit": str(min(limit, 200))
        }
        if start_time:
            params["startTime"] = str(start_time)
            if not end_time:
                import time
                params["endTime"] = str(int(time.time() * 1000))
        if end_time:
            params["endTime"] = str(end_time)

        res = self._get("/v5/market/funding/history", params)
        return res.get("list", [])

    def get_orderbook(self, category: str, symbol: str, limit: int = 200) -> Dict[str, Any]:
        """
        Fetches Orderbook (L2 depth) for spot or linear contract on Bybit.
        Returns dict with 'b' (bids) and 'a' (asks).
        """
        params = {
            "category": category,
            "symbol": symbol,
            "limit": str(limit)
        }
        res = self._get("/v5/market/orderbook", params)
        return res


