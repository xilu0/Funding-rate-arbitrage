import os
import json
import time
import threading
import logging
from typing import Dict, Any, List, Optional, Callable, Set, Tuple

try:
    import websocket
    HAS_WEBSOCKET = True
except ImportError:
    websocket = None
    HAS_WEBSOCKET = False

import src.env
from src.calculator import safe_float

logger = logging.getLogger("hyperliquid_ws")

class HyperliquidWsFeed:
    """
    High-performance real-time WebSocket client for Hyperliquid DEX.
    Subscribes to L2 Orderbooks (perp and spot) and Asset Contexts (funding rates),
    computes real-time basis spread and APR with sub-second latency,
    and handles automatic reconnection with prominent error logging.
    """

    DEFAULT_WS_URL = "wss://api.hyperliquid.xyz/ws"

    def __init__(
        self,
        ws_url: Optional[str] = None,
        symbols: Optional[List[str]] = None,
        spot_mappings: Optional[Dict[str, str]] = None,
        ping_interval: float = 30.0,
        reconnect_delay: float = 2.0
    ):
        self.ws_url = ws_url or os.getenv("HL_WS_URL", self.DEFAULT_WS_URL)
        # Monitored perpetual coins (e.g. ['HYPE'])
        self.symbols = [s.strip().upper() for s in (symbols or ["HYPE"])]
        # Map perpetual coin to its spot raw index (e.g. 'HYPE' -> '@107')
        self.coin_to_spot_raw: Dict[str, str] = spot_mappings or {"HYPE": "@107", "HYPER": "@107"}
        self.spot_raw_to_coin: Dict[str, str] = {v: k for k, v in self.coin_to_spot_raw.items()}

        self.ping_interval = ping_interval
        self.reconnect_delay = reconnect_delay

        # Market data caches (protected by lock)
        self._lock = threading.Lock()
        self.perp_books: Dict[str, Dict[str, Any]] = {}
        self.spot_books: Dict[str, Dict[str, Any]] = {}
        self.perp_ctxs: Dict[str, Dict[str, Any]] = {}
        self.mids: Dict[str, float] = {}
        self.latest_metrics: Dict[str, Dict[str, Any]] = {}

        # Connection health and statistics
        self.is_connected = False
        self.last_msg_time: Optional[float] = None
        self.last_error: Optional[str] = None
        self.last_error_time: Optional[float] = None
        self.error_count = 0
        self.reconnect_count = 0

        # Lifecycle management
        self._stop_event = threading.Event()
        self._ws_app: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._ping_thread: Optional[threading.Thread] = None
        self._listeners: List[Callable[[Dict[str, Any]], None]] = []

    def add_listener(self, callback: Callable[[Dict[str, Any]], None]):
        """Registers a listener callback invoked whenever real-time metrics update."""
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[Dict[str, Any]], None]):
        """Unregisters a listener callback."""
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def set_spot_mapping(self, coin: str, spot_raw: str):
        """Sets or updates mapping between coin and its spot raw symbol (e.g. HYPE -> @107)."""
        with self._lock:
            self.coin_to_spot_raw[coin.upper()] = spot_raw
            self.spot_raw_to_coin[spot_raw] = coin.upper()

    def start(self):
        """Starts WebSocket client in a background daemon thread."""
        if not HAS_WEBSOCKET:
            logger.warning("websocket-client library is not installed. WebSocket feed disabled.")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_reconnect_loop, name="HyperliquidWsLoop", daemon=True)
        self._thread.start()
        logger.info(f"HyperliquidWsFeed background runner started for symbols {self.symbols}")

    def stop(self):
        """Stops WebSocket client and joins thread."""
        self._stop_event.set()
        if self._ws_app:
            try:
                self._ws_app.close()
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self.is_connected = False
        logger.info("HyperliquidWsFeed stopped.")

    def _run_reconnect_loop(self):
        """Continuous reconnection loop with prominent error logging upon failure/disconnection."""
        if not HAS_WEBSOCKET:
            return
        backoff = self.reconnect_delay
        while not self._stop_event.is_set():
            try:
                logger.info(f"Connecting to Hyperliquid WebSocket: {self.ws_url}")
                self._ws_app = websocket.WebSocketApp(
                    self.ws_url,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                # run_forever blocks until disconnection
                self._ws_app.run_forever(ping_interval=self.ping_interval, ping_timeout=10)
            except Exception as e:
                self.error_count += 1
                self.last_error = str(e)
                self.last_error_time = time.time()
                logger.error(f"❌ [Hyperliquid WS] 行情连接异常崩溃: {e}", exc_info=True)

            self.is_connected = False
            if self._stop_event.is_set():
                break

            self.reconnect_count += 1
            logger.warning(
                f"⚠️ [Hyperliquid WS] 行情断开，将在 {backoff:.1f} 秒后重新建立连接 (累计重连: {self.reconnect_count} 次)..."
            )
            self._stop_event.wait(timeout=backoff)
            backoff = min(backoff * 1.5, 30.0)

    def _on_open(self, ws):
        """WebSocket connection open callback. Dispatches subscriptions."""
        self.is_connected = True
        self.last_msg_time = time.time()
        logger.info(f"✅ [Hyperliquid WS] 行情长连接建立成功 (URL: {self.ws_url})，正在发送订阅...")

        # 1. Subscribe to allMids for broad market awareness
        self._send_subscribe(ws, {"type": "allMids"})

        # 2. Subscribe to target perpetual coins (l2Book & activeAssetCtx)
        subscribed_coins: Set[str] = set()
        for sym in self.symbols:
            # Map HYPER <-> HYPE
            norm_sym = "HYPE" if sym in ["HYPER", "HYPE"] else sym
            if norm_sym not in subscribed_coins:
                subscribed_coins.add(norm_sym)
                self._send_subscribe(ws, {"type": "l2Book", "coin": norm_sym})
                self._send_subscribe(ws, {"type": "activeAssetCtx", "coin": norm_sym})

                # Subscribe to spot l2Book
                spot_raw = self.coin_to_spot_raw.get(norm_sym)
                if spot_raw:
                    self._send_subscribe(ws, {"type": "l2Book", "coin": spot_raw})

        logger.info(f"✅ [Hyperliquid WS] 已完成目标币种订阅: {list(subscribed_coins)}")

    def _send_subscribe(self, ws, subscription: Dict[str, Any]):
        try:
            payload = json.dumps({"method": "subscribe", "subscription": subscription})
            ws.send(payload)
        except Exception as e:
            logger.error(f"❌ [Hyperliquid WS] 发送订阅失败 {subscription}: {e}")

    def _on_error(self, ws, error):
        """WebSocket error callback with prominent error logging."""
        self.error_count += 1
        self.last_error = str(error)
        self.last_error_time = time.time()
        logger.error(f"❌ [Hyperliquid WS] 行情连接发生错误: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        """WebSocket close callback with prominent error logging."""
        self.is_connected = False
        logger.error(
            f"❌ [Hyperliquid WS] 行情长连接已中断 (CloseCode: {close_status_code}, Msg: {close_msg})！"
        )

    def _on_message(self, ws, message: str):
        """Handles incoming WebSocket stream message."""
        self.last_msg_time = time.time()
        if not message or message == "Websocket connection established.":
            return

        try:
            msg = json.loads(message)
        except Exception:
            return

        ch = msg.get("channel")
        data = msg.get("data")

        if ch == "pong":
            return
        elif ch == "subscriptionResponse":
            logger.debug(f"WS subscription response: {data}")
            return
        elif ch == "allMids" and isinstance(data, dict):
            raw_mids = data.get("mids", {})
            with self._lock:
                for k, v in raw_mids.items():
                    self.mids[k] = safe_float(v)
            return

        updated_coins: Set[str] = set()

        if ch == "activeAssetCtx" and isinstance(data, dict):
            coin = data.get("coin", "")
            ctx = data.get("ctx", {})
            if coin and ctx:
                with self._lock:
                    self.perp_ctxs[coin] = ctx
                updated_coins.add(coin)

        elif ch == "l2Book" and isinstance(data, dict):
            coin = data.get("coin", "")
            levels = data.get("levels", [[], []])
            bids = levels[0] if len(levels) > 0 else []
            asks = levels[1] if len(levels) > 1 else []
            book_entry = {
                "bids": bids,
                "asks": asks,
                "time": data.get("time", time.time() * 1000)
            }

            with self._lock:
                if coin.startswith("@"):
                    self.spot_books[coin] = book_entry
                    mapped_base = self.spot_raw_to_coin.get(coin)
                    if mapped_base:
                        updated_coins.add(mapped_base)
                else:
                    self.perp_books[coin] = book_entry
                    updated_coins.add(coin)

        # Trigger real-time calculation and notify listeners
        for coin in updated_coins:
            metrics = self._calculate_realtime_metrics(coin)
            if metrics:
                with self._lock:
                    self.latest_metrics[coin] = metrics
                    # Also alias HYPER -> HYPE metrics
                    if coin == "HYPE":
                        self.latest_metrics["HYPER"] = metrics
                    listeners_copy = list(self._listeners)

                for cb in listeners_copy:
                    try:
                        cb(metrics)
                    except Exception as e:
                        logger.error(f"Error in WS metric listener: {e}", exc_info=True)

    def _calculate_realtime_metrics(self, coin: str) -> Optional[Dict[str, Any]]:
        """
        Calculates real-time spot-perp arbitrage metrics (spread %, APR %, prices)
        from in-memory L2 books and asset context.
        """
        norm_coin = "HYPE" if coin in ["HYPER", "HYPE"] else coin
        spot_raw = self.coin_to_spot_raw.get(norm_coin, "@107")

        with self._lock:
            perp_book = self.perp_books.get(norm_coin)
            spot_book = self.spot_books.get(spot_raw)
            ctx = self.perp_ctxs.get(norm_coin, {})

        if not perp_book:
            return None

        perp_bids = perp_book.get("bids", [])
        perp_asks = perp_book.get("asks", [])
        perp_bid0 = safe_float(perp_bids[0].get("px") if perp_bids else 0.0)
        perp_ask0 = safe_float(perp_asks[0].get("px") if perp_asks else 0.0)
        perp_mid = (perp_bid0 + perp_ask0) / 2.0 if (perp_bid0 > 0 and perp_ask0 > 0) else (perp_bid0 or perp_ask0)

        spot_bid0 = 0.0
        spot_ask0 = 0.0
        spot_mid = 0.0
        if spot_book:
            spot_bids = spot_book.get("bids", [])
            spot_asks = spot_book.get("asks", [])
            spot_bid0 = safe_float(spot_bids[0].get("px") if spot_bids else 0.0)
            spot_ask0 = safe_float(spot_asks[0].get("px") if spot_asks else 0.0)
            spot_mid = (spot_bid0 + spot_ask0) / 2.0 if (spot_bid0 > 0 and spot_ask0 > 0) else (spot_bid0 or spot_ask0)

        # Fallback to mids cache if L2 book not yet populated
        if spot_mid <= 0.0:
            spot_mid = self.mids.get(spot_raw, 0.0)
        if perp_mid <= 0.0:
            perp_mid = self.mids.get(norm_coin, 0.0)

        if spot_mid <= 0.0 or perp_mid <= 0.0:
            return None

        # Basis spread %: (perp_mid - spot_mid) / spot_mid * 100
        spread_pct = ((perp_mid - spot_mid) / spot_mid * 100.0)

        # Taker executable spread: (perp_bid0 - spot_ask0) / spot_ask0 * 100
        taker_spread_pct = (
            ((perp_bid0 - spot_ask0) / spot_ask0 * 100.0)
            if (perp_bid0 > 0 and spot_ask0 > 0)
            else spread_pct
        )

        hourly_funding = safe_float(ctx.get("funding", 0.0))
        apr_pct = hourly_funding * 24.0 * 365.0 * 100.0
        try:
            apy_pct = ((1.0 + hourly_funding) ** (24.0 * 365.0) - 1.0) * 100.0
        except OverflowError:
            apy_pct = float("inf")

        # Payback estimation (approx. taker-taker roundtrip fee ~ 0.11%)
        roundtrip_fee = 0.001104
        net_friction = roundtrip_fee - (spread_pct / 100.0)
        if net_friction <= 0:
            payback_str = "0.0h (正基差即刻回本)"
        elif hourly_funding > 0:
            payback_hrs = net_friction / hourly_funding
            payback_str = f"{payback_hrs:.1f}h"
        else:
            payback_str = "N/A (费率为负)"

        return {
            "exchange": "Hyperliquid",
            "coin": norm_coin,
            "display_coin": coin,
            "spot_pair": f"{norm_coin}/USDC",
            "raw_spot_pair": spot_raw,
            "spot_price": spot_mid,
            "perp_price": perp_mid,
            "spot_bid0": spot_bid0,
            "spot_ask0": spot_ask0,
            "perp_bid0": perp_bid0,
            "perp_ask0": perp_ask0,
            "spread_pct": spread_pct,
            "taker_spread_pct": taker_spread_pct,
            "hourly_funding": hourly_funding,
            "hourly_funding_pct": hourly_funding * 100.0,
            "funding_interval_hr": 1.0,
            "apr_pct": apr_pct,
            "apy_pct": apy_pct,
            "roundtrip_payback_str": payback_str,
            "timestamp": time.time(),
            "source": "websocket"
        }

    def get_metrics(self, coin: str) -> Optional[Dict[str, Any]]:
        """Returns the latest real-time metrics for a coin."""
        norm_coin = "HYPE" if coin.upper() in ["HYPER", "HYPE"] else coin.upper()
        with self._lock:
            return self.latest_metrics.get(norm_coin)

    def get_health(self) -> Dict[str, Any]:
        """Returns health status of the WebSocket feed."""
        return {
            "ws_connected": self.is_connected,
            "has_websocket_pkg": HAS_WEBSOCKET,
            "ws_url": self.ws_url,
            "last_msg_time": self.last_msg_time,
            "last_error": self.last_error,
            "last_error_time": self.last_error_time,
            "error_count": self.error_count,
            "reconnect_count": self.reconnect_count,
            "symbols": self.symbols,
        }
