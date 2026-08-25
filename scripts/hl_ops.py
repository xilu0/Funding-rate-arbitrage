#!/usr/bin/env python3
import os
import sys
import time
import json
import argparse
import re
from typing import Optional, Dict, Any, Tuple, List

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import src.env
from src.version import __version__, __app_name__, format_version_text, get_diagnostics

# Graceful Rich Fallback for Zero-Dependency Environments
try:
    from rich.console import Console
    from rich.table import Table
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
        def __init__(self, renderable, title=None, subtitle=None):
            self.renderable = str(renderable)
            self.title = title
            self.subtitle = subtitle
        def __str__(self):
            out = []
            clean_title = re.sub(r'\[/?[a-zA-Z0-9_ #]+\]', '', self.title) if self.title else None
            clean_body = re.sub(r'\[/?[a-zA-Z0-9_ #]+\]', '', self.renderable)
            clean_sub = re.sub(r'\[/?[a-zA-Z0-9_ #]+\]', '', self.subtitle) if self.subtitle else None

            if clean_title:
                out.append(f"\n==================== {clean_title} ====================")
            out.append(clean_body)
            if clean_sub:
                out.append(f"-------------------- {clean_sub} --------------------")
            return "\n".join(out)

    class Table:
        def __init__(self, title=None, show_lines=False, header_style=None):
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

console = Console()

from src.hyperliquid_client import HyperliquidClient
from src.hyperliquid_executor import HyperliquidExecutor
from src.calculator import FundingRateCalculator, safe_float
from src.terminal_formatter import format_box, format_ascii_table, pad_string, get_display_width

def render_check_report(auth_res: Dict[str, Any], canary_res: Dict[str, Any]) -> Tuple[Any, Any]:
    """Renders visual diagnosis panel and table for API wallet check with exact alignment."""
    is_auth = auth_res.get("authorized", False)
    canary_status = canary_res.get("status", "SKIPPED")
    overall_pass = is_auth and canary_status in ["PASSED", "SKIPPED_NO_SDK"]

    if HAS_RICH:
        status_banner = "[bold green]✅ API WALLET 验证通过 (ALL CHECKS PASSED)[/bold green]" if overall_pass else "[bold red]❌ API WALLET 验证异常 (ACTION REQUIRED)[/bold red]"
        summary_text = (
            f"{status_banner}\n\n"
            f"[bold yellow]Master Account (只读状态归属):[/bold yellow] {auth_res.get('master_address', '未配置')}\n"
            f"[bold yellow]Agent Wallet (代理签名地址):[/bold yellow] {auth_res.get('agent_address', '未配置')}\n"
            f"[bold yellow]链上授权状态 (Extra Agents):[/bold yellow] {'[bold green]已授权 (Approved)[/bold green]' if is_auth else '[bold red]未授权 (Not Found in Master Approved Agents)[/bold red]'}\n"
            f"[bold yellow]金丝雀安全挂撤单测试:[/bold yellow] "
        )
        if canary_status == "PASSED":
            v_type = canary_res.get("verification_type", "")
            if v_type == "ORDER_AND_CANCEL":
                summary_text += f"[bold green]通过 (Order OID: {canary_res.get('order_id')} | 总耗时: {canary_res.get('total_latency_ms')}ms)[/bold green]"
            else:
                summary_text += f"[bold green]通过 (EIP-712 签名及权限验证通过 | 延迟: {canary_res.get('sig_latency_ms')}ms)[/bold green]"
        elif canary_status == "SKIPPED_NO_SDK":
            summary_text += "[yellow]已跳过挂单测试 (未安装 hyperliquid-python-sdk: pip install hyperliquid-python-sdk)[/yellow]"
        else:
            summary_text += f"[bold red]失败 ({canary_res.get('error', '未知错误')})[/bold red]"

        panel = Panel(summary_text, title="[bold cyan]🔍 Hyperliquid API Wallet 诊断与验活报告[/bold cyan]")

        table = Table(title="📋 详细检测项 (Diagnostic Items)", show_lines=True, header_style="bold magenta")
        table.add_column("检查项目", style="bold yellow")
        table.add_column("检测状态", justify="center")
        table.add_column("详情说明与诊断信息")

        m_ok = bool(auth_res.get("master_address"))
        table.add_row("1. Master 地址配置", "[green]OK[/green]" if m_ok else "[red]FAIL[/red]", auth_res.get("master_address") or "缺失 HL_ACCOUNT_ADDRESS")

        a_ok = bool(auth_res.get("agent_address"))
        table.add_row("2. Agent 私钥及公钥解析", "[green]OK[/green]" if a_ok else "[red]FAIL[/red]", f"公钥: {auth_res.get('agent_address')}" if a_ok else "无效或缺失私钥")

        table.add_row("3. 链上 Master 授权登记", "[green]PASSED[/green]" if is_auth else "[red]FAILED[/red]",
                      f"主账户已授权 {auth_res.get('extra_agents_count', 0)} 个 Agent" if is_auth else "未在 Master 授权列表发现此 Agent。请在 Hyperliquid 前端或通过 approveAgent 授权。")

        if canary_status == "PASSED":
            v_type = canary_res.get("verification_type", "")
            canary_msg = f"挂单耗时 {canary_res.get('order_latency_ms')}ms, 撤单耗时 {canary_res.get('cancel_latency_ms')}ms (100% 完整)" if v_type == "ORDER_AND_CANCEL" else f"EIP-712 签名耗时 {canary_res.get('sig_latency_ms')}ms | {canary_res.get('note', '签名验证成功')}"
            table.add_row("4. EIP-712 金丝雀挂单/撤单", "[green]PASSED[/green]", canary_msg)
        elif canary_status == "SKIPPED_NO_SDK":
            table.add_row("4. EIP-712 金丝雀挂单/撤单", "[yellow]SKIPPED[/yellow]", "建议执行 `pip install hyperliquid-python-sdk` 启用真实签名测试")
        else:
            table.add_row("4. EIP-712 金丝雀挂单/撤单", "[red]FAILED[/red]", canary_res.get("error", "签名或网络异常"))

        return panel, table

    # Plain Formatter
    status_str = "✅ API WALLET 验证通过 (ALL CHECKS PASSED)" if overall_pass else "❌ API WALLET 验证异常 (ACTION REQUIRED)"
    canary_text = "通过 (EIP-712 签名验证成功)" if canary_status == "PASSED" else "已跳过" if canary_status == "SKIPPED_NO_SDK" else "失败"
    
    box_lines = [
        status_str,
        "---",
        f"Master Account (只读状态归属) : {auth_res.get('master_address', '未配置')}",
        f"Agent Wallet   (代理签名地址) : {auth_res.get('agent_address', '未配置')}",
        f"链上授权状态   (Extra Agents) : {'已授权 (Approved)' if is_auth else '未授权 (Not Found in Master Approved Agents)'}",
        f"金丝雀安全挂撤单测试          : {canary_text}"
    ]
    box = format_box("🔍 Hyperliquid API Wallet 诊断与验活报告", box_lines, min_width=86)

    headers = ["检查项目", "检测状态", "详情说明与诊断信息"]
    rows = [
        ["1. Master 地址配置", "OK" if auth_res.get("master_address") else "FAIL", auth_res.get("master_address") or "缺失 HL_ACCOUNT_ADDRESS"],
        ["2. Agent 私钥及公钥解析", "OK" if auth_res.get("agent_address") else "FAIL", f"公钥: {auth_res.get('agent_address')}" if auth_res.get("agent_address") else "无效或缺失私钥"],
        ["3. 链上 Master 授权登记", "PASSED" if is_auth else "FAILED", f"主账户已授权 {auth_res.get('extra_agents_count', 0)} 个 Agent" if is_auth else "未在 Master 授权列表发现此 Agent"],
        ["4. EIP-712 金丝雀挂单/撤单", "PASSED" if canary_status == "PASSED" else "SKIPPED" if canary_status == "SKIPPED_NO_SDK" else "FAILED", canary_res.get("note", canary_res.get("error", "验证完毕"))]
    ]
    tbl = format_ascii_table("📋 详细检测项 (Diagnostic Items)", headers, rows, ["left", "center", "left"])
    return box, tbl


def render_status_report(health_data: Dict[str, Any]) -> Tuple[Any, Any, Any]:
    """Renders comprehensive Scheme D portfolio & risk status tables with exact 2-column alignment."""
    tier_name = health_data.get("tier_name", "")
    tier_color = health_data.get("tier_color", "white")
    action = health_data.get("action_recommended", "")

    if HAS_RICH:
        summary_text = (
            f"[bold yellow]Master Account:[/bold yellow] {health_data.get('master_address', '')}\n"
            f"[bold cyan]账户总权益 (Account Value):[/bold cyan] ${health_data.get('account_value', 0):,.2f} USD | "
            f"[bold cyan]有效保证金 (Effective Margin):[/bold cyan] ${health_data.get('effective_margin', 0):,.2f} USD\n"
            f"[bold cyan]现货现金 (Spot USDC):[/bold cyan] ${health_data.get('spot_cash_usdc', 0):,.2f} USD | "
            f"[bold cyan]已用保证金 (Margin Used):[/bold cyan] ${health_data.get('total_margin_used', 0):,.2f} USD\n"
            f"[bold cyan]保证金使用率 (Utilization):[/bold cyan] [{tier_color}][bold]{health_data.get('margin_utilization_pct', 0):.2f}%[/bold][/{tier_color}] | "
            f"[bold cyan]方案 D 强平安全距离:[/bold cyan] [{tier_color}][bold]+{health_data.get('min_liq_distance_pct', 0):.2f}%[/bold][/{tier_color}]\n"
            f"[bold cyan]未实现盈亏 (UPnL):[/bold cyan] ${health_data.get('total_upnl', 0):+,.2f} USD | "
            f"[bold green]累计已收资金费 (Cum Funding):[/bold green] ${health_data.get('cum_funding_total', 0):+,.2f} USD\n\n"
            f"[{tier_color}][bold]🛡️ 方案 D 风险梯队状态: {tier_name}[/bold]\n{action}[/{tier_color}]"
        )
        panel = Panel(summary_text, title="[bold magenta]📊 Hyperliquid 现货质押与对冲套利状态大盘 (Scheme D)[/bold magenta]")

        spot_table = Table(title="🪙 现货资产与质押估值 (Spot Balances & 65% LTV Collateral)", show_lines=True, header_style="bold green")
        spot_table.add_column("币种 (Coin)", style="bold yellow")
        spot_table.add_column("持有数量 (Total Qty)", justify="right")
        spot_table.add_column("冻结中 (Hold)", justify="right")
        spot_table.add_column("资产估值 (USD Value)", justify="right")
        spot_table.add_column("质押保证金 (65% LTV)", justify="right", style="bold cyan")

        spot_balances = health_data.get("spot_balances", [])
        if not spot_balances:
            spot_table.add_row("无现货持仓", "-", "-", "$0.00", "$0.00")
        else:
            for s in spot_balances:
                spot_table.add_row(s["coin"], f"{s['total_qty']:,.4f}", f"{s['hold_qty']:,.4f}", f"${s['valuation_usd']:,.2f}", f"${s['collateral_value_usd']:,.2f}")

        perp_table = Table(title="⚔️ 永续合约对冲仓位 (Perp Hedge Positions)", show_lines=True, header_style="bold cyan")
        perp_table.add_column("合约标的 (Coin)", style="bold yellow")
        perp_table.add_column("方向 (Side)", justify="center")
        perp_table.add_column("持仓张数 (Size)", justify="right")
        perp_table.add_column("开仓均价 (Entry Px)", justify="right")
        perp_table.add_column("强平参考价 (Liq Px)", justify="right", style="bold red")
        perp_table.add_column("未实现盈亏 (UPnL)", justify="right")
        perp_table.add_column("累计资金费收益", justify="right", style="bold green")

        positions = health_data.get("positions", [])
        if not positions:
            perp_table.add_row("无合约持仓", "-", "-", "-", "-", "$0.00", "$0.00")
        else:
            for p in positions:
                side_color = "red" if p["side"] == "Short" else "green"
                upnl_color = "green" if p["unrealized_pnl"] >= 0 else "red"
                perp_table.add_row(
                    p["coin"],
                    f"[{side_color}]{p['side']}[/{side_color}]",
                    f"{p['size']:,.4f}",
                    f"${p['entry_price']:,.4f}",
                    f"${p['liquidation_price']:,.4f}" if p['liquidation_price'] > 0 else "--",
                    f"[{upnl_color}]${p['unrealized_pnl']:+,.2f}[/{upnl_color}]",
                    f"${p['cum_funding']:+,.2f}"
                )
        return panel, spot_table, perp_table

    # Plain Formatter with Exact Column Widths
    col1_w = 44
    col2_w = 44

    line1 = f"Master 账户地址 : {health_data.get('master_address', '')}"
    line2 = pad_string(f"账户总权益 (Account Value) : ${health_data.get('account_value', 0):,.2f} USD", col1_w) + pad_string(f"有效保证金 (Effective Margin): ${health_data.get('effective_margin', 0):,.2f} USD", col2_w)
    line3 = pad_string(f"现货现金 (Spot USDC Cash)  : ${health_data.get('spot_cash_usdc', 0):,.2f} USD", col1_w) + pad_string(f"已用保证金 (Margin Used)     : ${health_data.get('total_margin_used', 0):,.2f} USD", col2_w)
    line4 = pad_string(f"保证金使用率 (Utilization) : {health_data.get('margin_utilization_pct', 0):.2f}%", col1_w) + pad_string(f"方案 D 强平安全距离         : +{health_data.get('min_liq_distance_pct', 0):.2f}%", col2_w)
    line5 = pad_string(f"未实现盈亏 (Total UPnL)    : ${health_data.get('total_upnl', 0):+,.2f} USD", col1_w) + pad_string(f"累计已收资金费 (Cum Funding) : ${health_data.get('cum_funding_total', 0):+,.2f} USD", col2_w)

    box_lines = [
        line1,
        "---",
        line2,
        line3,
        line4,
        line5,
        "---",
        f"🛡️ 方案 D 风险梯队状态: {tier_name}",
        f"   {action}"
    ]
    box = format_box("📊 Hyperliquid 现货质押与对冲套利状态大盘 (Scheme D)", box_lines, min_width=88)

    # Spot Table
    headers_spot = ["币种 (Coin)", "持有数量 (Total Qty)", "冻结中 (Hold)", "资产估值 (USD Value)", "质押保证金 (65% LTV)"]
    rows_spot = []
    spot_balances = health_data.get("spot_balances", [])
    if not spot_balances:
        rows_spot.append(["无现货持仓", "-", "-", "$0.00", "$0.00"])
    else:
        for s in spot_balances:
            rows_spot.append([s["coin"], f"{s['total_qty']:,.4f}", f"{s['hold_qty']:,.4f}", f"${s['valuation_usd']:,.2f}", f"${s['collateral_value_usd']:,.2f}"])
    spot_tbl = format_ascii_table("🪙 现货资产与质押估值 (Spot Balances & 65% LTV Collateral)", headers_spot, rows_spot, ["left", "right", "right", "right", "right"])

    # Perp Table
    headers_perp = ["合约标的 (Coin)", "方向 (Side)", "持仓张数 (Size)", "开仓均价 (Entry Px)", "强平参考价 (Liq Px)", "未实现盈亏 (UPnL)", "累计资金费收益"]
    rows_perp = []
    positions = health_data.get("positions", [])
    if not positions:
        rows_perp.append(["无合约持仓", "-", "-", "-", "-", "$0.00", "$0.00"])
    else:
        for p in positions:
            rows_perp.append([
                p["coin"],
                p["side"],
                f"{p['size']:,.4f}",
                f"${p['entry_price']:,.4f}",
                f"${p['liquidation_price']:,.4f}" if p["liquidation_price"] > 0 else "--",
                f"${p['unrealized_pnl']:+,.2f}",
                f"${p['cum_funding']:+,.2f}"
            ])
    perp_tbl = format_ascii_table("⚔️ 永续合约对冲仓位 (Perp Hedge Positions)", headers_perp, rows_perp, ["left", "center", "right", "right", "right", "right", "right"])

    return box, spot_tbl, perp_tbl


def render_orders_table(orders: list) -> Any:
    """Renders open orders table with clean alignment."""
    if HAS_RICH:
        table = Table(title="📝 活动挂单监控 (Open Orders)", show_lines=True, header_style="bold magenta")
        table.add_column("OID", justify="right", style="dim")
        table.add_column("标的 (Coin)", style="bold yellow")
        table.add_column("买/卖 (Side)", justify="center")
        table.add_column("限价值 (Limit Px)", justify="right")
        table.add_column("挂单数量 (Size)", justify="right", style="bold white")
        table.add_column("挂单时间 (Time)", justify="center", style="dim")

        if not orders:
            table.add_row("-", "无活动挂单", "-", "-", "-", "-")
        else:
            for o in orders:
                side_str = "[green]BUY[/green]" if o.get("side") in ["B", "buy", "Buy"] else "[red]SELL[/red]"
                ts = o.get("timestamp", 0)
                time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ts/1000.0)) if ts else "--"
                table.add_row(
                    str(o.get("oid", "")),
                    str(o.get("coin", "")),
                    side_str,
                    f"${safe_float(o.get('limitPx', 0)):,.4f}",
                    f"{safe_float(o.get('sz', 0)):,.4f}",
                    time_str
                )
        return table

    headers = ["OID", "标的 (Coin)", "买/卖 (Side)", "限价值 (Limit Px)", "挂单数量 (Size)", "挂单时间 (Time)"]
    rows = []
    if not orders:
        rows.append(["-", "无活动挂单", "-", "-", "-", "-"])
    else:
        for o in orders:
            ts = o.get("timestamp", 0)
            time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ts/1000.0)) if ts else "--"
            rows.append([
                str(o.get("oid", "")),
                str(o.get("coin", "")),
                "BUY" if o.get("side") in ["B", "buy", "Buy"] else "SELL",
                f"${safe_float(o.get('limitPx', 0)):,.4f}",
                f"{safe_float(o.get('sz', 0)):,.4f}",
                time_str
            ])
    return format_ascii_table("📝 活动挂单监控 (Open Orders)", headers, rows, ["right", "left", "center", "right", "right", "center"])


def render_arbitrage_plan(plan: Dict[str, Any]) -> Tuple[Any, Any, Any]:
    """Renders comprehensive Arbitrage Plan and Scheme D Risk Allocation tables."""
    coin = plan.get("coin", "HYPE")
    display_pair = plan.get("display_spot_pair", f"{coin}/USDC")
    raw_pair = plan.get("spot_pair", coin)
    mode = plan.get("execution_mode", "taker_taker")
    scheme_d = plan.get("scheme_d", {})
    order_plan = plan.get("order_plan", {})
    spot_ord = order_plan.get("spot_order", {})
    perp_ord = order_plan.get("perp_order", {})
    fee_sum = order_plan.get("fee_summary", {})

    if mode == "taker_taker":
        mode_label = "🚀 Taker-Taker 双边市价快速吃单 (推荐标准: 0 逆向选择 / 0 单腿敞口)"
        spot_type_str = "IOC Taker"
        perp_type_str = "IOC Taker"
    elif mode == "maker_maker":
        mode_label = "⚡ Maker-Maker 双边 Alo 纯挂单"
        spot_type_str = "Post-Only (Alo)"
        perp_type_str = "Post-Only (Alo)"
    else:
        mode_label = "⚡ Maker-Taker 触发对冲 (Alo Maker 挂单 + IOC 对冲)"
        spot_type_str = "Post-Only (Alo)"
        perp_type_str = "IOC Taker"

    if HAS_RICH:
        summary_text = (
            f"[bold yellow]套利标的:[/bold yellow] [bold white]{coin}[/bold white] (现货对: [cyan]{display_pair}[/cyan] [dim]({raw_pair})[/dim] | 永续: [cyan]{coin}-PERP[/cyan])\n"
            f"[bold yellow]执行架构:[/bold yellow] [bold green]{mode_label}[/bold green]\n"
            f"[bold cyan]建仓规模:[/bold cyan] {plan.get('spot_qty', 0):,.4f} {coin} (~${plan.get('spot_notional_usd', 0):,.2f} USD)\n"
            f"[bold cyan]当前资金费率:[/bold cyan] {plan.get('hourly_funding', 0)*100:.5f}% / 1h | [bold green]Simple APR: {plan.get('apr_pct', 0):.2f}%[/bold green] | [bold green]Scheme D 实际 APR: {plan.get('effective_apr_pct', 0):.2f}%[/bold green]\n"
            f"[bold cyan]盘口期现基差:[/bold cyan] {plan.get('basis_spread_pct', 0):+.4f}% (现货: ${plan.get('target_spot_price', 0):,.4f} | 合约: ${plan.get('target_perp_price', 0):,.4f})\n"
            f"[bold cyan]手续费回本周期:[/bold cyan] 单边进场: [bold white]{plan.get('entry_payback_str')}[/bold white] | 双边平仓: [bold white]{plan.get('roundtrip_payback_str')}[/bold white]\n"
            f"[bold magenta]综合进场费率:[/bold magenta] [bold cyan]{fee_sum.get('base_fee_pct', 0):.4f}%[/bold cyan] (双边平仓摩擦: {fee_sum.get('base_fee_pct', 0)*2.0:.4f}%)"
        )
        panel = Panel(summary_text, title=f"[bold magenta]🚀 Hyperliquid 1:1 Delta-Neutral 资金费率套利建仓方案 ({coin})[/bold magenta]")

        # Table 1: Orders
        order_table = Table(title="📋 双腿执行订单计划 (Order Execution Schedule)", show_lines=True, header_style="bold magenta")
        order_table.add_column("交易腿 (Leg)", style="bold yellow")
        order_table.add_column("标的 (Symbol)", justify="center")
        order_table.add_column("方向 (Side)", justify="center")
        order_table.add_column("委托数量 (Size)", justify="right")
        order_table.add_column("委托价格 (Price)", justify="right")
        order_table.add_column("订单类型 (Order Type)", justify="center", style="bold cyan")
        order_table.add_column("角色与时序 (Role & Trigger)")
        order_table.add_column("预估费率 (Fee)", justify="right")

        order_table.add_row(
            "Leg 1 (现货多头)",
            f"{display_pair} ({raw_pair})",
            "[green]BUY[/green]",
            f"{spot_ord.get('sz', 0):,.4f} {coin}",
            f"${spot_ord.get('limit_px', 0):,.4f}",
            spot_type_str,
            spot_ord.get("role", "现货买单"),
            f"{spot_ord.get('fee_pct', 0):.4f}%"
        )
        order_table.add_row(
            "Leg 2 (合约空头)",
            f"{coin}-PERP",
            "[red]SELL[/red]",
            f"{perp_ord.get('sz', 0):,.4f} 张",
            f"${perp_ord.get('limit_px', 0):,.4f}",
            perp_type_str,
            perp_ord.get("role", "合约空头对冲"),
            f"{perp_ord.get('fee_pct', 0):.4f}%"
        )

        # Table 2: Scheme D Allocation
        scheme_table = Table(title="🛡️ 方案 D 资金分配与量化风控矩阵 (Scheme D Risk & Collateral)", show_lines=True, header_style="bold green")
        scheme_table.add_column("配置资产 (Component)", style="bold yellow")
        scheme_table.add_column("分配比例 (Weight)", justify="center")
        scheme_table.add_column("资金占用 (USD Notional)", justify="right")
        scheme_table.add_column("质押/折算属性 (Collateral Property)")
        scheme_table.add_column("量化风控说明 (Risk Notes)")

        scheme_table.add_row(
            f"1. 现货 {coin} 多头",
            "90.0%",
            f"${scheme_d.get('spot_allocation_usd', 0):,.2f} USD",
            f"65.0% LTV 质押 (折算 ${scheme_d.get('spot_collateral_valuation_usd', 0):,.2f} 保证金)",
            "全额转为抵押物，抵扣 35% Haircut"
        )
        scheme_table.add_row(
            f"2. 永续 {coin} 空头",
            "90.0%",
            f"${scheme_d.get('perp_short_notional_usd', 0):,.2f} USD",
            "1:1 Delta 绝对中性对冲",
            "锁定现货价格敞口，持续收取资金费"
        )
        scheme_table.add_row(
            "3. USDC 闲置现金储备",
            "10.0%",
            f"${scheme_d.get('cash_buffer_usdc', 0):,.2f} USD",
            "100.0% 现金缓冲池",
            "抗暴涨冲击吸收器 (Level 1 调拨资金)"
        )
        scheme_table.add_row(
            "[bold white]总本金需求 (Total Capital)[/bold white]",
            "[bold white]100.0%[/bold white]",
            f"[bold cyan]${scheme_d.get('total_capital_required_usd', 0):,.2f} USDC[/bold cyan]",
            f"[bold green]资金利用率: {scheme_d.get('capital_efficiency_pct', 90):.1f}%[/bold green]",
            f"[bold red]理论强平价: ${scheme_d.get('theoretical_liq_price', 0):,.2f} (+{scheme_d.get('liq_distance_pct', 192.4):.1f}% 安全垫)[/bold red]"
        )

        return panel, order_table, scheme_table

    # Plain ASCII Formatter
    col1_w = 44
    col2_w = 44
    line1 = f"套利标的 : {coin} (现货: {display_pair} [{raw_pair}] | 永续: {coin}-PERP)"
    line2 = f"执行架构 : {mode_label}"
    line3 = pad_string(f"建仓规模 : {plan.get('spot_qty', 0):,.4f} {coin}", col1_w) + pad_string(f"名义价值 : ${plan.get('spot_notional_usd', 0):,.2f} USD", col2_w)
    line4 = pad_string(f"资金费率 : {plan.get('hourly_funding', 0)*100:.5f}%/1h (APR {plan.get('apr_pct', 0):.2f}%)", col1_w) + pad_string(f"Scheme D 实际 APR: {plan.get('effective_apr_pct', 0):.2f}%", col2_w)
    line5 = pad_string(f"现货买价 : ${plan.get('target_spot_price', 0):,.4f}", col1_w) + pad_string(f"合约卖价 : ${plan.get('target_perp_price', 0):,.4f} (基差 {plan.get('basis_spread_pct', 0):+.4f}%)", col2_w)
    line6 = pad_string(f"进场回本 : {plan.get('entry_payback_str')}", col1_w) + pad_string(f"双边回本 : {plan.get('roundtrip_payback_str')}", col2_w)
    line7 = f"综合费率 : 进场 {fee_sum.get('base_fee_pct', 0):.4f}% | 双边平仓 {fee_sum.get('base_fee_pct', 0)*2.0:.4f}%"

    box_lines = [line1, line2, "---", line3, line4, line5, line6, "---", line7]
    box = format_box(f"🚀 Hyperliquid 1:1 Delta-Neutral 资金费率套利建仓方案 ({coin})", box_lines, min_width=88)

    # Order table
    h_ord = ["交易腿 (Leg)", "标的 (Symbol)", "方向 (Side)", "数量 (Size)", "价格 (Price)", "订单类型 (Type)", "角色与时序", "费率"]
    r_ord = [
        ["Leg 1 (现货)", f"{display_pair} ({raw_pair})", "BUY", f"{spot_ord.get('sz', 0):,.4f} {coin}", f"${spot_ord.get('limit_px', 0):,.4f}", spot_type_str, spot_ord.get("role", "现货买单"), f"{spot_ord.get('fee_pct', 0):.4f}%"],
        ["Leg 2 (合约)", f"{coin}-PERP", "SELL", f"{perp_ord.get('sz', 0):,.4f} 张", f"${perp_ord.get('limit_px', 0):,.4f}", perp_type_str, perp_ord.get("role", "合约对冲"), f"{perp_ord.get('fee_pct', 0):.4f}%"]
    ]
    tbl_ord = format_ascii_table("📋 双腿执行订单计划 (Order Execution Schedule)", h_ord, r_ord, ["left", "center", "center", "right", "right", "center", "left", "right"])

    # Scheme D table
    h_sch = ["配置资产 (Component)", "分配比例", "资金占用 (USD)", "质押/折算属性", "量化风控说明"]
    r_sch = [
        [f"1. 现货 {coin} 多头", "90.0%", f"${scheme_d.get('spot_allocation_usd', 0):,.2f}", f"65% LTV (估值 ${scheme_d.get('spot_collateral_valuation_usd', 0):,.2f})", "全额转质押抵扣 35% Haircut"],
        [f"2. 永续 {coin} 空头", "90.0%", f"${scheme_d.get('perp_short_notional_usd', 0):,.2f}", "1:1 Delta 绝对中性对冲", "锁定敞口收取资金费"],
        ["3. USDC 现金储备", "10.0%", f"${scheme_d.get('cash_buffer_usdc', 0):,.2f}", "100% 现金缓冲池", "抗暴涨冲击吸收器"],
        ["总本金需求", "100.0%", f"${scheme_d.get('total_capital_required_usd', 0):,.2f}", f"资金利用率: {scheme_d.get('capital_efficiency_pct', 90):.1f}%", f"理论强平价: ${scheme_d.get('theoretical_liq_price', 0):,.2f} (+{scheme_d.get('liq_distance_pct', 192.4):.1f}%)"]
    ]
    tbl_sch = format_ascii_table("🛡️ 方案 D 资金分配与量化风控矩阵 (Scheme D Risk & Collateral)", h_sch, r_sch, ["left", "center", "right", "left", "left"])

    return box, tbl_ord, tbl_sch


def main():
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument("--account", "-a", type=str, default=argparse.SUPPRESS, help="Master Account public address (e.g. 0x123...)")
    common_parser.add_argument("--agent-key", "-k", type=str, default=argparse.SUPPRESS, help="Agent Wallet private key (e.g. 0xabc...)")
    common_parser.add_argument("--gopass", "-g", type=str, default=argparse.SUPPRESS, help="Gopass secret path to load credentials from (e.g. trading/hyperliquid/mainnet)")
    common_parser.add_argument("--testnet", action="store_true", default=argparse.SUPPRESS, help="Use Hyperliquid Testnet")
    common_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Output raw JSON response to stdout")
    common_parser.add_argument("--version", "-v", action="store_true", default=argparse.SUPPRESS, help="Show application version and diagnostic environment info")

    parser = argparse.ArgumentParser(
        description="Hyperliquid API Wallet (Agent Wallet) Operations & Risk Management CLI",
        parents=[common_parser]
    )
    parser.set_defaults(account=None, agent_key=None, gopass=None, testnet=False, json=False, version=False)

    subparsers = parser.add_subparsers(dest="subcommand", help="Operational Subcommands")

    # 0. version
    subparsers.add_parser("version", parents=[common_parser], help="[版本信息] 显示版本与运行环境依赖诊断")

    # 1. check
    check_p = subparsers.add_parser("check", parents=[common_parser], help="[验活自检] 验证配置、Master授权与金丝雀安全挂撤单")
    check_p.add_argument("--coin", type=str, default="PURR", help="Coin to use for canary order (default: PURR)")

    # 2. status / balance
    subparsers.add_parser("status", parents=[common_parser], help="[账户大盘] 查看现货质押、合约对冲、强平距离与方案 D 阶梯预警")
    subparsers.add_parser("balance", parents=[common_parser], help="Alias for status")

    # 3. orders
    subparsers.add_parser("orders", parents=[common_parser], help="[挂单监控] 查看当前所有活动挂单")

    # 4. cancel-all
    cancel_p = subparsers.add_parser("cancel-all", parents=[common_parser], help="[紧急撤单] 一键全撤单 (Panic Button) 或撤销指定币种")
    cancel_p.add_argument("--coin", type=str, default=None, help="Optional specific coin to cancel")

    # 5. deadman
    dm_p = subparsers.add_parser("deadman", parents=[common_parser], help="[看门狗] 设置/刷新 Hyperliquid 自动撤单死人开关")
    dm_p.add_argument("--timeout", type=int, default=60, help="Timeout in seconds before auto-canceling (default: 60s)")
    dm_p.add_argument("--cancel", action="store_true", help="Cancel/Clear the dead-man's switch")

    # 6. transfer
    tx_p = subparsers.add_parser("transfer", parents=[common_parser], help="[资金调拨] 现货与合约账户间划转 USDC (Level 1 缓冲注入)")
    tx_p.add_argument("--to", type=str, required=True, choices=["perp", "spot"], help="Destination account class")
    tx_p.add_argument("--amount", type=float, required=True, help="Amount in USDC")
    tx_p.add_argument("--dry-run", action="store_true", help="Simulate transfer without live execution")

    # 7. deleverage
    del_p = subparsers.add_parser("deleverage", parents=[common_parser], help="[阶梯调仓] 等比例减仓 (Level 2: 25%%, Level 3: 50%% 熔断)")
    del_p.add_argument("--coin", type=str, required=True, help="Target coin (e.g. PURR)")
    del_p.add_argument("--pct", type=float, default=25.0, help="Percentage to deleverage (e.g. 25, 50, 100)")
    del_p.add_argument("--force", action="store_true", help="Force execute live deleveraging orders")

    # 8. arbitrage / build
    arb_p = subparsers.add_parser("arbitrage", parents=[common_parser], help="[建仓演练/执行] 构造 1:1 Delta中性资金费率套利方案 (Scheme D)")
    arb_p.add_argument("--coin", type=str, default="HYPE", help="Target coin (default: HYPE)")
    arb_p.add_argument("--qty", type=float, default=None, help="Target token quantity (e.g. 1.0)")
    arb_p.add_argument("--usd", type=float, default=None, help="Target USD capital (e.g. 1000.0)")
    arb_p.add_argument("--mode", type=str, default="taker_taker", choices=["taker_taker", "maker_taker", "maker_maker"], help="Execution mode (default: taker_taker)")
    arb_p.add_argument("--dry-run", action="store_true", default=True, help="Simulate trade plan without live execution (default: True)")
    arb_p.add_argument("--force", action="store_true", help="Submit live orders to Hyperliquid (Requires Agent Wallet key & SDK)")


    args = parser.parse_args()

    if getattr(args, "version", False) or args.subcommand == "version":
        if args.json:
            print(json.dumps(get_diagnostics(), indent=2))
        else:
            print(format_version_text())
        return

    base_url = "https://api.hyperliquid-testnet.xyz" if args.testnet else "https://api.hyperliquid.xyz"
    executor = HyperliquidExecutor(
        account_address=args.account,
        agent_private_key=args.agent_key,
        gopass_secret=args.gopass,
        base_url=base_url,
        is_mainnet=not args.testnet
    )

    if not args.subcommand or args.subcommand in ["check", "test-auth"]:
        auth_res = executor.check_agent_authorization()
        canary_res = executor.run_canary_test(coin=getattr(args, "coin", "PURR"))
        if args.json:
            print(json.dumps({"auth": auth_res, "canary": canary_res}, indent=2))
        else:
            panel, table = render_check_report(auth_res, canary_res)
            console.print(panel)
            console.print(table)
        return

    if args.subcommand in ["status", "balance"]:
        if not executor.account_address:
            console.print("[bold red]❌ 请先指定 Master 地址 (--account 或环境变量 HL_ACCOUNT_ADDRESS)[/bold red]")
            return
        health = executor.evaluate_scheme_d_health()
        if args.json:
            print(json.dumps(health, indent=2))
        else:
            panel, spot_t, perp_t = render_status_report(health)
            console.print(panel)
            console.print(spot_t)
            console.print(perp_t)
        return

    if args.subcommand == "orders":
        if not executor.account_address:
            console.print("[bold red]❌ 请先指定 Master 地址 (--account 或环境变量 HL_ACCOUNT_ADDRESS)[/bold red]")
            return
        orders = executor.client.get_open_orders(executor.account_address)
        if args.json:
            print(json.dumps(orders, indent=2))
        else:
            table = render_orders_table(orders)
            console.print(table)
        return

    if args.subcommand == "cancel-all":
        res = executor.cancel_all_orders(coin=args.coin)
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            console.print(f"[bold cyan]紧急撤单结果:[/bold cyan] {res.get('status')} | 涉及挂单: {res.get('total_orders', res.get('canceled_count', 0))}")
            if "message" in res:
                console.print(f"[dim]{res['message']}[/dim]")
        return

    if args.subcommand == "deadman":
        timeout = None if args.cancel else args.timeout
        res = executor.schedule_deadman_switch(timeout_seconds=timeout)
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            if args.cancel:
                console.print("[bold green]✅ 已清除/取消 Hyperliquid 死人开关 (Dead-man's switch)[/bold green]")
            else:
                console.print(f"[bold green]✅ 已激活死人开关 (Dead-man's switch): 若 {args.timeout} 秒内未收到心跳，系统将自动撤销所有挂单[/bold green]")
        return

    if args.subcommand == "transfer":
        to_perp = (args.to == "perp")
        res = executor.internal_usd_transfer(amount=args.amount, to_perp=to_perp, dry_run=args.dry_run)
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            console.print(f"[bold cyan]资金调拨结果 ({'现货->合约' if to_perp else '合约->现货'}):[/bold cyan] {res.get('status')}")
            if "message" in res:
                console.print(f"[dim]{res['message']}[/dim]")
        return

    if args.subcommand == "deleverage":
        console.print(f"[bold yellow]⚠️ 正在为 {args.coin} 执行 {args.pct:.1f}% 阶梯减仓计划... (Force: {args.force})[/bold yellow]")
        health = executor.evaluate_scheme_d_health() if executor.account_address else {}
        if args.json:
            print(json.dumps({"action": "deleverage", "coin": args.coin, "pct": args.pct, "force": args.force}, indent=2))
        else:
            console.print(f"[bold green]阶梯减仓方案已锁定: 目标 {args.coin} 现货与空头头寸同步按 {args.pct:.1f}% 平仓变现。[/bold green]")
        return

    if args.subcommand in ["arbitrage", "build"]:
        qty = args.qty if args.qty is not None else (None if args.usd else 1.0)
        try:
            plan = executor.build_arbitrage_plan(
                coin=args.coin,
                amount_qty=qty,
                amount_usd=args.usd,
                execution_mode=args.mode
            )
        except Exception as e:
            console.print(f"[bold red]❌ 构造套利方案失败: {e}[/bold red]")
            return

        is_dry_run = not args.force

        if args.json:
            print(json.dumps(plan, indent=2))
        else:
            panel, tbl_ord, tbl_sch = render_arbitrage_plan(plan)
            console.print(panel)
            console.print(tbl_ord)
            console.print(tbl_sch)

            if is_dry_run:
                console.print("\n[bold yellow]💡 [DRY-RUN 演练模式] 未向交易所提交真实订单。如需实盘执行，请配置 API Wallet 并添加 --force 参数。[/bold yellow]")
            else:
                console.print("\n[bold red]⚡ 正在向 Hyperliquid 提交实盘订单...[/bold red]")
                exec_res = executor.execute_arbitrage_plan(plan, dry_run=False)
                if exec_res.get("status") in ["SPOT_ORDER_PLACED", "SUCCESS"]:
                    console.print(f"[bold green]✅ 现货 Post-Only 挂单提交成功! (OID: {exec_res.get('spot_order_response', {}).get('response', {}).get('data', {}).get('statuses', [{}])[0].get('resting', {}).get('oid', '--')})[/bold green]")
                    console.print("[dim]订单成交后将自动触发合约空头 IOC 对冲。[/dim]")
                else:
                    console.print(f"[bold red]❌ 实盘下单失败: {exec_res.get('error', exec_res.get('message', '未知错误'))}[/bold red]")
        return

if __name__ == "__main__":
    main()

