#!/usr/bin/env python3
import sys
import time
import argparse
import json
import re
import src.env
from typing import Optional, Tuple
from src.version import __version__, format_version_text, get_diagnostics

try:
    from rich.console import Console
    from rich.table import Table
    from rich.live import Live
    from rich.panel import Panel
    from rich.text import Text
    HAS_RICH = True
except ImportError:
    HAS_RICH = False
    class Text:
        def __init__(self, text="", style=None):
            self.text = text
        def __str__(self):
            return self.text

    class Panel:
        def __init__(self, renderable, title=None, subtitle=None, border_style=None):
            self.renderable = str(renderable)
            self.title = title
            self.subtitle = subtitle
            self.border_style = border_style
        def __str__(self):
            out = []
            clean_title = re.sub(r'\[/?[a-zA-Z0-9_ #]+\]', '', str(self.title)) if self.title else None
            clean_body = re.sub(r'\[/?[a-zA-Z0-9_ #]+\]', '', str(self.renderable))
            clean_sub = re.sub(r'\[/?[a-zA-Z0-9_ #]+\]', '', str(self.subtitle)) if self.subtitle else None
            if clean_title:
                out.append(f"\n==================== {clean_title} ====================")
            out.append(clean_body)
            if clean_sub:
                out.append(f"-------------------- {clean_sub} --------------------")
            return "\n".join(out)

    class Table:
        def __init__(self, title=None, show_lines=False, header_style=None, title_style=None):
            self.title = title
            self.columns = []
            self.rows = []
        def add_column(self, header, justify="left", style=None, no_wrap=False):
            self.columns.append(header)
        def add_row(self, *args):
            self.rows.append([str(a) for a in args])
        def __str__(self):
            def clean(s):
                return re.sub(r'\[/?[a-zA-Z0-9_ #]+\]', '', str(s))
            out = []
            if self.title:
                out.append(f"\n>>> {clean(self.title)}")
            if self.columns:
                col_headers = [clean(c) for c in self.columns]
                out.append(" | ".join(col_headers))
                out.append("-" * max(len(" | ".join(col_headers)), 40))
            for row in self.rows:
                out.append(" | ".join([clean(r) for r in row]))
            return "\n".join(out)

    class Console:
        def print(self, *args, **kwargs):
            cleaned_args = []
            for a in args:
                if isinstance(a, (Panel, Table, Text)):
                    cleaned_args.append(str(a))
                else:
                    cleaned_args.append(re.sub(r'\[/?[a-zA-Z0-9_ #]+\]', '', str(a)))
            print(*cleaned_args)

from src.hyperliquid_client import HyperliquidClient
from src.bybit_client import BybitClient
from src.calculator import (
    FundingRateCalculator,
    DEFAULT_HL_SPOT_TAKER_FEE,
    DEFAULT_HL_PERP_TAKER_FEE,
    DEFAULT_BYBIT_SPOT_TAKER_FEE,
    DEFAULT_BYBIT_PERP_TAKER_FEE
)

def render_domain_guide_cli() -> Panel:
    """Renders comprehensive domain knowledge and arbitrage risk management guide in terminal."""
    guide_text = (
        "[bold cyan]📚 Hyperliquid & Bybit 套利业务知识与风控速查手册 (Domain Knowledge Guide)[/bold cyan]\n\n"
        "[bold yellow]一、 Hyperliquid 标的来源性质分类详解：[/bold yellow]\n"
        "  1. [bold cyan][🏛️ 官方 Canonical][/bold cyan]: 官方创世/原生资产 (如 PURR, USDC)。\n"
        "     • 特点：由官方验证节点统一定价预言机，流动性充裕，在 Portfolio Margin 账户中享有 50% 质押折算率 (Scheme D 核心标的)。\n"
        "  2. [bold cyan][🏛️ 原生 HYPE][/bold cyan]: Hyperliquid L1 核心代币。\n"
        "     • 特点：流动性极佳，现货质押+永续对冲的核心标的。\n"
        "  3. [bold yellow][🌉 Unit 映射][/bold yellow]: Unit Protocol 封装映射资产 (如 UBTC, UETH, USOL, UZEC, UENA, UFART, UPUMP, UUUSPX, UBONK)。\n"
        "     • 特点：解决 L1 现货匮乏问题，适合 1:1 全款现货持有对冲；目前在账户中作为独立现货持仓 (非跨资产质押品)。\n"
        "  4. [bold blue][🌉 跨链桥接][/bold blue]: HyBridge / Wagyu 桥接资产 (如 LINK0, AAVE0, XMR1, BNB1, CFX0)。\n"
        "     • 特点：跨链封装资产，开仓前需关注跨链深度与现货-永续基差 (Spread)。\n"
        "  5. [bold magenta][⚡ HIP-1 发币][/bold magenta]: 社区无许可发币 (如 AZTEC, @260, @107 等)。\n"
        "     • 特点：荷兰拍/联合曲线创建，开仓务必核对底层真实 Pair ID (如 @260) 谨防同名碰撞，并关注部署者手续费分成与抛压。\n"
        "  6. [bold green][⛓️ HyperEVM][/bold green]: 具备 HyperEVM 智能合约地址，支持 EVM 生态双向流转。\n"
        "  7. [bold dim][🔢 1000x 乘数][/bold dim]: 1张合约对应 1000 个现货 Token (如 kBONK, 1000PEPE)，张数须除以乘数。\n\n"
        "[bold yellow]二、 核心量化指标与套利风控准则：[/bold yellow]\n"
        "  • [bold green]1小时资金费率[/bold green]: 永续合约每小时结算比率。Simple APR = Rate × 24 × 365 × 100%。\n"
        "  • [bold green]回本时间 (Payback Hours)[/bold green]: 开仓/双边手续费被资金费率收益覆盖所需时长 (建议 <= 48h)。\n"
        "  • [bold green]基差 (Basis Spread)[/bold green]: (永续价格 - 现货价格) / 现货价格。正基差有利，严禁大幅负基差入场。\n"
        "  • [bold green]Hyperliquid 方案 D (Scheme D)[/bold green]: 90% 现货质押 + 90% 永续做空 + 10% USDC 现金缓冲 + 强平线 > +109.6%。\n"
        "  • [bold green]Maker-Taker 触发式对冲[/bold green]: 现货挂单 (Post-Only) + 永续市价 (IOC) 触发对冲，节省 50%~70% 手续费摩擦。"
    )
    return Panel(guide_text, title="[bold cyan]💡 业务名词与套利风控指南 (Cheat Sheet)[/bold cyan]", border_style="cyan")


def render_cli_table(data: list, 
                     spot_fee_pct: float, 
                     perp_fee_pct: float, 
                     limit: Optional[int] = None) -> Table:
    """Renders a Rich ASCII table of funding rates and payback times with token origins."""
    table = Table(title="🚀 Capital Spot & Perp Funding Rate Arbitrage Monitor", 
                  title_style="bold cyan", 
                  header_style="bold magenta", 
                  show_lines=True)

    table.add_column("Rank", justify="right", style="dim", no_wrap=True)
    table.add_column("Exchange", justify="center", style="bold cyan")
    table.add_column("Asset", justify="left", style="bold yellow")
    table.add_column("Spot Pair", justify="left", style="green")
    table.add_column("Origin / 性质", justify="center")
    table.add_column("Pair ID", justify="center", style="dim")
    table.add_column("Hourly Rate", justify="right")
    table.add_column("APR (Simple)", justify="right", style="bold green")
    table.add_column("APY (Compound)", justify="right", style="green")
    table.add_column("Basis Spread", justify="right")
    table.add_column("Entry Payback", justify="right", style="bold blue")
    table.add_column("Roundtrip Payback", justify="right", style="blue")
    table.add_column("24h Volume (Perp)", justify="right", style="dim")

    rows = data[:limit] if limit else data

    for i, item in enumerate(rows, 1):
        hr_rate = item["hourly_funding_pct"]
        hr_str = f"{hr_rate:+.4f}%"
        if hr_rate > 0.01:
            hr_cell = f"[bold green]{hr_str}[/bold green]"
        elif hr_rate > 0:
            hr_cell = f"[green]{hr_str}[/green]"
        elif hr_rate < 0:
            hr_cell = f"[red]{hr_str}[/red]"
        else:
            hr_cell = f"[dim]{hr_str}[/dim]"

        apr = item["apr_pct"]
        apr_str = f"{apr:+.2f}%"

        apy = item["apy_pct"]
        apy_str = f"{apy:+.2f}%" if apy < 10000 else ">9999%"

        spread = item["spread_pct"]
        spread_str = f"{spread:+.2f}%"

        entry_pb = item["entry_payback_str"]
        rt_pb = item["roundtrip_payback_str"]

        vol = item["perp_24h_volume"]
        vol_str = f"${vol:,.0f}"

        exch_badge = f"[bold cyan]HL[/bold cyan]" if item.get("exchange") == "Hyperliquid" else f"[bold yellow]Bybit[/bold yellow]"

        origin_badge = item.get("origin_badge", "")
        if "官方" in origin_badge:
            origin_cell = f"[bold cyan]{origin_badge}[/bold cyan]"
        elif "Unit" in origin_badge:
            origin_cell = f"[bold yellow]{origin_badge}[/bold yellow]"
        elif "HIP-1" in origin_badge:
            origin_cell = f"[bold magenta]{origin_badge}[/bold magenta]"
        elif "桥接" in origin_badge:
            origin_cell = f"[bold blue]{origin_badge}[/bold blue]"
        elif "乘数" in origin_badge:
            origin_cell = f"[dim]{origin_badge}[/dim]"
        else:
            origin_cell = origin_badge

        raw_id = item.get("raw_pair_id") or item.get("raw_spot_pair") or item.get("spot_pair") or ""
        if item.get("has_evm"):
            raw_id = f"{raw_id} [cyan][EVM][/cyan]"

        table.add_row(
            str(i),
            exch_badge,
            item["coin"],
            f"{item['spot_symbol']} ({item['spot_pair']})",
            origin_cell,
            raw_id,
            hr_cell,
            apr_str,
            apy_str,
            spread_str,
            entry_pb,
            rt_pb,
            vol_str
        )

    return table

def fetch_data_for_exchange(exchange: str,
                           hl_client: Optional[HyperliquidClient],
                           bybit_client: Optional[BybitClient],
                           calculator: FundingRateCalculator) -> list:
    results = []
    
    if exchange in ["hyperliquid", "all", "hl"] and hl_client:
        perp_univ, perp_ctxs = hl_client.get_perp_market_data()
        spot_toks, spot_univ, spot_ctxs = hl_client.get_spot_market_data()
        hl_results = calculator.match_and_calculate(
            perp_univ, perp_ctxs, spot_toks, spot_univ, spot_ctxs
        )
        results.extend(hl_results)

    if exchange in ["bybit", "all"] and bybit_client:
        linear_tickers, linear_insts = bybit_client.get_linear_market_data()
        spot_tickers, spot_insts = bybit_client.get_spot_market_data()
        bybit_results = calculator.match_and_calculate_bybit(
            linear_tickers, spot_tickers, linear_insts, spot_insts
        )
        results.extend(bybit_results)

    results.sort(key=lambda x: x["hourly_funding"], reverse=True)
    return results

def render_history_cli_table(history_data: dict, limit: Optional[int] = None) -> Tuple[Panel, Table]:
    stats = history_data.get("stats", {})
    symbol = history_data.get("symbol", "")
    interval = history_data.get("funding_interval_hr", 8.0)

    summary_text = (
        f"[bold yellow]Symbol:[/bold yellow] {symbol} | "
        f"[bold yellow]Interval:[/bold yellow] {interval:.0f}h | "
        f"[bold yellow]Total Periods:[/bold yellow] {history_data.get('total_periods', 0)}\n"
        f"[bold green]Avg Hourly Rate:[/bold green] {stats.get('avg_hourly_funding_pct', 0):+.4f}% | "
        f"[bold green]Avg Simple APR:[/bold green] [bold]{stats.get('apr_simple_pct', 0):+.2f}%[/bold]\n"
        f"[bold cyan]Cumulative Funding:[/bold cyan] {stats.get('cumulative_funding_pct', 0):+.4f}% | "
        f"[bold blue]Positive Ratio:[/bold blue] {stats.get('pos_pct', 0):.1f}% ({stats.get('pos_count', 0)} periods)\n"
        f"[bold magenta]Max Rate:[/bold magenta] {stats.get('max_rate_pct', 0):+.4f}% ({stats.get('max_rate_time', '')}) | "
        f"[bold red]Min Rate:[/bold red] {stats.get('min_rate_pct', 0):+.4f}% ({stats.get('min_rate_time', '')})\n"
        f"[dim]Std Dev (Volatility): {stats.get('std_dev_period_pct', 0):.4f}% (Period) / {stats.get('std_dev_hourly_pct', 0):.4f}% (Hourly)[/dim]"
    )
    panel = Panel(summary_text, title=f"[bold cyan]📊 Bybit Perpetual Funding Rate History Stats ({symbol})[/bold cyan]")

    table = Table(show_lines=True, header_style="bold magenta")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Time (UTC)", justify="center", style="bold yellow")
    table.add_column("Period Rate", justify="right")
    table.add_column("Hourly Eq. Rate", justify="right")
    table.add_column("Simple APR", justify="right", style="bold green")

    records = history_data.get("records", [])
    if limit:
        records = records[-limit:]

    for i, rec in enumerate(records, 1):
        p_rate = rec["period_funding_pct"]
        p_str = f"{p_rate:+.4f}%"
        p_cell = f"[green]{p_str}[/green]" if p_rate > 0 else (f"[red]{p_str}[/red]" if p_rate < 0 else f"[dim]{p_str}[/dim]")
        
        h_rate = rec["hourly_funding_pct"]
        h_str = f"{h_rate:+.4f}%"
        h_cell = f"[green]{h_str}[/green]" if h_rate > 0 else (f"[red]{h_str}[/red]" if h_rate < 0 else f"[dim]{h_str}[/dim]")

        table.add_row(
            str(i),
            rec["datetime_utc"],
            p_cell,
            h_cell,
            f"{rec['apr_pct']:+.2f}%"
        )

    return panel, table

def run_cli_loop(exchange: str,
                 hl_client: HyperliquidClient, 
                 bybit_client: BybitClient,
                 calculator: FundingRateCalculator, 
                 interval: int, 
                 limit: Optional[int]):
    console = Console()
    console.print(f"[bold green]Starting Capital Funding Rate Arbitrage Monitor CLI ({exchange.upper()})... Refresh interval: {interval}s[/bold green]")
    
    with Live(console=console, refresh_per_second=1) as live:
        while True:
            try:
                results = fetch_data_for_exchange(exchange, hl_client, bybit_client, calculator)
                
                table = render_cli_table(
                    results, 
                    calculator.spot_taker_fee * 100.0, 
                    calculator.perp_taker_fee * 100.0, 
                    limit
                )
                
                timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                footer_text = Text(f"Last updated: {timestamp} UTC | Monitored Pairs: {len(results)} | Fees: Spot Taker {calculator.spot_taker_fee*100:.3f}%, Perp Taker {calculator.perp_taker_fee*100:.3f}%", style="dim italic")
                
                panel = Panel(table, subtitle=footer_text)
                live.update(panel)
            except Exception as e:
                console.print(f"[bold red]Error updating data: {e}[/bold red]")
            
            time.sleep(interval)

def render_depth_cli_table(capacity_data: dict) -> Tuple[Panel, Table]:
    data = capacity_data.get("data", {})
    symbol = capacity_data.get("symbol", "")
    exchange = capacity_data.get("exchange", "")
    caps = data.get("max_capacities", {})
    fees = data.get("fees", {})
    
    summary_text = (
        f"[bold yellow]Symbol:[/bold yellow] {symbol} ({exchange.upper()}) | "
        f"[bold yellow]Hourly Funding:[/bold yellow] {data.get('hourly_funding_pct', 0):+.4f}%/h | "
        f"[bold yellow]Base Fees:[/bold yellow] {fees.get('base_fee_pct', 0):.3f}%\n"
        f"[bold green]Max Cap @ 0.10% Slip:[/bold green] [bold]${caps.get('max_cap_01_pct_usd', 0):,.0f}[/bold] | "
        f"[bold green]Max Cap @ 0.20% Slip:[/bold green] [bold]${caps.get('max_cap_02_pct_usd', 0):,.0f}[/bold]\n"
        f"[bold cyan]Max Cap @ 0.50% Slip:[/bold cyan] ${caps.get('max_cap_05_pct_usd', 0):,.0f} | "
        f"[bold blue]Max Cap @ 24h Payback:[/bold blue] ${caps.get('max_cap_24h_payback_usd', 0):,.0f}"
    )
    panel = Panel(summary_text, title=f"[bold cyan]⚖️ Delta-Neutral Capital Capacity & Depth Evaluation ({symbol})[/bold cyan]")

    table = Table(show_lines=True, header_style="bold magenta")
    table.add_column("Target Position ($)", justify="right", style="bold yellow")
    table.add_column("Spot VWAP / Slip", justify="right")
    table.add_column("Perp VWAP / Slip", justify="right")
    table.add_column("Total Slip (%)", justify="right", style="bold cyan")
    table.add_column("Total Cost (%)", justify="right")
    table.add_column("Payback Time", justify="right", style="bold blue")
    table.add_column("Evaluation Status", justify="left")

    for sim in data.get("simulations", []):
        if sim.get("exceeds_depth"):
            table.add_row(
                f"${sim['target_usd']:,}",
                "-",
                "-",
                "-",
                "-",
                "[dim]超出深度[/dim]",
                "[red]超出盘口深度[/red]"
            )
        else:
            s_slip = sim['spot_slippage_pct']
            p_slip = sim['perp_slippage_pct']
            tot_slip = sim['combined_slippage_pct']

            table.add_row(
                f"${sim['target_usd']:,}",
                f"${sim['spot_vwap']:,.4f} (+{s_slip:.4f}%)" if s_slip is not None else "-",
                f"${sim['perp_vwap']:,.4f} (-{p_slip:.4f}%)" if p_slip is not None else "-",
                f"{tot_slip:+.4f}%" if tot_slip is not None else "-",
                f"{sim['total_cost_pct']:.4f}%" if sim['total_cost_pct'] is not None else "-",
                sim['payback_str'],
                f"[bold green]{sim['status']}[/bold green]" if "推荐" in sim['status'] else f"[yellow]{sim['status']}[/yellow]"
            )

    return panel, table

def render_build_arbitrage_cli_report(plan_payload: dict) -> Tuple[Panel, Table]:
    plan = plan_payload.get("try_run_plan", {})
    risk = plan.get("risk_guard", {})
    symbol = plan.get("symbol", "")
    spot_symbol = plan.get("spot_symbol", "")
    mode = plan_payload.get("mode", "DRY-RUN")

    decision = risk.get("decision", "BLOCKED")
    if decision == "PASSED":
        decision_tag = "[bold green]✅ PASSED (安全可行)[/bold green]"
    elif decision == "FORCED_OVERRIDE":
        decision_tag = "[bold yellow]⚠️ FORCED OVERRIDE (强制覆写拦截)[/bold yellow]"
    else:
        decision_tag = "[bold red]🛑 BLOCKED (已被风控系统拦截)[/bold red]"

    summary_text = (
        f"[bold yellow]Arbitrage Target:[/bold yellow] {symbol} (Spot: {spot_symbol}) | [bold yellow]Mode:[/bold yellow] {mode}\n"
        f"[bold yellow]Risk Guard Decision:[/bold yellow] {decision_tag}\n"
        f"[bold cyan]Target Capital:[/bold cyan] ${plan.get('target_usd', 0):,.2f} USD | "
        f"[bold cyan]Hourly Funding:[/bold cyan] {plan.get('hourly_funding_pct', 0):+.4f}%/h\n"
        f"[bold cyan]Combined Slippage:[/bold cyan] {plan.get('combined_slippage_pct', 0) or 0:+.4f}% | "
        f"[bold cyan]Total Cost:[/bold cyan] {plan.get('total_cost_pct', 0) or 0:.4f}% | "
        f"[bold blue]Payback Time:[/bold blue] {plan.get('payback_str', '--')}"
    )

    if risk.get("has_risks"):
        warnings_str = "\n".join([f"  • {w}" for w in risk.get("warnings", [])])
        summary_text += f"\n\n[bold red]⚠️ 触发的关键风险拦截卡口:[/bold red]\n[red]{warnings_str}[/red]"
        if decision == "BLOCKED":
            summary_text += "\n\n[bold yellow]💡 提示: 避开以上风险或使用 --force 参数强制覆写风控建仓。[/bold yellow]"

    panel = Panel(summary_text, title=f"[bold magenta]🚀 Bybit Delta-Neutral Arbitrage Execution Plan ({symbol})[/bold magenta]")

    table = Table(show_lines=True, header_style="bold cyan")
    table.add_column("Leg", style="bold yellow")
    table.add_column("Category")
    table.add_column("Symbol")
    table.add_column("Side", style="bold magenta")
    table.add_column("Order Type")
    table.add_column("Quantity", justify="right", style="bold white")
    table.add_column("Expected VWAP", justify="right")
    table.add_column("Slippage (%)", justify="right", style="bold cyan")

    for order in plan.get("orders_plan", []):
        table.add_row(
            order["leg"],
            order["category"].upper(),
            order["symbol"],
            order["side"],
            order["order_type"],
            order["quantity_str"],
            f"${order['expected_vwap']:,.4f}" if order["expected_vwap"] else "-",
            f"+{order['slippage_pct']:.4f}%" if order["slippage_pct"] is not None else "-"
        )

    return panel, table

def main():
    parser = argparse.ArgumentParser(description="Capital Spot & Perp Funding Rate Arbitrage Monitor (Hyperliquid & Bybit)")
    parser.add_argument("--exchange", type=str, default="all", choices=["hyperliquid", "bybit", "all"], help="Exchange to monitor (default: all)")
    parser.add_argument("--history", type=str, default=None, help="Fetch funding rate history for specific Bybit symbol (e.g. BTCUSDT)")
    parser.add_argument("--depth", type=str, default=None, help="Evaluate orderbook depth & capital capacity for specific symbol (e.g. BTCUSDT)")
    parser.add_argument("--build-arbitrage", type=str, default=None, help="Build Delta-neutral funding arbitrage position for Bybit symbol (e.g. BTCUSDT)")
    parser.add_argument("--amount-usd", type=float, default=None, help="Target position size in USD capital")
    parser.add_argument("--amount-qty", type=float, default=None, help="Target position size in token quantity")
    parser.add_argument("--dry-run", "--try-run", action="store_true", default=True, help="Dry-run simulation mode (default: True)")
    parser.add_argument("--execute", action="store_true", help="Enable live order execution on exchange")
    parser.add_argument("--force", action="store_true", help="Force execution even if risk guard flags warnings")
    parser.add_argument("--max-slippage", type=float, default=0.50, help="Max allowed combined slippage %% threshold (default: 0.50)")
    parser.add_argument("--max-payback", type=float, default=72.0, help="Max allowed payback hours threshold (default: 72.0)")
    parser.add_argument("--target-usd", type=float, default=None, help="Custom target USD capital amount for depth evaluation")
    parser.add_argument("--days", type=int, default=30, help="Days of history to analyze when --history is passed (default: 30)")
    parser.add_argument("--live", "--cli", dest="live", action="store_true", help="Run in continuous live refresh mode in terminal")
    parser.add_argument("--web", action="store_true", help="Run Web UI dashboard server")
    parser.add_argument("--port", type=int, default=8080, help="Port to run Web UI server (default: 8080)")
    parser.add_argument("--interval", type=int, default=5, help="Refresh interval in seconds (default: 5)")
    parser.add_argument("--spot-fee", type=float, default=None, help="Spot Taker Fee percentage (default: 0.07 for HL, 0.10 for Bybit)")
    parser.add_argument("--perp-fee", type=float, default=None, help="Perp Taker Fee percentage (default: 0.035 for HL, 0.055 for Bybit)")
    parser.add_argument("--max-spread", type=float, default=10.0, help="Max allowed price spread percentage to filter fake symbol collisions (default: 10.0)")
    parser.add_argument("--no-alias", action="store_true", help="Disable token alias matching (e.g. UBTC->BTC)")
    parser.add_argument("--limit", type=int, default=None, help="Limit CLI table output rows")
    parser.add_argument("--json", action="store_true", help="Fetch once and output JSON to stdout")
    parser.add_argument("--guide", action="store_true", help="Show comprehensive domain knowledge and arbitrage risk management guide")
    parser.add_argument("--version", "-v", action="store_true", help="Show application version and diagnostic info")
    parser.add_argument("--hl-check", action="store_true", help="Run Hyperliquid API Wallet verification & canary diagnostic")
    parser.add_argument("--hl-status", action="store_true", help="Show Hyperliquid Scheme D portfolio status & risk tier")
    parser.add_argument("--hl-account", type=str, default=None, help="Hyperliquid Master Account address")
    parser.add_argument("--hl-agent-key", type=str, default=None, help="Hyperliquid Agent Wallet private key")
    parser.add_argument("--gopass", "-g", type=str, default=None, help="Gopass secret path (e.g. trading/hyperliquid/mainnet)")

    args = parser.parse_args()

    if args.guide:
        console = Console()
        console.print(render_domain_guide_cli())
        return

    if args.version:
        if args.json:
            print(json.dumps(get_diagnostics(), indent=2))
        else:
            print(format_version_text())
        return

    hl_client = HyperliquidClient()
    bybit_client = BybitClient()

    if args.hl_check or args.hl_status:
        from src.hyperliquid_executor import HyperliquidExecutor
        from scripts.hl_ops import render_check_report, render_status_report
        console = Console()
        hl_exec = HyperliquidExecutor(
            account_address=args.hl_account,
            agent_private_key=args.hl_agent_key,
            gopass_secret=args.gopass,
            hl_client=hl_client
        )
        if args.hl_check:
            auth_res = hl_exec.check_agent_authorization()
            canary_res = hl_exec.run_canary_test()
            if args.json:
                print(json.dumps({"auth": auth_res, "canary": canary_res}, indent=2))
            else:
                p, t = render_check_report(auth_res, canary_res)
                console.print(p)
                console.print(t)
            return
        if args.hl_status:
            if not hl_exec.account_address:
                console.print("[bold red]❌ 请指定 Master 地址 (--hl-account 或环境变量 HL_ACCOUNT_ADDRESS)[/bold red]")
                return
            health = hl_exec.evaluate_scheme_d_health()
            if args.json:
                print(json.dumps(health, indent=2))
            else:
                p, st, pt = render_status_report(health)
                console.print(p)
                console.print(st)
                console.print(pt)
            return

    if args.build_arbitrage:
        console = Console()
        symbol = args.build_arbitrage.strip().upper()
        dry_run = not args.execute
        force = args.force

        console.print(f"[bold cyan]Generating Delta-Neutral Arbitrage Execution Plan for {symbol} (Mode: {'DRY-RUN' if dry_run else 'LIVE'})...[/bold cyan]")

        from src.bybit_executor import BybitArbitrageExecutor
        from src.calculator import parse_base_multiplier, safe_float

        executor = BybitArbitrageExecutor(
            default_max_slippage_pct=args.max_slippage,
            default_max_payback_hours=args.max_payback
        )

        is_hl = args.exchange == "hyperliquid" or (args.exchange == "all" and not symbol.endswith("USDT") and not symbol.endswith("USDC"))

        if is_hl:
            spot_fee = args.spot_fee / 100.0 if args.spot_fee is not None else DEFAULT_HL_SPOT_TAKER_FEE
            perp_fee = args.perp_fee / 100.0 if args.perp_fee is not None else DEFAULT_HL_PERP_TAKER_FEE
            calc = FundingRateCalculator(
                spot_taker_fee=spot_fee,
                perp_taker_fee=perp_fee
            )
            executor = BybitArbitrageExecutor(
                calculator=calc,
                default_max_slippage_pct=args.max_slippage,
                default_max_payback_hours=args.max_payback
            )

            perp_univ, perp_ctxs = hl_client.get_perp_market_data()
            matched_idx = next((i for i, u in enumerate(perp_univ) if u.get("name") == symbol), None)
            if matched_idx is None:
                console.print(f"[bold red]❌ 标的 {symbol} 在 Hyperliquid 永续合约中未找到[/bold red]")
                return

            ctx = perp_ctxs[matched_idx]
            hourly_funding = safe_float(ctx.get("funding"))

            if symbol.startswith("k") and len(symbol) > 1 and symbol[1:].isupper():
                mult = 1000.0
            else:
                mult, _ = parse_base_multiplier(symbol)

            spot_coin = symbol
            perp_book = hl_client.get_l2_book(symbol)
            spot_book = hl_client.get_l2_book(spot_coin)

            perp_levels = perp_book.get("levels", [[], []])
            spot_levels = spot_book.get("levels", [[], []])

            perp_bids_raw = perp_levels[0] if len(perp_levels) > 0 else []
            perp_asks_raw = perp_levels[1] if len(perp_levels) > 1 else []
            spot_bids_raw = spot_levels[0] if len(spot_levels) > 0 and len(spot_levels[0]) > 0 else perp_bids_raw
            spot_asks_raw = spot_levels[1] if len(spot_levels) > 1 and len(spot_levels[1]) > 0 else perp_asks_raw

            perp_asks = [(safe_float(item.get("px")), safe_float(item.get("sz"))) for item in perp_asks_raw]
            perp_bids = [(safe_float(item.get("px")), safe_float(item.get("sz"))) for item in perp_bids_raw]
            spot_asks = [(safe_float(item.get("px")) * mult, safe_float(item.get("sz")) / mult) for item in spot_asks_raw]
            spot_bids = [(safe_float(item.get("px")) * mult, safe_float(item.get("sz")) / mult) for item in spot_bids_raw]

            spot_mid = (spot_asks[0][0] + spot_bids[0][0]) / 2.0 if spot_asks and spot_bids else safe_float(ctx.get("midPx"))
            perp_mid = (perp_asks[0][0] + perp_bids[0][0]) / 2.0 if perp_asks and perp_bids else spot_mid

            size_info = executor.parse_execution_size(
                symbol=symbol,
                amount_usd=args.amount_usd or (10000.0 if not args.amount_qty else None),
                amount_qty=args.amount_qty,
                spot_price=spot_mid,
                perp_price=perp_mid
            )

            try_run_plan = executor.generate_try_run_plan(
                symbol=f"{symbol}-PERP",
                spot_symbol=f"{symbol}/USDC",
                multiplier=mult,
                target_usd=size_info["target_usd"],
                spot_qty=size_info["spot_qty"],
                perp_contracts_qty=size_info["perp_contracts_qty"],
                hourly_funding=hourly_funding,
                spot_asks=spot_asks,
                spot_bids=spot_bids,
                spot_mid_px=spot_mid,
                perp_asks=perp_asks,
                perp_bids=perp_bids,
                perp_mid_px=perp_mid,
                max_slippage_pct=args.max_slippage,
                max_payback_hours=args.max_payback,
                force=force
            )
        else:
            linear_tickers, _ = bybit_client.get_linear_market_data()
            perp_ticker = next((t for t in linear_tickers if t.get("symbol") == symbol), {})
            funding_rate = safe_float(perp_ticker.get("fundingRate"))
            interval_hr = safe_float(perp_ticker.get("fundingIntervalHour"), default=8.0)
            hourly_funding = funding_rate / (interval_hr if interval_hr > 0 else 8.0)
            perp_price = safe_float(perp_ticker.get("lastPrice"))

            base_coin = symbol[:-4] if symbol.endswith("USDT") or symbol.endswith("USDC") else symbol
            mult, clean_base = parse_base_multiplier(base_coin)
            spot_sym = f"{clean_base}USDT"

            spot_tickers, _ = bybit_client.get_spot_market_data()
            spot_ticker = next((t for t in spot_tickers if t.get("symbol") == spot_sym), {})
            spot_price = safe_float(spot_ticker.get("lastPrice")) * mult

            size_info = executor.parse_execution_size(
                symbol=symbol,
                amount_usd=args.amount_usd or (10000.0 if not args.amount_qty else None),
                amount_qty=args.amount_qty,
                spot_price=spot_price,
                perp_price=perp_price
            )

            spot_book = bybit_client.get_orderbook("spot", spot_sym, limit=200)
            perp_book = bybit_client.get_orderbook("linear", symbol, limit=200)

            spot_asks = [(safe_float(px)*mult, safe_float(sz)/mult) for px, sz in spot_book.get("a", [])]
            spot_bids = [(safe_float(px)*mult, safe_float(sz)/mult) for px, sz in spot_book.get("b", [])]
            perp_asks = [(safe_float(px), safe_float(sz)) for px, sz in perp_book.get("a", [])]
            perp_bids = [(safe_float(px), safe_float(sz)) for px, sz in perp_book.get("b", [])]

            spot_mid = (spot_asks[0][0] + spot_bids[0][0]) / 2.0 if spot_asks and spot_bids else spot_price
            perp_mid = (perp_asks[0][0] + perp_bids[0][0]) / 2.0 if perp_asks and perp_bids else perp_price

            try_run_plan = executor.generate_try_run_plan(
                symbol=symbol,
                spot_symbol=spot_sym,
                multiplier=mult,
                target_usd=size_info["target_usd"],
                spot_qty=size_info["spot_qty"],
                perp_contracts_qty=size_info["perp_contracts_qty"],
                hourly_funding=hourly_funding,
                spot_asks=spot_asks,
                spot_bids=spot_bids,
                spot_mid_px=spot_mid,
                perp_asks=perp_asks,
                perp_bids=perp_bids,
                perp_mid_px=perp_mid,
                max_slippage_pct=args.max_slippage,
                max_payback_hours=args.max_payback,
                force=force
            )

        payload = {
            "mode": "DRY-RUN" if dry_run else "LIVE",
            "try_run_plan": try_run_plan
        }

        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            panel, table = render_build_arbitrage_cli_report(payload)
            console.print(panel)
            console.print(table)
        return


    hl_client = HyperliquidClient()
    bybit_client = BybitClient()

    if args.history:
        console = Console()
        console.print(f"[bold cyan]Fetching funding history for {args.history} on Bybit...[/bold cyan]")
        raw_hist = bybit_client.get_funding_rate_history(args.history, limit=200)
        hist_data = FundingRateCalculator.calculate_funding_history_stats(raw_hist, days=args.days)
        
        if args.json:
            print(json.dumps(hist_data, indent=2))
        else:
            panel, table = render_history_cli_table(hist_data, limit=args.limit)
            console.print(panel)
            console.print(table)
        return

    if args.depth:
        console = Console()
        exch = "bybit" if args.exchange in ["all", "bybit"] else "hyperliquid"
        console.print(f"[bold cyan]Evaluating orderbook depth & capacity for {args.depth} on {exch.upper()}...[/bold cyan]")
        
        spot_fee = 0.10 if exch == "bybit" else 0.07 if args.spot_fee is None else args.spot_fee
        perp_fee = 0.055 if exch == "bybit" else 0.035 if args.perp_fee is None else args.perp_fee
        calc = FundingRateCalculator(spot_taker_fee=spot_fee/100.0, perp_taker_fee=perp_fee/100.0)

        from src.calculator import parse_base_multiplier, safe_float
        if exch == "bybit":
            linear_tickers, _ = bybit_client.get_linear_market_data()
            perp_ticker = next((t for t in linear_tickers if t.get("symbol") == args.depth), {})
            funding_rate = safe_float(perp_ticker.get("fundingRate"))
            interval_hr = safe_float(perp_ticker.get("fundingIntervalHour"), default=8.0)
            hourly_funding = funding_rate / (interval_hr if interval_hr > 0 else 8.0)

            base_coin = args.depth[:-4] if args.depth.endswith("USDT") or args.depth.endswith("USDC") else args.depth
            mult, clean_base = parse_base_multiplier(base_coin)
            spot_sym = f"{clean_base}USDT"

            spot_book = bybit_client.get_orderbook("spot", spot_sym, limit=200)
            perp_book = bybit_client.get_orderbook("linear", args.depth, limit=200)

            spot_asks = [(safe_float(px)*mult, safe_float(sz)/mult) for px, sz in spot_book.get("a", [])]
            spot_bids = [(safe_float(px)*mult, safe_float(sz)/mult) for px, sz in spot_book.get("b", [])]
            perp_asks = [(safe_float(px), safe_float(sz)) for px, sz in perp_book.get("a", [])]
            perp_bids = [(safe_float(px), safe_float(sz)) for px, sz in perp_book.get("b", [])]

            spot_mid = (spot_asks[0][0] + spot_bids[0][0]) / 2.0 if spot_asks and spot_bids else safe_float(perp_ticker.get("lastPrice"))
            perp_mid = (perp_asks[0][0] + perp_bids[0][0]) / 2.0 if perp_asks and perp_bids else safe_float(perp_ticker.get("lastPrice"))
        else:
            perp_univ, perp_ctxs = hl_client.get_perp_market_data()
            matched_idx = next((i for i, u in enumerate(perp_univ) if u.get("name") == args.depth), 0)
            ctx = perp_ctxs[matched_idx] if matched_idx < len(perp_ctxs) else {}
            hourly_funding = safe_float(ctx.get("funding"))

            hl_book = hl_client.get_l2_book(args.depth)
            levels = hl_book.get("levels", [[], []])
            perp_bids = [(safe_float(item.get("px")), safe_float(item.get("sz"))) for item in (levels[0] if len(levels)>0 else [])]
            perp_asks = [(safe_float(item.get("px")), safe_float(item.get("sz"))) for item in (levels[1] if len(levels)>1 else [])]
            spot_asks, spot_bids = perp_asks, perp_bids
            spot_mid = (spot_asks[0][0] + spot_bids[0][0]) / 2.0 if spot_asks and spot_bids else safe_float(ctx.get("midPx"))
            perp_mid = spot_mid

        eval_res = calc.evaluate_capital_capacity(
            spot_asks=spot_asks, spot_bids=spot_bids, spot_mid_px=spot_mid,
            perp_asks=perp_asks, perp_bids=perp_bids, perp_mid_px=perp_mid,
            hourly_funding=hourly_funding, custom_target_usd=args.target_usd
        )

        cap_payload = {
            "symbol": args.depth,
            "exchange": exch,
            "data": eval_res
        }

        if args.json:
            print(json.dumps(cap_payload, indent=2))
        else:
            panel, table = render_depth_cli_table(cap_payload)
            console.print(panel)
            console.print(table)
        return

    # Determine default fees based on exchange if not specified
    if args.spot_fee is None:
        spot_fee = 0.10 if args.exchange == "bybit" else 0.07
    else:
        spot_fee = args.spot_fee

    if args.perp_fee is None:
        perp_fee = 0.055 if args.exchange == "bybit" else 0.035
    else:
        perp_fee = args.perp_fee

    calculator = FundingRateCalculator(
        spot_taker_fee=spot_fee / 100.0,
        perp_taker_fee=perp_fee / 100.0,
        enable_aliases=not args.no_alias,
        max_spread_pct=args.max_spread
    )

    if args.json:
        results = fetch_data_for_exchange(args.exchange, hl_client, bybit_client, calculator)
        print(json.dumps(results, indent=2))
        return

    if args.web:
        from server import start_web_server
        start_web_server(args.port, hl_client, bybit_client, calculator, args.interval)
    elif args.live:
        run_cli_loop(args.exchange, hl_client, bybit_client, calculator, args.interval, args.limit)
    else:
        console = Console()
        console.print(f"[bold cyan]Fetching Capital Funding Rate Arbitrage Snapshot ({args.exchange.upper()})...[/bold cyan]")
        results = fetch_data_for_exchange(args.exchange, hl_client, bybit_client, calculator)
        table = render_cli_table(
            results,
            calculator.spot_taker_fee * 100.0,
            calculator.perp_taker_fee * 100.0,
            args.limit
        )
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        footer_text = Text(f"Snapshot taken: {timestamp} UTC | Monitored Pairs: {len(results)} | Fees: Spot Taker {calculator.spot_taker_fee*100:.3f}%, Perp Taker {calculator.perp_taker_fee*100:.3f}%", style="dim italic")
        panel = Panel(table, subtitle=footer_text)
        console.print(panel)

        legend_text = (
            "[bold cyan][🏛️ 官方 Canonical][/bold cyan] 官方创世/原生资产 (支持50%质押) | "
            "[bold yellow][🌉 Unit 映射][/bold yellow] 外部资产封装映射 (全款对冲) | "
            "[bold blue][🌉 跨链桥接][/bold blue] 跨链桥封装资产 | "
            "[bold magenta][⚡ HIP-1 发币][/bold magenta] 社区无许可发币 (核对Pair ID防撞车) | "
            "[dim][🔢 1000x][/dim] 乘数合约 | [cyan][EVM][/cyan] 具备EVM合约\n"
            "[dim]💡 提示: 运行 [bold]python3 monitor.py --guide[/bold] 可查看详细套利业务手册与风控准则。[/dim]"
        )
        console.print(Panel(legend_text, title="[bold cyan]💡 标的来源性质图例与业务提示[/bold cyan]", border_style="dim cyan"))

if __name__ == "__main__":
    main()



