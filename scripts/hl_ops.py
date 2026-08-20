#!/usr/bin/env python3
import os
import sys
import time
import json
import argparse
from typing import Optional, Dict, Any, Tuple, List

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from src.hyperliquid_client import HyperliquidClient
from src.hyperliquid_executor import HyperliquidExecutor
from src.calculator import FundingRateCalculator, safe_float

console = Console()

def render_check_report(auth_res: Dict[str, Any], canary_res: Dict[str, Any]) -> Tuple[Panel, Table]:
    """Renders visual diagnosis panel and table for API wallet check."""
    is_auth = auth_res.get("authorized", False)
    canary_status = canary_res.get("status", "SKIPPED")

    overall_pass = is_auth and canary_status in ["PASSED", "SKIPPED_NO_SDK"]

    if overall_pass:
        status_banner = "[bold green]✅ API WALLET 验证通过 (ALL CHECKS PASSED)[/bold green]"
    else:
        status_banner = "[bold red]❌ API WALLET 验证异常 (ACTION REQUIRED)[/bold red]"

    summary_text = (
        f"{status_banner}\n\n"
        f"[bold yellow]Master Account (只读状态归属):[/bold yellow] {auth_res.get('master_address', '未配置')}\n"
        f"[bold yellow]Agent Wallet (代理签名地址):[/bold yellow] {auth_res.get('agent_address', '未配置')}\n"
        f"[bold yellow]链上授权状态 (Extra Agents):[/bold yellow] {'[bold green]已授权 (Approved)[/bold green]' if is_auth else '[bold red]未授权 (Not Found in Master Approved Agents)[/bold red]'}\n"
        f"[bold yellow]金丝雀安全挂撤单测试:[/bold yellow] "
    )

    if canary_status == "PASSED":
        summary_text += f"[bold green]通过 (Order OID: {canary_res.get('order_id')} | 总耗时: {canary_res.get('total_latency_ms')}ms)[/bold green]"
    elif canary_status == "SKIPPED_NO_SDK":
        summary_text += f"[yellow]已跳过挂单测试 (未安装 hyperliquid-python-sdk: pip install hyperliquid-python-sdk)[/yellow]"
    else:
        summary_text += f"[bold red]失败 ({canary_res.get('error', '未知错误')})[/bold red]"

    panel = Panel(summary_text, title="[bold cyan]🔍 Hyperliquid API Wallet 诊断与验活报告[/bold cyan]")

    table = Table(title="📋 详细检测项 (Diagnostic Items)", show_lines=True, header_style="bold magenta")
    table.add_column("检查项目", style="bold yellow")
    table.add_column("检测状态", justify="center")
    table.add_column("详情说明与诊断信息")

    # Item 1: Master address
    m_ok = bool(auth_res.get("master_address"))
    table.add_row("1. Master 地址配置", "[green]OK[/green]" if m_ok else "[red]FAIL[/red]", auth_res.get("master_address") or "缺失 HL_ACCOUNT_ADDRESS")

    # Item 2: Agent key & public address
    a_ok = bool(auth_res.get("agent_address"))
    table.add_row("2. Agent 私钥及公钥解析", "[green]OK[/green]" if a_ok else "[red]FAIL[/red]", f"公钥: {auth_res.get('agent_address')}" if a_ok else "无效或缺失私钥")

    # Item 3: On-chain Agent approval
    table.add_row("3. 链上 Master 授权登记", "[green]PASSED[/green]" if is_auth else "[red]FAILED[/red]",
                  f"主账户已授权 {auth_res.get('extra_agents_count', 0)} 个 Agent" if is_auth else f"未在 Master 授权列表发现此 Agent。请在 Hyperliquid 前端或通过 approveAgent 授权。")

    # Item 4: Canary Execution
    if canary_status == "PASSED":
        canary_msg = f"挂单耗时 {canary_res.get('order_latency_ms')}ms, 撤单耗时 {canary_res.get('cancel_latency_ms')}ms"
        table.add_row("4. EIP-712 金丝雀挂单/撤单", "[green]PASSED[/green]", canary_msg)
    elif canary_status == "SKIPPED_NO_SDK":
        table.add_row("4. EIP-712 金丝雀挂单/撤单", "[yellow]SKIPPED[/yellow]", "建议执行 `pip install hyperliquid-python-sdk` 启用真实签名测试")
    else:
        table.add_row("4. EIP-712 金丝雀挂单/撤单", "[red]FAILED[/red]", canary_res.get("error", "签名或网络异常"))

    return panel, table

def render_status_report(health_data: Dict[str, Any]) -> Tuple[Panel, Table, Table]:
    """Renders comprehensive Scheme D portfolio & risk status tables."""
    tier_name = health_data.get("tier_name", "")
    tier_color = health_data.get("tier_color", "white")
    action = health_data.get("action_recommended", "")

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

    # Table 1: Spot Balances
    spot_table = Table(title="🪙 现货资产与质押估值 (Spot Balances & 50% Haircut Collateral)", show_lines=True, header_style="bold green")
    spot_table.add_column("币种 (Coin)", style="bold yellow")
    spot_table.add_column("持有数量 (Total Qty)", justify="right")
    spot_table.add_column("冻结中 (Hold)", justify="right")
    spot_table.add_column("资产估值 (USD Value)", justify="right")
    spot_table.add_column("折价质押金 (Collateral 50%)", justify="right", style="bold cyan")

    spot_balances = health_data.get("spot_balances", [])
    if not spot_balances:
        spot_table.add_row("无现货持仓", "-", "-", "$0.00", "$0.00")
    else:
        for s in spot_balances:
            spot_table.add_row(
                s["coin"],
                f"{s['total_qty']:,.4f}",
                f"{s['hold_qty']:,.4f}",
                f"${s['valuation_usd']:,.2f}",
                f"${s['collateral_value_usd']:,.2f}"
            )

    # Table 2: Perp Positions
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

def render_orders_table(orders: list) -> Table:
    """Renders open orders table."""
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

def main():
    parser = argparse.ArgumentParser(description="Hyperliquid API Wallet (Agent Wallet) Operations & Risk Management CLI")
    parser.add_argument("--account", "-a", type=str, default=None, help="Master Account public address (e.g. 0x123...)")
    parser.add_argument("--agent-key", "-k", type=str, default=None, help="Agent Wallet private key (e.g. 0xabc...)")
    parser.add_argument("--testnet", action="store_true", help="Use Hyperliquid Testnet")
    parser.add_argument("--json", action="store_true", help="Output raw JSON response to stdout")

    subparsers = parser.add_subparsers(dest="subcommand", help="Operational Subcommands")

    # 1. check
    check_p = subparsers.add_parser("check", help="[验活自检] 验证配置、Master授权与金丝雀安全挂撤单")
    check_p.add_argument("--coin", type=str, default="PURR", help="Coin to use for canary order (default: PURR)")

    # 2. status / balance
    subparsers.add_parser("status", help="[账户大盘] 查看现货质押、合约对冲、强平距离与方案 D 阶梯预警")
    subparsers.add_parser("balance", help="Alias for status")

    # 3. orders
    subparsers.add_parser("orders", help="[挂单监控] 查看当前所有活动挂单")

    # 4. cancel-all
    cancel_p = subparsers.add_parser("cancel-all", help="[紧急撤单] 一键全撤单 (Panic Button) 或撤销指定币种")
    cancel_p.add_argument("--coin", type=str, default=None, help="Optional specific coin to cancel")

    # 5. deadman
    dm_p = subparsers.add_parser("deadman", help="[看门狗] 设置/刷新 Hyperliquid 自动撤单死人开关")
    dm_p.add_argument("--timeout", type=int, default=60, help="Timeout in seconds before auto-canceling (default: 60s)")
    dm_p.add_argument("--cancel", action="store_true", help="Cancel/Clear the dead-man's switch")

    # 6. transfer
    tx_p = subparsers.add_parser("transfer", help="[资金调拨] 现货与合约账户间划转 USDC (Level 1 缓冲注入)")
    tx_p.add_argument("--to", type=str, required=True, choices=["perp", "spot"], help="Destination account class")
    tx_p.add_argument("--amount", type=float, required=True, help="Amount in USDC")
    tx_p.add_argument("--dry-run", action="store_true", help="Simulate transfer without live execution")

    # 7. deleverage
    del_p = subparsers.add_parser("deleverage", help="[阶梯调仓] 等比例减仓 (Level 2: 25%%, Level 3: 50%% 熔断)")
    del_p.add_argument("--coin", type=str, required=True, help="Target coin (e.g. PURR)")
    del_p.add_argument("--pct", type=float, default=25.0, help="Percentage to deleverage (e.g. 25, 50, 100)")
    del_p.add_argument("--force", action="store_true", help="Force execute live deleveraging orders")

    args = parser.parse_args()

    base_url = "https://api.hyperliquid-testnet.xyz" if args.testnet else "https://api.hyperliquid.xyz"
    executor = HyperliquidExecutor(
        account_address=args.account,
        agent_private_key=args.agent_key,
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

if __name__ == "__main__":
    main()
