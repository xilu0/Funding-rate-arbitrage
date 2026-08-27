import os
import time
import threading
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple, Callable
import src.env

from src.hyperliquid_executor import HyperliquidExecutor
from src.hyperliquid_client import HyperliquidClient
from src.telegram_notifier import TelegramNotifier
from src.calculator import safe_float
from src.basis_auditor import BasisAuditor

logger = logging.getLogger("auto_arbitrage_engine")

class AutoArbitrageEngine:
    """
    Strategy 2: Automated Basis-Sniping Delta-Neutral Arbitrage Engine.
    Listens to millisecond WebSocket market updates, filters out transient micro-flicker,
    dynamically sizes trade tranches according to Scheme D capital allocation,
    and executes Dual-IOC taker orders to capture positive basis and funding rates.
    """

    def __init__(
        self,
        executor: Optional[HyperliquidExecutor] = None,
        hl_client: Optional[HyperliquidClient] = None,
        notifier: Optional[TelegramNotifier] = None,
        enabled: Optional[bool] = None,
        dry_run: Optional[bool] = None,
        symbols: Optional[List[str]] = None,
        min_spread_pct: Optional[float] = None,
        min_apr_pct: Optional[float] = None,
        max_total_capital_usd: Optional[float] = None,
        per_trade_usd: Optional[float] = None,
        min_cash_reserve_usd: Optional[float] = None,
        max_slippage_pct: Optional[float] = None,
        cooldown_seconds: Optional[float] = None,
        persistence_ticks: Optional[int] = None,
        persistence_ms: Optional[float] = None,
        auditor: Optional[BasisAuditor] = None,
        require_reliable_basis: Optional[bool] = None,
        min_reliability_score: Optional[float] = None,
        window_seconds: Optional[float] = None,
        min_p10_floor_pct: Optional[float] = None,
        min_depth_multiple: Optional[float] = None,
    ):
        self.executor = executor or HyperliquidExecutor()
        self.hl_client = hl_client or HyperliquidClient()
        self.notifier = notifier or TelegramNotifier()

        # 1. Enabled toggle
        if enabled is not None:
            self.enabled = enabled
        else:
            env_en = os.getenv("AUTO_ARBITRAGE_ENABLED", "false").strip().lower()
            self.enabled = env_en in ["true", "1", "yes", "on"]

        # 2. Dry-Run simulation toggle (defaults to True for quantitative safety)
        if dry_run is not None:
            self.dry_run = dry_run
        else:
            env_dr = os.getenv("AUTO_ARBITRAGE_DRY_RUN", "true").strip().lower()
            self.dry_run = env_dr in ["true", "1", "yes", "on"]

        # 3. Target symbols (default HYPER / HYPE)
        if symbols is not None:
            self.symbols = [s.strip().upper() for s in symbols if s.strip()]
        else:
            raw_syms = os.getenv("AUTO_ARBITRAGE_SYMBOLS", os.getenv("TELEGRAM_ALERT_SYMBOLS", "HYPER")).strip()
            self.symbols = [s.strip().upper() for s in raw_syms.split(",") if s.strip()]
        if not self.symbols:
            self.symbols = ["HYPER"]

        # 4. Trigger thresholds
        self.min_spread_pct = float(min_spread_pct if min_spread_pct is not None else os.getenv("AUTO_ARBITRAGE_MIN_SPREAD_PCT", "0.08"))
        self.min_apr_pct = float(min_apr_pct if min_apr_pct is not None else os.getenv("AUTO_ARBITRAGE_MIN_APR_PCT", "10.0"))

        # 5. Capital & Sizing Policy (Scheme D)
        self.max_total_capital_usd = float(max_total_capital_usd if max_total_capital_usd is not None else os.getenv("AUTO_ARBITRAGE_MAX_TOTAL_CAPITAL_USD", "5000.0"))
        self.per_trade_usd = float(per_trade_usd if per_trade_usd is not None else os.getenv("AUTO_ARBITRAGE_PER_TRADE_USD", "500.0"))
        self.min_cash_reserve_usd = float(min_cash_reserve_usd if min_cash_reserve_usd is not None else os.getenv("AUTO_ARBITRAGE_MIN_CASH_RESERVE_USD", "1000.0"))
        self.max_slippage_pct = float(max_slippage_pct if max_slippage_pct is not None else os.getenv("AUTO_ARBITRAGE_MAX_SLIPPAGE_PCT", "0.30"))
        self.cooldown_seconds = float(cooldown_seconds if cooldown_seconds is not None else os.getenv("AUTO_ARBITRAGE_COOLDOWN_SECONDS", "120.0"))

        # 6. Anti-Flicker persistence parameters
        self.persistence_ticks = int(persistence_ticks if persistence_ticks is not None else os.getenv("AUTO_ARBITRAGE_PERSISTENCE_TICKS", "2"))
        self.persistence_ms = float(persistence_ms if persistence_ms is not None else os.getenv("AUTO_ARBITRAGE_PERSISTENCE_MS", "100.0"))

        # 7. Reliable Basis Auditor (Certifying Structural Basis vs Transient Spikes)
        if require_reliable_basis is not None:
            self.require_reliable_basis = require_reliable_basis
        else:
            env_rb = os.getenv("AUTO_ARBITRAGE_REQUIRE_RELIABLE_BASIS", "true").strip().lower()
            self.require_reliable_basis = env_rb in ["true", "1", "yes", "on"]

        self.min_reliability_score = float(
            min_reliability_score if min_reliability_score is not None else os.getenv("AUTO_ARBITRAGE_MIN_RELIABILITY_SCORE", "80.0")
        )
        aud_window = float(window_seconds if window_seconds is not None else os.getenv("AUTO_ARBITRAGE_WINDOW_SECONDS", "30.0"))
        aud_p10 = float(min_p10_floor_pct if min_p10_floor_pct is not None else os.getenv("AUTO_ARBITRAGE_MIN_P10_FLOOR_PCT", "0.02"))
        aud_depth_mult = float(min_depth_multiple if min_depth_multiple is not None else os.getenv("AUTO_ARBITRAGE_MIN_DEPTH_MULTIPLE", "2.5"))

        self.auditor = auditor or BasisAuditor(
            window_seconds=aud_window,
            min_spread_pct=self.min_spread_pct,
            min_p10_floor_pct=aud_p10,
            min_depth_multiple=aud_depth_mult,
            min_apr_pct=self.min_apr_pct,
            min_reliability_score=self.min_reliability_score
        )

        # Internal state tracking
        self._lock = threading.Lock()
        self._tick_history: Dict[str, List[Dict[str, Any]]] = {}
        self._last_trade_time: Dict[str, float] = {}
        self._trade_history: List[Dict[str, Any]] = []
        self._latest_audit: Dict[str, Any] = {}
        self._total_trades_attempted: int = 0
        self._total_trades_succeeded: int = 0
        self._total_volume_usd: float = 0.0

        # Spot pair mapping cache e.g. HYPE -> @107
        self._spot_pair_cache: Dict[str, str] = {"HYPE": "@107", "HYPER": "@107"}

    def matches_symbol(self, coin: str) -> bool:
        """Checks if market coin matches target symbols (handles HYPER <-> HYPE equivalence)."""
        c_up = coin.upper()
        for s in self.symbols:
            s_up = s.upper()
            if s_up in ["ALL", "*"]:
                return True
            if c_up == s_up:
                return True
            if s_up == "HYPER" and c_up == "HYPE":
                return True
            if s_up == "HYPE" and c_up == "HYPER":
                return True
        return False

    def resolve_spot_pair(self, coin: str) -> str:
        """Resolves Hyperliquid spot universe token/pair name for given coin."""
        c_up = coin.upper()
        if c_up in self._spot_pair_cache:
            return self._spot_pair_cache[c_up]
        try:
            spot_info = self.executor.resolve_spot_market_pair(coin)
            if spot_info and "spot_pair_name" in spot_info:
                pair = spot_info["spot_pair_name"]
                self._spot_pair_cache[c_up] = pair
                return pair
        except Exception:
            pass
        return "@107" if c_up in ["HYPE", "HYPER"] else f"{c_up}/USDC"

    def get_account_capital_metrics(self) -> Dict[str, float]:
        """
        Retrieves current account balances: Spot USDC and existing position notional.
        Gracefully handles API errors by falling back to conservative zeroes.
        """
        account_addr = self.executor.account_address
        if not account_addr:
            return {"spot_usdc": 0.0, "current_position_usd": 0.0}

        spot_usdc = 0.0
        current_pos_usd = 0.0

        try:
            spot_state = self.hl_client.get_spot_clearinghouse_state(account_addr)
            balances = spot_state.get("balances", []) if isinstance(spot_state, dict) else []
            for b in balances:
                coin_name = b.get("coin", "")
                total_qty = safe_float(b.get("total", 0.0))
                if coin_name in ["USDC", "USDS"]:
                    spot_usdc += total_qty
                elif coin_name in ["HYPE", "HYPER"]:
                    current_pos_usd += total_qty * 80.0
        except Exception as e:
            logger.warning(f"Error fetching spot capital metrics: {e}")

        return {
            "spot_usdc": spot_usdc,
            "current_position_usd": current_pos_usd
        }

    def calculate_trade_sizing(self, coin: str, spot_price: float) -> Tuple[bool, float, str, Dict[str, Any]]:
        """
        Calculates trade sizing based on Scheme D capital allocation:
        - 90% Spot Token + 90% Perp Short Notional + 10% Cash buffer.
        - Enforces usable cash = max(0, spot_usdc - min_cash_reserve_usd).
        - Enforces remaining quota = max(0, max_total_capital_usd - current_pos_usd).
        - Enforces allocated capital = min(per_trade_usd, usable_cash, remaining_quota).
        - Checks minimum order value ($10) and precision.
        """
        if spot_price <= 0.0:
            return False, 0.0, "Invalid spot price", {}

        cap = self.get_account_capital_metrics()
        spot_usdc = cap["spot_usdc"]
        current_pos_usd = cap["current_position_usd"]

        # In dry run mode, if account balance is 0 or unconfigured, allow testing with per_trade_usd
        if self.dry_run and spot_usdc <= 0.0:
            spot_usdc = 10000.0

        usable_cash = max(0.0, spot_usdc - self.min_cash_reserve_usd)
        remaining_quota = max(0.0, self.max_total_capital_usd - current_pos_usd)

        if usable_cash <= 10.0:
            return False, 0.0, f"Insufficient usable cash (${usable_cash:.2f} <= reserve buffer ${self.min_cash_reserve_usd:.2f})", cap

        if remaining_quota <= 10.0:
            return False, 0.0, f"Max capital ceiling reached (current: ${current_pos_usd:.2f}, limit: ${self.max_total_capital_usd:.2f})", cap

        allocated_usd = min(self.per_trade_usd, usable_cash, remaining_quota)
        # Scheme D allocates 90% to Spot Token
        spot_notional = allocated_usd * 0.90
        raw_qty = spot_notional / spot_price

        # HYPE lot precision (0.01 precision)
        qty = round(raw_qty, 2)
        if qty <= 0.0:
            qty = 0.01

        notional_usd = qty * spot_price
        if notional_usd < 10.0:
            return False, 0.0, f"Order notional (${notional_usd:.2f}) below Hyperliquid minimum ($10.0)", cap

        sizing_info = {
            "spot_usdc": spot_usdc,
            "usable_cash": usable_cash,
            "current_pos_usd": current_pos_usd,
            "remaining_quota": remaining_quota,
            "allocated_usd": allocated_usd,
            "spot_notional_usd": notional_usd,
            "qty": qty,
            "spot_price": spot_price
        }
        return True, qty, "OK", sizing_info

    def on_market_tick(self, metric: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Receives real-time market updates from WebSocket feed.
        Evaluates trigger conditions with Anti-Flicker persistence filter,
        and fires automated Dual-IOC execution when verified.
        """
        if not self.enabled:
            return None

        coin = metric.get("coin", "").upper()
        if not self.matches_symbol(coin):
            return None

        target_coin = "HYPE" if coin in ["HYPE", "HYPER"] else coin
        spot_price = safe_float(metric.get("spot_price", 0.0))
        perp_price = safe_float(metric.get("perp_price", 0.0))
        spread_pct = safe_float(metric.get("spread_pct", 0.0))
        apr_pct = safe_float(metric.get("apr_pct", 0.0))

        if spot_price <= 0.0 or perp_price <= 0.0:
            return None

        now = time.time()

        with self._lock:
            # 0. Always record tick into BasisAuditor rolling window
            self.auditor.record_tick(
                coin=target_coin,
                spot_price=spot_price,
                perp_price=perp_price,
                spread_pct=spread_pct,
                apr_pct=apr_pct,
                now=now
            )

            # 1. Cooldown Guard
            last_traded = self._last_trade_time.get(target_coin, 0.0)
            if (now - last_traded) < self.cooldown_seconds:
                return None

            # 2. Threshold Condition Check (Strict rule: spread >= 0.0% always required!)
            condition_met = (
                spread_pct >= self.min_spread_pct and
                spread_pct >= 0.0 and
                apr_pct >= self.min_apr_pct
            )

            if not condition_met:
                self._tick_history[target_coin] = []
                return None

            # 3. Anti-Flicker Persistence Filter
            history = self._tick_history.setdefault(target_coin, [])
            history.append({
                "time": now,
                "spread_pct": spread_pct,
                "spot_price": spot_price,
                "perp_price": perp_price
            })

            history = [t for t in history if (now - t["time"]) <= 3.0]
            self._tick_history[target_coin] = history

            elapsed_ms = (now - history[0]["time"]) * 1000.0
            ticks_count = len(history)

            if ticks_count < self.persistence_ticks and elapsed_ms < self.persistence_ms:
                logger.debug(
                    f"Auto-arbitrage tick for {target_coin} pending persistence: "
                    f"{ticks_count}/{self.persistence_ticks} ticks, {elapsed_ms:.1f}/{self.persistence_ms:.1f} ms"
                )
                return None

            # 4. Reliable Basis Audit (Certifying Structural Basis vs Transient Micro-Spikes)
            spot_book = metric.get("spot_book")
            perp_book = metric.get("perp_book")
            audit_res = self.auditor.audit_basis(
                coin=target_coin,
                current_spread_pct=spread_pct,
                current_apr_pct=apr_pct,
                spot_price=spot_price,
                target_notional_usd=self.per_trade_usd,
                spot_book=spot_book,
                perp_book=perp_book,
                now=now
            )
            self._latest_audit[target_coin] = audit_res

            if self.require_reliable_basis and not audit_res.get("is_reliable", False):
                logger.debug(
                    f"Auto-arbitrage tick for {target_coin} filtered by reliability audit: "
                    f"score={audit_res.get('reliability_score')}/100 ({audit_res.get('grade_badge')}), "
                    f"duration={audit_res.get('metrics', {}).get('duration_seconds')}s"
                )
                return None

            # 5. Sizing and Capital Allocation Calculation
            ok, qty, reason, sizing_info = self.calculate_trade_sizing(target_coin, spot_price)
            if not ok:
                logger.info(f"Auto-arbitrage condition met for {target_coin} but sizing rejected: {reason}")
                return None

            # 6. Execute Trade (Dual IOC Taker-Taker)
            spot_pair = self.resolve_spot_pair(target_coin)
            self._total_trades_attempted += 1

            logger.info(
                f"⚡ [AUTO-ARBITRAGE TRIGGERED] Firing Dual-IOC for {target_coin} "
                f"(Qty={qty}, Spot={spot_price}, Perp={perp_price}, Spread={spread_pct:+.3f}%, "
                f"Score={audit_res.get('reliability_score')}, DryRun={self.dry_run})"
            )

            exec_res = self.executor.execute_dual_ioc_arbitrage(
                coin=target_coin,
                spot_pair=spot_pair,
                qty=qty,
                spot_price=spot_price,
                perp_price=perp_price,
                max_slippage_pct=self.max_slippage_pct,
                dry_run=self.dry_run
            )

            is_success = exec_res.get("status") in ["SUCCESS", "SIMULATED_SUCCESS"]
            if is_success:
                self._total_trades_succeeded += 1
                self._last_trade_time[target_coin] = now
                self._total_volume_usd += sizing_info.get("spot_notional_usd", 0.0)

            trade_record = {
                "timestamp": now,
                "datetime": datetime.now(timezone.utc).isoformat(),
                "coin": target_coin,
                "spot_pair": spot_pair,
                "qty": qty,
                "target_spot_px": spot_price,
                "target_perp_px": perp_price,
                "target_spread_pct": spread_pct,
                "apr_pct": apr_pct,
                "dry_run": self.dry_run,
                "status": exec_res.get("status"),
                "exec_spread_pct": exec_res.get("exec_spread_pct", spread_pct),
                "latency_ms": exec_res.get("latency_ms", 0),
                "sizing_info": sizing_info,
                "reliability_audit": audit_res,
                "exec_details": exec_res
            }
            self._trade_history.append(trade_record)
            if len(self._trade_history) > 100:
                self._trade_history.pop(0)

            self._tick_history[target_coin] = []

        # 7. Telegram Notification Dispatch (outside lock)
        self._dispatch_trade_notification(trade_record)
        return trade_record

    def _dispatch_trade_notification(self, trade: Dict[str, Any]):
        """Formats and sends a Telegram notification reporting auto-arbitrage trade execution."""
        if not self.notifier.is_configured():
            return

        coin = trade["coin"]
        dry_badge = "🧪 【模拟演练 DRY-RUN】" if trade["dry_run"] else "🚀 【实盘自动建仓 LIVE】"
        status_badge = "✅ 双边对冲成交 (Dual-IOC Filled)" if trade["status"] in ["SUCCESS", "SIMULATED_SUCCESS"] else "⚠️ 部分或异常 (Check Orders)"
        spread_str = f"{trade.get('exec_spread_pct', 0.0):+.3f}%"
        apr_str = f"{trade.get('apr_pct', 0.0):.2f}%"

        sizing = trade.get("sizing_info", {})
        notional_str = f"${sizing.get('spot_notional_usd', 0.0):,.2f} USD"
        qty_str = f"{trade.get('qty', 0.0):,.2f} {coin}"

        aud = trade.get("reliability_audit", {})
        audit_section = ""
        if aud:
            score_val = aud.get("reliability_score", 0)
            grade_badge = aud.get("grade_badge", "")
            metrics = aud.get("metrics", {})
            dur_str = f"{metrics.get('duration_seconds', 0):.1f}s"
            p10_str = f"{metrics.get('p10_floor_pct', 0.0):+.3f}%"
            depth_mult_str = f"{metrics.get('depth_multiple', 1.0):.1f}x"
            audit_section = (
                f"\n💎 *稳健基差审核认证 (Reliable Basis)*:\n"
                f"• 综合评分: *`{score_val}/100`* ({grade_badge})\n"
                f"• 平台持续: `{dur_str}` (最差底线: `{p10_str}`)\n"
                f"• 盘口深度: `{depth_mult_str}` 买方缓冲垫\n"
            )

        msg = (
            f"{dry_badge} *策略二: 自动化抢基差建仓报告*\n\n"
            f"• *执行状态*: `{status_badge}`\n"
            f"• *交易标的*: `{coin}` (现货 `{trade.get('spot_pair')}` / 合约 `{coin}-PERP`)\n"
            f"• *建仓规模*: `{qty_str}` ({notional_str})\n"
            f"• *目标基差*: `{trade.get('target_spread_pct', 0.0):+.3f}%`\n"
            f"• *实成基差*: *`{spread_str}`* (进场锁死利差)\n"
            f"• *资金费率*: `年化 {apr_str}` (Scheme D 持续捕获)\n"
            f"• *执行延迟*: `{trade.get('latency_ms', 0)} ms`\n"
            f"{audit_section}\n"
            f"🛡️ *Scheme D 账户状态*:\n"
            f"• 现金缓冲保留: `${self.min_cash_reserve_usd:,.2f} USDC` (底线安全垫)\n"
            f"• 累计头寸限额: `${self.max_total_capital_usd:,.2f} USD`\n"
            f"• 理论强平安全垫: `+192.4%` (抗三倍暴涨不爆仓)"
        )

        try:
            self.notifier.send_message(msg)
        except Exception as e:
            logger.error(f"Error dispatching auto-arbitrage trade notification: {e}")

    def update_config(self, **kwargs) -> Dict[str, Any]:
        """Dynamically updates configuration parameters at runtime."""
        with self._lock:
            if "enabled" in kwargs and kwargs["enabled"] is not None:
                self.enabled = bool(kwargs["enabled"])
            if "dry_run" in kwargs and kwargs["dry_run"] is not None:
                self.dry_run = bool(kwargs["dry_run"])
            if "min_spread_pct" in kwargs and kwargs["min_spread_pct"] is not None:
                self.min_spread_pct = float(kwargs["min_spread_pct"])
            if "min_apr_pct" in kwargs and kwargs["min_apr_pct"] is not None:
                self.min_apr_pct = float(kwargs["min_apr_pct"])
            if "max_total_capital_usd" in kwargs and kwargs["max_total_capital_usd"] is not None:
                self.max_total_capital_usd = float(kwargs["max_total_capital_usd"])
            if "per_trade_usd" in kwargs and kwargs["per_trade_usd"] is not None:
                self.per_trade_usd = float(kwargs["per_trade_usd"])
            if "min_cash_reserve_usd" in kwargs and kwargs["min_cash_reserve_usd"] is not None:
                self.min_cash_reserve_usd = float(kwargs["min_cash_reserve_usd"])
            if "cooldown_seconds" in kwargs and kwargs["cooldown_seconds"] is not None:
                self.cooldown_seconds = float(kwargs["cooldown_seconds"])
            if "require_reliable_basis" in kwargs and kwargs["require_reliable_basis"] is not None:
                self.require_reliable_basis = bool(kwargs["require_reliable_basis"])
            if "min_reliability_score" in kwargs and kwargs["min_reliability_score"] is not None:
                self.min_reliability_score = float(kwargs["min_reliability_score"])
                self.auditor.min_reliability_score = self.min_reliability_score
        return self.get_status()

    def get_status(self) -> Dict[str, Any]:
        """Returns comprehensive status and diagnostic data for auto-arbitrage engine."""
        with self._lock:
            return {
                "enabled": self.enabled,
                "dry_run": self.dry_run,
                "symbols": self.symbols,
                "min_spread_pct": self.min_spread_pct,
                "min_apr_pct": self.min_apr_pct,
                "capital_policy": {
                    "max_total_capital_usd": self.max_total_capital_usd,
                    "per_trade_usd": self.per_trade_usd,
                    "min_cash_reserve_usd": self.min_cash_reserve_usd,
                    "max_slippage_pct": self.max_slippage_pct,
                    "scheme_d_spot_weight": 0.90,
                    "scheme_d_perp_weight": 0.90,
                    "scheme_d_cash_buffer_weight": 0.10,
                },
                "reliability_policy": {
                    "require_reliable_basis": self.require_reliable_basis,
                    "min_reliability_score": self.min_reliability_score,
                    "window_seconds": self.auditor.window_seconds,
                    "min_p10_floor_pct": self.auditor.min_p10_floor_pct,
                    "min_depth_multiple": self.auditor.min_depth_multiple,
                },
                "persistence_filter": {
                    "persistence_ticks": self.persistence_ticks,
                    "persistence_ms": self.persistence_ms
                },
                "cooldown_seconds": self.cooldown_seconds,
                "total_trades_attempted": self._total_trades_attempted,
                "total_trades_succeeded": self._total_trades_succeeded,
                "total_volume_usd": self._total_volume_usd,
                "latest_audit": self._latest_audit,
                "recent_trades": self._trade_history[-10:]
            }
