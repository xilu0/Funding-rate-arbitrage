import os
import time
import threading
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple
import src.env

from src.hyperliquid_client import HyperliquidClient
from src.bybit_client import BybitClient
from src.telegram_notifier import TelegramNotifier
from src.calculator import FundingRateCalculator, safe_float
from src.hyperliquid_executor import HyperliquidExecutor

logger = logging.getLogger("hourly_funding_reporter")


class HourlyFundingReporter:
    """
    Automated Reporter that monitors funding settlements, aggregates hourly
    funding profits and cumulative earnings, evaluates Scheme D portfolio health,
    and broadcasts institutional-grade Markdown digests to Telegram every hour.
    """

    def __init__(
        self,
        hl_client: Optional[HyperliquidClient] = None,
        bybit_client: Optional[BybitClient] = None,
        notifier: Optional[TelegramNotifier] = None,
        account_address: Optional[str] = None,
        enabled: Optional[bool] = None,
        report_minute: Optional[int] = None,
        show_market: Optional[bool] = None,
        only_on_payout: Optional[bool] = None,
        symbols: Optional[List[str]] = None,
    ):
        self.hl_client = hl_client or HyperliquidClient()
        self.bybit_client = bybit_client or BybitClient()
        self.notifier = notifier or TelegramNotifier()
        self.calculator = FundingRateCalculator()

        # Master Account address (read-only queries)
        self.account_address = (
            account_address
            if account_address is not None
            else os.getenv("HL_ACCOUNT_ADDRESS", "")
        ).strip()

        # Executor for Scheme D health & risk evaluation (read-only)
        self.executor = HyperliquidExecutor(
            account_address=self.account_address if self.account_address else None,
            hl_client=self.hl_client
        )

        # 1. Enabled flag
        if enabled is not None:
            self.enabled = enabled
        else:
            env_val = os.getenv("TELEGRAM_HOURLY_REPORT_ENABLED", "").strip().lower()
            if env_val in ["true", "1", "yes", "on"]:
                self.enabled = True
            elif env_val in ["false", "0", "no", "off"]:
                self.enabled = False
            else:
                # Default: inherit from TELEGRAM_ALERT_ENABLED
                alert_val = os.getenv("TELEGRAM_ALERT_ENABLED", "false").strip().lower()
                self.enabled = alert_val in ["true", "1", "yes", "on"]

        # 2. Minute of the hour to broadcast (default: 1, i.e. xx:01:00 UTC)
        if report_minute is not None:
            self.report_minute = int(report_minute)
        else:
            try:
                self.report_minute = int(os.getenv("TELEGRAM_HOURLY_REPORT_MINUTE", "1"))
            except ValueError:
                self.report_minute = 1

        # 3. Whether to include market overview & yield projections
        if show_market is not None:
            self.show_market = show_market
        else:
            env_sm = os.getenv("TELEGRAM_HOURLY_REPORT_SHOW_MARKET", "true").strip().lower()
            self.show_market = env_sm in ["true", "1", "yes", "on"]

        # 4. Only broadcast if there was an actual payout in the hour
        if only_on_payout is not None:
            self.only_on_payout = only_on_payout
        else:
            env_op = os.getenv("TELEGRAM_HOURLY_REPORT_ONLY_ON_PAYOUT", "false").strip().lower()
            self.only_on_payout = env_op in ["true", "1", "yes", "on"]

        # 5. Monitored symbols
        if symbols is not None:
            self.symbols = [s.strip().upper() for s in symbols if s.strip()]
        else:
            raw_syms = os.getenv("TELEGRAM_HOURLY_REPORT_SYMBOLS", "") or os.getenv("TELEGRAM_ALERT_SYMBOLS", "HYPE")
            self.symbols = [s.strip().upper() for s in raw_syms.split(",") if s.strip()]
        if not self.symbols:
            self.symbols = ["HYPE"]

        # Tracking state
        self._last_reported_hour: Optional[str] = None
        self._last_report_time: Optional[float] = None
        self._last_report_data: Optional[Dict[str, Any]] = None
        self._report_history: List[Dict[str, Any]] = []
        self._total_reports_sent: int = 0
        self._is_running: bool = False
        self._stop_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None

    def is_enabled(self) -> bool:
        """Returns True if hourly reporting is enabled and Telegram credentials exist."""
        return self.enabled and self.notifier.is_configured()

    def start(self):
        """Starts background daemon thread for scheduled hourly funding reports."""
        if self._is_running:
            return
        if not self.is_enabled():
            logger.info("HourlyFundingReporter is disabled or Telegram credentials missing.")
            return

        self._stop_event.clear()
        self._is_running = True
        self._worker_thread = threading.Thread(
            target=self._run_loop,
            name="HourlyFundingReporterThread",
            daemon=True
        )
        self._worker_thread.start()
        logger.info(
            f"HourlyFundingReporter started (report_minute={self.report_minute}, "
            f"show_market={self.show_market}, only_on_payout={self.only_on_payout})"
        )

    def stop(self):
        """Stops the background reporter thread."""
        if not self._is_running:
            return
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.0)
        self._is_running = False
        logger.info("HourlyFundingReporter stopped.")

    def _run_loop(self):
        """Periodic background loop checking whether to trigger the hourly report."""
        while not self._stop_event.is_set():
            try:
                self.check_and_trigger()
            except Exception as e:
                logger.error(f"Error in HourlyFundingReporter loop: {e}", exc_info=True)
            self._stop_event.wait(timeout=20.0)

    def get_status(self) -> Dict[str, Any]:
        """Returns runtime diagnostic status of the hourly funding reporter."""
        return {
            "enabled": self.enabled,
            "is_configured": self.notifier.is_configured(),
            "is_running": self._is_running,
            "account_address": self.account_address,
            "report_minute": self.report_minute,
            "show_market": self.show_market,
            "only_on_payout": self.only_on_payout,
            "symbols": self.symbols,
            "last_reported_hour": self._last_reported_hour,
            "last_report_time": self._last_report_time,
            "total_reports_sent": self._total_reports_sent,
            "history_count": len(self._report_history),
        }

    def fetch_recent_settlements(self, max_lookback_seconds: int = 7200) -> List[Dict[str, Any]]:
        """
        Fetches user funding settlement records from Hyperliquid API and filters
        for settlements within the latest hour window (or last settlement event).
        """
        if not self.account_address:
            return []

        try:
            # Query last 2 hours of funding events
            start_time_ms = int((time.time() - max_lookback_seconds) * 1000)
            fundings = self.hl_client.get_user_funding(self.account_address, start_time=start_time_ms)
            if not fundings:
                # If no records in lookback, fetch general recent list without start_time filter
                fundings = self.hl_client.get_user_funding(self.account_address)
        except Exception as e:
            logger.error(f"Failed to fetch user funding records: {e}")
            return []

        if not fundings or not isinstance(fundings, list):
            return []

        # Find the latest settlement timestamp
        latest_time = 0
        for f in fundings:
            t = f.get("time", 0)
            if t > latest_time:
                latest_time = t

        if latest_time == 0:
            return []

        # Group all settlement records that occurred at the same latest settlement block/hour
        # Typically occurs within +/- 60 seconds of latest_time
        settlements = []
        for f in fundings:
            t = f.get("time", 0)
            if abs(t - latest_time) <= 60000:
                delta = f.get("delta", {})
                coin = delta.get("coin", "")
                usdc_val = safe_float(delta.get("usdc", 0.0))
                szi_val = safe_float(delta.get("szi", 0.0))
                funding_rate = safe_float(delta.get("fundingRate", 0.0))

                hourly_rate_pct = funding_rate * 100.0
                apr_pct = funding_rate * 24.0 * 365.0 * 100.0

                settlements.append({
                    "coin": coin,
                    "time": t,
                    "datetime_utc": datetime.fromtimestamp(t / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "usdc": usdc_val,
                    "szi": szi_val,
                    "side": "Short" if szi_val < 0 else "Long",
                    "abs_size": abs(szi_val),
                    "funding_rate": funding_rate,
                    "hourly_rate_pct": hourly_rate_pct,
                    "apr_pct": apr_pct,
                    "hash": f.get("hash", "")
                })

        return settlements

    def fetch_portfolio_health(self) -> Dict[str, Any]:
        """
        Retrieves Master Account's Scheme D portfolio health, active positions,
        cumulative funding earnings, margin ratio, and liquidation distance.
        """
        if not self.account_address:
            return {}

        try:
            return self.executor.evaluate_scheme_d_health()
        except Exception as e:
            logger.warning(f"Failed to evaluate Scheme D health: {e}")
            return {}

    def fetch_market_overview(self, target_symbols: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        Fetches live market data (spot price, perp price, basis spread, funding rate & APR)
        for target symbols on Hyperliquid.
        """
        coins = target_symbols or self.symbols
        # HYPER <-> HYPE alias equivalence
        target_coins = set()
        for c in coins:
            u = c.upper()
            target_coins.add(u)
            if u == "HYPER":
                target_coins.add("HYPE")
            elif u == "HYPE":
                target_coins.add("HYPER")

        market_results = []
        try:
            p_univ, p_ctxs = self.hl_client.get_perp_market_data()
            s_toks, s_univ, s_ctxs = self.hl_client.get_spot_market_data()
            hl_results = self.calculator.match_and_calculate(
                p_univ, p_ctxs, s_toks, s_univ, s_ctxs
            )

            for m in hl_results:
                coin_name = m.get("coin", "").upper()
                if coin_name in target_coins:
                    market_results.append(m)
        except Exception as e:
            logger.warning(f"Failed to fetch market overview: {e}")

        return market_results

    def build_report(self, force: bool = False) -> Dict[str, Any]:
        """
        Builds structured data and formatted Markdown report for the current hourly broadcast.
        """
        now = time.time()
        now_dt = datetime.now(timezone.utc)
        dt_utc_str = now_dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        hour_tag_str = now_dt.strftime("%Y-%m-%d %H:00 UTC")

        # 1. Settlements in recent hour
        settlements = self.fetch_recent_settlements()

        # 2. Portfolio health & active positions
        health = self.fetch_portfolio_health()
        positions = health.get("positions", [])
        spot_balances = health.get("spot_balances", [])

        # Active perp positions with non-zero size
        active_positions = [p for p in positions if abs(safe_float(p.get("size", 0.0))) > 1e-6]

        has_active_position = bool(active_positions or settlements)

        # 3. Market overview
        markets = self.fetch_market_overview() if self.show_market else []

        # Totals
        total_payout_usdc = sum(s.get("usdc", 0.0) for s in settlements)
        total_cum_funding_usdc = health.get("cum_funding_total", 0.0)
        total_equity_usd = health.get("account_value", 0.0)
        cash_usdc = health.get("spot_cash_usdc", 0.0)
        effective_margin = health.get("effective_margin", 0.0)
        margin_used = health.get("total_margin_used", 0.0)
        margin_util = health.get("margin_utilization_pct", 0.0)
        min_liq_dist = health.get("min_liq_distance_pct")
        tier_name = health.get("tier_name", "Level 0 (正常运行)")

        report_data = {
            "timestamp": now,
            "datetime_utc": dt_utc_str,
            "hour_tag": hour_tag_str,
            "has_active_position": has_active_position,
            "settlements": settlements,
            "portfolio": health,
            "active_positions": active_positions,
            "spot_balances": spot_balances,
            "markets": markets,
            "total_payout_usdc": total_payout_usdc,
            "total_cum_funding_usdc": total_cum_funding_usdc,
            "total_equity_usd": total_equity_usd,
            "cash_usdc": cash_usdc,
            "effective_margin": effective_margin,
            "margin_used": margin_used,
            "margin_utilization_pct": margin_util,
            "min_liq_distance_pct": min_liq_dist,
            "tier_name": tier_name,
        }

        # Build Markdown text
        markdown_text = self.format_markdown_report(report_data)
        report_data["markdown"] = markdown_text

        return report_data

    def format_markdown_report(self, data: Dict[str, Any]) -> str:
        """
        Renders rich, institutional Markdown text for Telegram notification.
        """
        has_pos = data.get("has_active_position", False)
        hour_tag = data.get("hour_tag", "")
        settlements = data.get("settlements", [])
        health = data.get("portfolio", {})
        active_pos = data.get("active_positions", [])
        spot_bals = data.get("spot_balances", [])
        markets = data.get("markets", [])

        total_payout = data.get("total_payout_usdc", 0.0)
        total_cum = data.get("total_cum_funding_usdc", 0.0)
        total_equity = data.get("total_equity_usd", 0.0)
        cash_usdc = data.get("cash_usdc", 0.0)

        # Time formatting: UTC and Beijing Time (UTC+8)
        now_dt = datetime.now(timezone.utc)
        bj_hour = (now_dt.hour + 8) % 24
        bj_time_str = f"{bj_hour:02d}:00"

        if has_pos:
            title = "💰 *【资金费率每小时收益播报】*"
            sub_title = f"⏰ *结算周期*: `{hour_tag}` (北京时间 {bj_time_str})"
        else:
            title = "💰 *【资金费率每小时市场播报】*"
            sub_title = f"⏰ *播报时间*: `{hour_tag}` (北京时间 {bj_time_str})"

        lines = [title, sub_title, ""]

        # Section 1: Hourly Payout Details
        if settlements:
            lines.append("💎 *【本期实收资金费 (Hourly Payout)】*")
            for s in settlements:
                coin = s.get("coin", "")
                payout = s.get("usdc", 0.0)
                payout_sign = "+" if payout >= 0 else ""
                rate_pct = s.get("hourly_rate_pct", 0.0)
                apr_pct = s.get("apr_pct", 0.0)
                szi = s.get("abs_size", 0.0)
                side_str = "空头对冲" if s.get("szi", 0) < 0 else "多头"

                lines.append(f"• 结算标的: *{coin}*")
                lines.append(f"• 结算费率: `{rate_pct:+.5f}% / 1h` (等效年化: *{apr_pct:+.2f}% APR*)")
                lines.append(f"• 持仓头寸: `{szi:.2f} {coin}` ({side_str})")
                lines.append(f"• 本期实收: *`{payout_sign}{payout:.4f} USDC`* 💵")

            if len(settlements) > 1:
                total_sign = "+" if total_payout >= 0 else ""
                lines.append(f"• 本期实收总计: *`{total_sign}{total_payout:.4f} USDC`*")

            cum_sign = "+" if total_cum >= 0 else ""
            lines.append(f"• 累计已收收益: *`{cum_sign}{total_cum:.4f} USDC`*")
            lines.append("")

            # Section 2: Yield Projections
            if settlements:
                first_s = settlements[0]
                first_payout = first_s.get("usdc", 0.0)
                est_24h = first_payout * 24.0
                est_30d = first_payout * 24.0 * 30.0
                first_apr = first_s.get("apr_pct", 0.0)
                scheme_d_net_apr = first_apr * 0.90  # 90% capital efficiency in Scheme D

                lines.append("📈 *【收益率与未来测算】*")
                lines.append(f"• 24 小时预估收益: `~${est_24h:+.4f} USDC`")
                lines.append(f"• 30 天预估收益: `~${est_30d:+.2f} USDC`")
                lines.append(f"• 方案 D 综合净年化: *`{scheme_d_net_apr:+.2f}% APR`* (90% 资金效率)")
                lines.append("")

        elif has_pos and active_pos:
            # Active position exists but settlement was slightly delayed or 0
            lines.append("💎 *【实盘持仓资金费状态】*")
            for p in active_pos:
                coin = p.get("coin", "")
                sz = p.get("size", 0.0)
                side = p.get("side", "Short")
                entry_px = p.get("entry_price", 0.0)
                cum_f = p.get("cum_funding", 0.0)
                lines.append(f"• 持仓标的: *{coin}* (`{sz:.2f} {coin}` {side} | 成本 `${entry_px:.4f}`)")
                lines.append(f"• 历史累计资金费: `+{cum_f:.4f} USDC`")
            lines.append("")
        elif not has_pos:
            lines.append("ℹ️ *【账户持仓状态】*")
            lines.append("• 当前账户暂无活跃资金费率套利对冲持仓")
            if total_equity > 0 or health.get("total_account_value", 0) > 0:
                equity_val = total_equity if total_equity > 0 else health.get("total_account_value", 0.0)
                lines.append(f"• 账户闲置可用资金: *`${equity_val:,.2f} USDC`*")
            lines.append("")

        # Section 3: Scheme D Health & Risk Guards
        if has_pos and health and (total_equity > 0 or health.get("total_account_value", 0) > 0):
            equity_val = total_equity if total_equity > 0 else health.get("total_account_value", 0.0)
            lines.append("🛡️ *【账户健康与 Scheme D 风控看板】*")
            lines.append(f"• 账户总净值: *`${equity_val:,.2f} USDC`* (闲置现金: `${cash_usdc:,.2f}`)")

            # Spot Collateral
            spot_tokens = [b for b in spot_bals if b.get("coin") != "USDC" and safe_float(b.get("total_qty") or b.get("total", 0)) > 1e-4]
            if spot_tokens:
                for st in spot_tokens:
                    s_coin = st.get("coin", "")
                    s_qty = safe_float(st.get("total_qty") or st.get("total", 0.0))
                    s_val = safe_float(st.get("valuation_usd", 0.0))
                    s_ltv = safe_float(st.get("ltv", 0.65)) * 100.0
                    lines.append(f"• 现货质押: `{s_qty:.2f} {s_coin}` (市值 `${s_val:,.2f}` | {s_ltv:.0f}% LTV 折算抵押)")

            # Margin & Liquidation
            margin_used = safe_float(health.get("total_margin_used", 0.0))
            margin_util = safe_float(health.get("margin_utilization_pct", 0.0))
            min_liq_dist = health.get("min_liq_distance_pct")
            tier_badge = health.get("tier_name") or health.get("tier_badge", "🟢 Level 0 (正常运行)")

            lines.append(f"• 保证金使用率: `{margin_util:.2f}%` (已占用 `${margin_used:,.2f}` | 安全线 < 60%)")
            if min_liq_dist is not None:
                lines.append(f"• 强平安全距离: *`+{min_liq_dist:.1f}%`* (极高抗穿仓裕度)")
            lines.append(f"• 阶梯风控状态: {tier_badge}")
            lines.append("")

        # Section 4: Market Overview & Top Yield Opportunities
        if markets:
            lines.append("📊 *【标的行情与期现基差】*" if has_pos else "📊 *【监控标的当前费率与套利机会】*")
            for m in markets:
                coin = m.get("coin", "")
                spot_px = m.get("spot_price", 0.0)
                perp_px = m.get("perp_price", 0.0)
                spread_pct = m.get("spread_pct", 0.0)
                hourly_f = m.get("hourly_funding_pct", 0.0)
                apr_pct = m.get("apr_pct", 0.0)
                spread_icon = "🟢" if spread_pct >= 0 else "🔴"

                # Estimated yield per $10k
                est_10k_hr = 10000.0 * 0.90 * (hourly_f / 100.0)
                est_10k_day = est_10k_hr * 24.0

                lines.append(
                    f"• *{coin}*: 费率 `{hourly_f:+.5f}%/h` (年化 *{apr_pct:+.2f}% APR*) | 基差 `{spread_pct:+.3f}%` {spread_icon}"
                )
                if not has_pos:
                    lines.append(f"  ↳ 测算每 $10,000 本金收益: `~${est_10k_hr:+.3f}/h` (日化 `~${est_10k_day:+.2f}`)")
                else:
                    lines.append(f"  ↳ 现货: `${spot_px:,.4f}` │ 合约: `${perp_px:,.4f}`")
            lines.append("")

        # If zero position, give actionable guidance
        if not has_pos:
            lines.append("💡 *操作建议*: 当前无活跃持仓，可通过 `scripts/hl_ops.py arbitrage` 构建 1:1 Delta中性对冲。")

        return "\n".join(lines).strip()

    def send_report(self, force: bool = False) -> Tuple[bool, str]:
        """
        Executes hourly report generation and dispatches Telegram message.
        Guarantees idempotency (max once per hour unless force=True).
        """
        if not self.is_enabled():
            return False, "Hourly funding reporter is disabled or Telegram credentials missing"

        now_dt = datetime.now(timezone.utc)
        current_hour_str = now_dt.strftime("%Y-%m-%d-%H")

        if not force and self._last_reported_hour == current_hour_str:
            return False, f"Report for hour {current_hour_str} already sent. Skipping."

        try:
            report_data = self.build_report(force=force)
            if self.only_on_payout and not report_data.get("settlements"):
                logger.info("Hourly report skipped: only_on_payout=True and no settlements found.")
                return False, "Skipped: no funding settlement payout in this hour."

            msg = report_data.get("markdown", "")
            success, err_or_msg = self.notifier.send_message(msg)

            if success:
                self._last_reported_hour = current_hour_str
                self._last_report_time = time.time()
                self._last_report_data = report_data
                self._total_reports_sent += 1

                self._report_history.append({
                    "timestamp": self._last_report_time,
                    "hour": current_hour_str,
                    "datetime_utc": report_data.get("datetime_utc"),
                    "payout_usdc": report_data.get("total_payout_usdc", 0.0),
                    "cum_funding_usdc": report_data.get("total_cum_funding_usdc", 0.0),
                    "response": err_or_msg
                })
                if len(self._report_history) > 50:
                    self._report_history.pop(0)

                logger.info(f"✅ Hourly funding rate report sent successfully for {current_hour_str}")
                return True, "Hourly funding rate report sent successfully"
            else:
                logger.error(f"Failed to send hourly funding rate report: {err_or_msg}")
                return False, f"Failed to send report: {err_or_msg}"

        except Exception as e:
            logger.error(f"Error during hourly funding report generation/dispatch: {e}", exc_info=True)
            return False, f"Error generating report: {str(e)}"

    def check_and_trigger(self, now: Optional[datetime] = None) -> Optional[Tuple[bool, str]]:
        """
        Invoked periodically (e.g. every 15~30s) by background daemon thread.
        Evaluates whether the current clock time matches the designated reporting
        minute and dispatches the report if not yet sent for the current hour.
        """
        if not self.is_enabled():
            return None

        current_dt = now or datetime.now(timezone.utc)
        current_hour_str = current_dt.strftime("%Y-%m-%d-%H")

        # Check if already sent in this hour
        if self._last_reported_hour == current_hour_str:
            return None

        # Check if current minute is within trigger window [report_minute, report_minute + 2]
        # (Allows 3-minute grace window in case of poll intervals or network delay)
        minute = current_dt.minute
        if self.report_minute <= minute <= (self.report_minute + 2):
            logger.info(f"Triggering scheduled hourly funding report for {current_hour_str} at minute {minute}...")
            return self.send_report(force=False)

        return None
