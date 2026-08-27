import os
import time
import threading
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple, Callable
import src.env

from src.hyperliquid_client import HyperliquidClient
from src.bybit_client import BybitClient
from src.calculator import FundingRateCalculator
from src.telegram_notifier import TelegramNotifier
from src.hyperliquid_ws import HyperliquidWsFeed
from src.auto_arbitrage_engine import AutoArbitrageEngine
from src.basis_auditor import BasisAuditor

logger = logging.getLogger("arbitrage_alert_monitor")

class ArbitrageAlertMonitor:
    """
    Background monitor that periodically audits perpetual and spot market pairs,
    evaluates basis spread and funding rate conditions, and dispatches actionable
    arbitrage alerts via Telegram Bot.
    """

    def __init__(
        self,
        hl_client: Optional[HyperliquidClient] = None,
        bybit_client: Optional[BybitClient] = None,
        calculator: Optional[FundingRateCalculator] = None,
        notifier: Optional[TelegramNotifier] = None,
        enabled: Optional[bool] = None,
        exchange: Optional[str] = None,
        symbols: Optional[List[str]] = None,
        check_spread: Optional[bool] = None,
        min_spread_pct: Optional[float] = None,
        check_funding: Optional[bool] = None,
        min_apr_pct: Optional[float] = None,
        cooldown_minutes: Optional[float] = None,
        poll_interval: Optional[float] = None,
        ws_feed: Optional[HyperliquidWsFeed] = None,
        on_arbitrage_callback: Optional[Callable[[Dict[str, Any], str], None]] = None,
        auto_engine: Optional[AutoArbitrageEngine] = None,
        only_reliable: Optional[bool] = None,
    ):
        self.hl_client = hl_client or HyperliquidClient()
        self.bybit_client = bybit_client or BybitClient()
        self.calculator = calculator or FundingRateCalculator()
        self.notifier = notifier or TelegramNotifier()

        # 1. Enabled switch
        if enabled is not None:
            self.enabled = enabled
        else:
            env_enabled = os.getenv("TELEGRAM_ALERT_ENABLED", "false").strip().lower()
            self.enabled = env_enabled in ["true", "1", "yes", "on"]

        # 2. Exchange to monitor
        self.exchange = (exchange or os.getenv("TELEGRAM_ALERT_EXCHANGE", "hyperliquid")).strip().lower()

        # 3. Target symbols to monitor (default: HYPER, automatically recognizes HYPE)
        if symbols is not None:
            self.symbols = [s.strip().upper() for s in symbols if s.strip()]
        else:
            raw_syms = os.getenv("TELEGRAM_ALERT_SYMBOLS", "HYPER").strip()
            self.symbols = [s.strip().upper() for s in raw_syms.split(",") if s.strip()]
        if not self.symbols:
            self.symbols = ["HYPER"]

        # 4. Basis spread configuration (whether to check basis spread and threshold %)
        if check_spread is not None:
            self.check_spread = check_spread
        else:
            env_cs = os.getenv("TELEGRAM_ALERT_CHECK_SPREAD", "true").strip().lower()
            self.check_spread = env_cs in ["true", "1", "yes", "on"]

        if min_spread_pct is not None:
            self.min_spread_pct = float(min_spread_pct)
        else:
            try:
                self.min_spread_pct = float(os.getenv("TELEGRAM_ALERT_MIN_SPREAD_PCT", "0.10"))
            except ValueError:
                self.min_spread_pct = 0.10

        # 5. Funding rate configuration (whether to check funding rate and APR threshold %)
        if check_funding is not None:
            self.check_funding = check_funding
        else:
            env_cf = os.getenv("TELEGRAM_ALERT_CHECK_FUNDING", "true").strip().lower()
            self.check_funding = env_cf in ["true", "1", "yes", "on"]

        if min_apr_pct is not None:
            self.min_apr_pct = float(min_apr_pct)
        else:
            try:
                self.min_apr_pct = float(os.getenv("TELEGRAM_ALERT_MIN_APR_PCT", "20.0"))
            except ValueError:
                self.min_apr_pct = 20.0

        # 6. Cooldown & poll interval
        if cooldown_minutes is not None:
            self.cooldown_minutes = float(cooldown_minutes)
        else:
            try:
                self.cooldown_minutes = float(os.getenv("TELEGRAM_ALERT_COOLDOWN_MINUTES", "30"))
            except ValueError:
                self.cooldown_minutes = 30.0

        if poll_interval is not None:
            self.poll_interval = float(poll_interval)
        else:
            try:
                self.poll_interval = float(os.getenv("TELEGRAM_ALERT_POLL_INTERVAL", "30"))
            except ValueError:
                self.poll_interval = 30.0

        # Anti-spam cooldown records: key -> timestamp of last sent alert
        self._last_alerts: Dict[str, float] = {}
        # Runtime diagnostic status
        self._last_check_time: Optional[float] = None
        self._last_metrics: Dict[str, Any] = {}
        self._alert_history: List[Dict[str, Any]] = []
        self._total_alerts_sent: int = 0
        self._is_running: bool = False
        self._stop_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None

        # 7. WebSocket Feed for Hyperliquid (sub-second streaming & immediate event trigger)
        self.ws_feed = ws_feed
        if self.ws_feed is None and self.exchange in ["hyperliquid", "hl", "all"]:
            self.ws_feed = HyperliquidWsFeed(symbols=self.symbols)
        if self.ws_feed:
            self.ws_feed.add_listener(self._on_ws_market_update)

        self.on_arbitrage_callback = on_arbitrage_callback
        self.auto_engine = auto_engine

        if only_reliable is not None:
            self.only_reliable = only_reliable
        else:
            env_or = os.getenv("TELEGRAM_ALERT_ONLY_RELIABLE", "true").strip().lower()
            self.only_reliable = env_or in ["true", "1", "yes", "on"]

        self.auditor = getattr(auto_engine, "auditor", None) or BasisAuditor(
            min_spread_pct=self.min_spread_pct,
            min_apr_pct=self.min_apr_pct
        )

    def is_enabled(self) -> bool:
        """Returns True if alert monitoring is enabled and Telegram credentials exist."""
        return self.enabled and self.notifier.is_configured()

    def start(self):
        """Starts the background monitoring daemon thread and WebSocket feed."""
        if self._is_running:
            return
        if not self.is_enabled():
            logger.info("ArbitrageAlertMonitor is disabled or Telegram credentials missing.")
            return

        # Start WebSocket feed if available
        if self.ws_feed:
            self.ws_feed.start()

        self._stop_event.clear()
        self._is_running = True
        self._worker_thread = threading.Thread(
            target=self._run_loop,
            name="ArbitrageAlertMonitorThread",
            daemon=True
        )
        self._worker_thread.start()
        logger.info(
            f"ArbitrageAlertMonitor started (exchange={self.exchange}, symbols={self.symbols}, "
            f"check_spread={self.check_spread}[>={self.min_spread_pct}%], "
            f"check_funding={self.check_funding}[>={self.min_apr_pct}%], "
            f"interval={self.poll_interval}s, cooldown={self.cooldown_minutes}m)"
        )

    def stop(self):
        """Stops the background monitoring thread and WebSocket feed."""
        if not self._is_running:
            return
        self._stop_event.set()
        if self.ws_feed:
            self.ws_feed.stop()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=3.0)
        self._is_running = False
        logger.info("ArbitrageAlertMonitor stopped.")

    def _on_ws_market_update(self, metric: Dict[str, Any]):
        """
        Real-time event callback triggered by HyperliquidWsFeed upon L2/Ctx stream updates.
        Evaluates conditions with sub-second latency and dispatches Telegram notification immediately.
        """
        coin = metric.get("coin", "")
        if not self._matches_target_symbol(coin):
            return
        exchange = metric.get("exchange", "Hyperliquid")
        now = time.time()
        self._last_metrics[f"{exchange}:{coin}"] = {
            "timestamp": now,
            "spot_price": metric.get("spot_price"),
            "perp_price": metric.get("perp_price"),
            "spread_pct": metric.get("spread_pct"),
            "taker_spread_pct": metric.get("taker_spread_pct"),
            "hourly_funding_pct": metric.get("hourly_funding_pct"),
            "apr_pct": metric.get("apr_pct"),
            "source": "websocket"
        }

        # Record into BasisAuditor
        spot_px = float(metric.get("spot_price") or 0.0)
        perp_px = float(metric.get("perp_price") or 0.0)
        spread_pct = float(metric.get("spread_pct") or 0.0)
        apr_pct = float(metric.get("apr_pct") or 0.0)
        if hasattr(self, "auditor") and self.auditor:
            self.auditor.record_tick(coin, spot_px, perp_px, spread_pct, apr_pct, now=now)

        # Strategy 2: Automated Basis-Sniping execution hook (independent from human alert cooldown)
        if getattr(self, "auto_engine", None):
            try:
                self.auto_engine.on_market_tick(metric)
            except Exception as e:
                logger.error(f"Error in auto_engine.on_market_tick: {e}", exc_info=True)

        # Check conditions for human Telegram alert
        is_triggered, reasons, trigger_key = self.evaluate_conditions(metric)
        if not is_triggered:
            return

        # Perform Reliable Basis Audit
        audit_res = None
        if hasattr(self, "auditor") and self.auditor:
            audit_res = self.auditor.audit_basis(
                coin=coin,
                current_spread_pct=spread_pct,
                current_apr_pct=apr_pct,
                spot_price=spot_px,
                spot_book=metric.get("spot_book"),
                perp_book=metric.get("perp_book"),
                now=now
            )
            # If user configured to ONLY alert on reliable structural basis, filter out micro-spikes
            if self.only_reliable and not audit_res.get("is_reliable", False):
                logger.debug(
                    f"Telegram alert for {coin} skipped: only_reliable=True and grade={audit_res.get('grade')}"
                )
                return

        cooldown_key = f"{exchange}:{coin}:{trigger_key}"
        last_sent = self._last_alerts.get(cooldown_key, 0.0)
        cooldown_secs = self.cooldown_minutes * 60.0

        if (now - last_sent) < cooldown_secs:
            return

        # Pre-hook for future automated execution if configured
        if self.on_arbitrage_callback:
            try:
                self.on_arbitrage_callback(metric, trigger_key)
            except Exception as e:
                logger.error(f"Error in on_arbitrage_callback: {e}", exc_info=True)

        msg = self.format_alert_message(metric, reasons, trigger_key, audit_result=audit_res)
        success, err_or_msg = self.notifier.send_message(msg)

        alert_record = {
            "timestamp": now,
            "datetime": datetime.now(timezone.utc).isoformat(),
            "exchange": exchange,
            "coin": coin,
            "reasons": reasons,
            "trigger_key": trigger_key,
            "success": success,
            "response": err_or_msg,
            "source": "websocket",
            "reliability_audit": audit_res,
            "metrics": {
                "spot_price": metric.get("spot_price"),
                "perp_price": metric.get("perp_price"),
                "spread_pct": metric.get("spread_pct"),
                "taker_spread_pct": metric.get("taker_spread_pct"),
                "apr_pct": metric.get("apr_pct"),
            }
        }
        self._alert_history.append(alert_record)
        if len(self._alert_history) > 100:
            self._alert_history.pop(0)

        if success:
            self._last_alerts[cooldown_key] = now
            self._total_alerts_sent += 1
            logger.info(f"⚡ [Realtime WS Alert] Telegram alert sent for {exchange}:{coin} ({trigger_key})")
        else:
            logger.error(f"Failed to send Telegram alert for {exchange}:{coin}: {err_or_msg}")

    def _run_loop(self):
        while not self._stop_event.is_set():
            try:
                self.poll_once()
            except Exception as e:
                logger.error(f"Error in ArbitrageAlertMonitor loop: {e}", exc_info=True)
            self._stop_event.wait(timeout=self.poll_interval)

    def _matches_target_symbol(self, coin: str) -> bool:
        """
        Determines if a market coin matches target symbols.
        Special handling: 'HYPER' matches both 'HYPER' and 'HYPE' on Hyperliquid.
        """
        c_upper = coin.upper()
        for sym in self.symbols:
            s_upper = sym.upper()
            if s_upper in ["ALL", "*"]:
                return True
            if c_upper == s_upper:
                return True
            # HYPER <-> HYPE alias equivalence for Hyperliquid ecosystem
            if s_upper == "HYPER" and c_upper == "HYPE":
                return True
            if s_upper == "HYPE" and c_upper == "HYPER":
                return True
            # Strip standard stablecoin suffixes e.g. BTCUSDT -> BTC
            if c_upper.endswith("USDT") and c_upper[:-4] == s_upper:
                return True
            if c_upper.endswith("USDC") and c_upper[:-4] == s_upper:
                return True
        return False

    def fetch_market_metrics(self) -> List[Dict[str, Any]]:
        """Fetches and computes spot-perp arbitrage metrics from target exchange(s)."""
        metrics = []

        # 1. Hyperliquid
        if self.exchange in ["hyperliquid", "hl", "all"]:
            try:
                p_univ, p_ctxs = self.hl_client.get_perp_market_data()
                s_toks, s_univ, s_ctxs = self.hl_client.get_spot_market_data()
                hl_results = self.calculator.match_and_calculate(
                    p_univ, p_ctxs, s_toks, s_univ, s_ctxs
                )
                metrics.extend(hl_results)
            except Exception as e:
                logger.warning(f"Failed to fetch Hyperliquid market data for alert monitor: {e}")

        # 2. Bybit
        if self.exchange in ["bybit", "all"]:
            try:
                linear_tickers, linear_insts = self.bybit_client.get_linear_market_data()
                spot_tickers, spot_insts = self.bybit_client.get_spot_market_data()
                bybit_results = self.calculator.match_and_calculate_bybit(
                    linear_tickers, spot_tickers, linear_insts, spot_insts
                )
                metrics.extend(bybit_results)
            except Exception as e:
                logger.warning(f"Failed to fetch Bybit market data for alert monitor: {e}")

        return metrics

    def evaluate_conditions(self, metric: Dict[str, Any]) -> Tuple[bool, List[str], str]:
        """
        Evaluates whether a metric triggers an alert based on check_spread and check_funding.
        Returns (is_triggered, reasons_list, trigger_key).
        """
        spread_pct = metric.get("spread_pct", 0.0)
        apr_pct = metric.get("apr_pct", 0.0)

        spread_triggered = bool(self.check_spread and spread_pct >= self.min_spread_pct)
        funding_triggered = bool(self.check_funding and apr_pct >= self.min_apr_pct)

        reasons = []
        if spread_triggered and funding_triggered:
            trigger_key = "both"
            reasons.append(f"⚡ 基差与资金费率双重共振 (基差 {spread_pct:+.3f}% >= {self.min_spread_pct:+.3f}% 且 年化 {apr_pct:+.2f}% >= {self.min_apr_pct:+.2f}%)")
        elif spread_triggered:
            trigger_key = "spread"
            reasons.append(f"⚡ 基差扩大突破阈值 (当前基差 {spread_pct:+.3f}% >= 阈值 {self.min_spread_pct:+.3f}%)")
        elif funding_triggered:
            trigger_key = "funding"
            reasons.append(f"💰 资金费率突破阈值 (当前年化 {apr_pct:+.2f}% >= 阈值 {self.min_apr_pct:+.2f}%)")
        else:
            trigger_key = "none"

        is_triggered = spread_triggered or funding_triggered
        return is_triggered, reasons, trigger_key

    def format_alert_message(
        self,
        metric: Dict[str, Any],
        reasons: List[str],
        trigger_key: str,
        audit_result: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Formats structured, quantitative Telegram alert notification with Markdown.
        Always displays current funding rate and basis spread regardless of trigger mode.
        """
        exchange = metric.get("exchange", "Hyperliquid")
        coin = metric.get("coin", "HYPE")
        spot_pair = metric.get("spot_pair", f"{coin}/USDC")
        spot_price = metric.get("spot_price", 0.0)
        perp_price = metric.get("perp_price", 0.0)
        spread_pct = metric.get("spread_pct", 0.0)
        hourly_funding_pct = metric.get("hourly_funding_pct", 0.0)
        funding_interval_hr = metric.get("funding_interval_hr", 1.0)
        apr_pct = metric.get("apr_pct", 0.0)
        apy_pct = metric.get("apy_pct", 0.0)
        roundtrip_payback_str = metric.get("roundtrip_payback_str", "N/A")

        # Badge & Strategy Note
        if trigger_key == "both":
            trigger_badge = "🔥 *【基差与费率双重共振】*"
            analysis_note = (
                "正基差扩大的同时资金费率高企，Delta 中性建仓不仅能立即锁定基差额外收益，"
                "还能持续吃取高额资金费率，回本周期极短！"
            )
        elif trigger_key == "spread":
            trigger_badge = "⚡ *【基差扩大先行提醒 (费率前瞻)】*"
            analysis_note = (
                "现货与合约基差作为*先行指标*已显著扩大！基差走阔通常会驱动后续资金费率攀升。"
                "在费率滞后结算上涨前提前建仓，可锁定正基差折价安全垫。"
            )
        else:
            trigger_badge = "💰 *【高额资金费率提醒】*"
            analysis_note = (
                "当前资金费率年化收益丰厚，适合构建 1:1 现货质押/对冲头寸稳定获取费率现金流。"
            )

        spread_icon = "🟢" if spread_pct > 0 else "🔴"
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        audit_section = ""
        if audit_result:
            score = audit_result.get("reliability_score", 0)
            badge = audit_result.get("grade_badge", "")
            metrics = audit_result.get("metrics", {})
            dur = metrics.get("duration_seconds", 0)
            p10 = metrics.get("p10_floor_pct", 0)
            depth_m = metrics.get("depth_multiple", 1.0)
            advice = audit_result.get("advice", "")
            audit_section = (
                f"💎 *基差稳健性审核 (Reliable Basis Audit)*:\n"
                f"• 稳健评级: *`{score}/100`* ({badge})\n"
                f"• 平台持续: `{dur:.1f}s` (最差底线: `{p10:+.3f}%`)\n"
                f"• 盘口深度: `{depth_m:.1f}x` 买方挂单缓冲\n"
                f"• 量化建议: _{advice}_\n\n"
            )

        msg = (
            f"🚨 *【资金费率套利建仓提醒】*\n"
            f"🏛️ *交易所*: `{exchange}`\n"
            f"🪙 *标的*: `{coin}` (现货: `{spot_pair}`)\n"
            f"🎯 *触发类型*: {trigger_badge}\n\n"
            f"📋 *触发详情*:\n"
            + "\n".join([f"• {r}" for r in reasons]) + "\n\n"
            f"📊 *实时核心指标*:\n"
            f"• 合约价格: `${perp_price:,.4f}`\n"
            f"• 现货价格: `${spot_price:,.4f}`\n"
            f"• 实时基差: `{spread_pct:+.3f}%` {spread_icon}\n"
            f"• 周期费率: `{hourly_funding_pct:+.5f}%` ({funding_interval_hr:.0f}h)\n"
            f"• 年化收益: Simple APR `{apr_pct:+.2f}%` | Compound APY `{apy_pct:+.2f}%`\n"
            f"• 预估回本: `{roundtrip_payback_str}`\n\n"
            f"{audit_section}"
            f"💡 *量化分析*:\n"
            f"{analysis_note}\n\n"
            f"🛠️ *快速建仓指令 (默认 Taker-Taker 双边吃单)*:\n"
            f"`hl-ops arbitrage --coin {coin} --qty 10 --force`\n"
            f"🌐 Web 演练与构建: [Web Dashboard](http://localhost:8000/#build)\n\n"
            f"⏰ 触发时间: `{now_utc} UTC`"
        )
        return msg

    def poll_once(self) -> List[Dict[str, Any]]:
        """
        Executes a single monitoring cycle:
        1. Fetches all spot-perp metrics.
        2. Filters for target symbols.
        3. Evaluates alert conditions (spread and/or funding).
        4. Checks cooldown.
        5. Sends Telegram notification if triggered.
        """
        now = time.time()
        self._last_check_time = now

        metrics = self.fetch_market_metrics()
        matched = [m for m in metrics if self._matches_target_symbol(m.get("coin", ""))]

        dispatched = []
        for m in matched:
            coin = m.get("coin", "")
            exchange = m.get("exchange", "")
            self._last_metrics[f"{exchange}:{coin}"] = {
                "timestamp": now,
                "spot_price": m.get("spot_price"),
                "perp_price": m.get("perp_price"),
                "spread_pct": m.get("spread_pct"),
                "hourly_funding_pct": m.get("hourly_funding_pct"),
                "apr_pct": m.get("apr_pct"),
            }

            is_triggered, reasons, trigger_key = self.evaluate_conditions(m)
            if not is_triggered:
                continue

            # Audit basis
            audit_res = None
            if hasattr(self, "auditor") and self.auditor:
                spot_px = float(m.get("spot_price") or 0.0)
                perp_px = float(m.get("perp_price") or 0.0)
                spread_pct = float(m.get("spread_pct") or 0.0)
                apr_pct = float(m.get("apr_pct") or 0.0)
                audit_res = self.auditor.audit_basis(
                    coin=coin,
                    current_spread_pct=spread_pct,
                    current_apr_pct=apr_pct,
                    spot_price=spot_px,
                    now=now
                )
                if self.only_reliable and not audit_res.get("is_reliable", False):
                    logger.debug(
                        f"Poll alert for {coin} skipped: only_reliable=True and grade={audit_res.get('grade')}"
                    )
                    continue

            cooldown_key = f"{exchange}:{coin}:{trigger_key}"
            last_sent = self._last_alerts.get(cooldown_key, 0.0)
            cooldown_secs = self.cooldown_minutes * 60.0

            if (now - last_sent) < cooldown_secs:
                logger.debug(f"Alert for {cooldown_key} in cooldown ({now - last_sent:.0f}s < {cooldown_secs:.0f}s).")
                continue

            msg = self.format_alert_message(m, reasons, trigger_key, audit_result=audit_res)
            success, err_or_msg = self.notifier.send_message(msg)

            alert_record = {
                "timestamp": now,
                "datetime": datetime.now(timezone.utc).isoformat(),
                "exchange": exchange,
                "coin": coin,
                "reasons": reasons,
                "trigger_key": trigger_key,
                "success": success,
                "response": err_or_msg,
                "metrics": {
                    "spot_price": m.get("spot_price"),
                    "perp_price": m.get("perp_price"),
                    "spread_pct": m.get("spread_pct"),
                    "apr_pct": m.get("apr_pct"),
                }
            }
            self._alert_history.append(alert_record)
            if len(self._alert_history) > 100:
                self._alert_history.pop(0)

            if success:
                self._last_alerts[cooldown_key] = now
                self._total_alerts_sent += 1
                dispatched.append(alert_record)
                logger.info(f"Telegram alert sent for {exchange}:{coin} ({trigger_key})")
            else:
                logger.error(f"Failed to send Telegram alert for {exchange}:{coin}: {err_or_msg}")

        return dispatched

    def get_status(self) -> Dict[str, Any]:
        """Returns comprehensive status dictionary for API and diagnostics."""
        return {
            "enabled": self.enabled,
            "is_configured": self.notifier.is_configured(),
            "is_running": self._is_running,
            "exchange": self.exchange,
            "symbols": self.symbols,
            "check_spread": self.check_spread,
            "min_spread_pct": self.min_spread_pct,
            "only_reliable": self.only_reliable,
            "check_funding": self.check_funding,
            "min_apr_pct": self.min_apr_pct,
            "cooldown_minutes": self.cooldown_minutes,
            "poll_interval": self.poll_interval,
            "last_check_timestamp": self._last_check_time,
            "last_check_datetime": datetime.fromtimestamp(self._last_check_time, tz=timezone.utc).isoformat() if self._last_check_time else None,
            "total_alerts_sent": self._total_alerts_sent,
            "ws_feed": self.ws_feed.get_health() if self.ws_feed else {"ws_connected": False, "enabled": False},
            "last_metrics": self._last_metrics,
            "recent_alerts": self._alert_history[-10:],
            "auto_arbitrage": self.auto_engine.get_status() if self.auto_engine else None,
        }
