import json
import urllib.request
import urllib.error
from typing import Dict, Any, Tuple, Optional

HYPERLIQUID_API_URL = "https://api.hyperliquid.xyz/info"

class HyperliquidClient:
    """Client for fetching market metadata and asset contexts from Hyperliquid API."""

    def __init__(self, api_url: str = HYPERLIQUID_API_URL, timeout: int = 10):
        self.api_url = api_url
        self.timeout = timeout

    def _post(self, payload: Dict[str, Any]) -> Any:
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            self.api_url,
            data=data,
            headers={'Content-Type': 'application/json', 'User-Agent': 'HyperliquidFundingMonitor/1.0'}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                if response.status == 200:
                    return json.loads(response.read().decode('utf-8'))
                else:
                    raise RuntimeError(f"API request failed with status HTTP {response.status}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"Failed to connect to Hyperliquid API: {e}")

    def get_perp_market_data(self) -> Tuple[list, list]:
        """
        Fetches perpetual contracts metadata and asset contexts.
        Returns (universe_list, asset_ctxs_list)
        """
        res = self._post({"type": "metaAndAssetCtxs"})
        if isinstance(res, list) and len(res) >= 2:
            universe = res[0].get("universe", [])
            asset_ctxs = res[1]
            return universe, asset_ctxs
        raise ValueError("Invalid response format for metaAndAssetCtxs")

    def get_spot_market_data(self) -> Tuple[list, list, list]:
        """
        Fetches spot market metadata and asset contexts.
        Returns (tokens_list, universe_list, asset_ctxs_list)
        """
        res = self._post({"type": "spotMetaAndAssetCtxs"})
        if isinstance(res, list) and len(res) >= 2:
            tokens = res[0].get("tokens", [])
            universe = res[0].get("universe", [])
            asset_ctxs = res[1]
            return tokens, universe, asset_ctxs
        raise ValueError("Invalid response format for spotMetaAndAssetCtxs")

    def get_l2_book(self, coin: str) -> Dict[str, Any]:
        """
        Fetches L2 Orderbook for a perpetual or spot coin on Hyperliquid.
        Returns dict with 'levels': [bids, asks] where levels[0] is bids, levels[1] is asks.
        """
        res = self._post({"type": "l2Book", "coin": coin})
        if isinstance(res, dict) and "levels" in res:
            return res
        return {"levels": [[], []]}
