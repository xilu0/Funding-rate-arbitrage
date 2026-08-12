#!/usr/bin/env python3
import sys
import time
import argparse
import json
from typing import Optional
from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from src.hyperliquid_client import HyperliquidClient
from src.calculator import FundingRateCalculator

def render_cli_table(data: list, 
                     spot_fee_pct: float, 
                     perp_fee_pct: float, 
                     limit: Optional[int] = None) -> Table:
    """Renders a Rich ASCII table of funding rates and payback times."""
    table = Table(title="🚀 Hyperliquid Spot & Perp Funding Rate Arbitrage Monitor", 
                  title_style="bold cyan", 
                  header_style="bold magenta", 
                  show_lines=True)

    table.add_column("Rank", justify="right", style="dim", no_wrap=True)
    table.add_column("Asset", justify="left", style="bold yellow")
    table.add_column("Spot Pair", justify="left", style="green")
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

        table.add_row(
            str(i),
            item["coin"],
            f"{item['spot_symbol']} ({item['spot_pair']})",
            hr_cell,
            apr_str,
            apy_str,
            spread_str,
            entry_pb,
            rt_pb,
            vol_str
        )

    return table

def run_cli_loop(client: HyperliquidClient, 
                 calculator: FundingRateCalculator, 
                 interval: int, 
                 limit: Optional[int]):
    console = Console()
    console.print(f"[bold green]Starting Hyperliquid Monitor CLI... Refresh interval: {interval}s[/bold green]")
    
    with Live(console=console, refresh_per_second=1) as live:
        while True:
            try:
                perp_univ, perp_ctxs = client.get_perp_market_data()
                spot_toks, spot_univ, spot_ctxs = client.get_spot_market_data()
                
                results = calculator.match_and_calculate(
                    perp_univ, perp_ctxs, spot_toks, spot_univ, spot_ctxs
                )
                
                table = render_cli_table(
                    results, 
                    calculator.spot_taker_fee * 100.0, 
                    calculator.perp_taker_fee * 100.0, 
                    limit
                )
                
                timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                footer_text = Text(f"Last updated: {timestamp} UTC | Monitored Spot+Perp Pairs: {len(results)} | Fees: Spot Taker {calculator.spot_taker_fee*100:.3f}%, Perp Taker {calculator.perp_taker_fee*100:.3f}%", style="dim italic")
                
                panel = Panel(table, subtitle=footer_text)
                live.update(panel)
            except Exception as e:
                console.print(f"[bold red]Error updating data: {e}[/bold red]")
            
            time.sleep(interval)

def main():
    parser = argparse.ArgumentParser(description="Hyperliquid Spot & Perp Funding Rate Arbitrage Monitor")
    parser.add_argument("--cli", action="store_true", help="Run in Terminal CLI mode")
    parser.add_argument("--port", type=int, default=8080, help="Port to run Web UI server (default: 8080)")
    parser.add_argument("--interval", type=int, default=5, help="Refresh interval in seconds (default: 5)")
    parser.add_argument("--spot-fee", type=float, default=0.07, help="Spot Taker Fee percentage (default: 0.07)")
    parser.add_argument("--perp-fee", type=float, default=0.035, help="Perp Taker Fee percentage (default: 0.035)")
    parser.add_argument("--max-spread", type=float, default=100.0, help="Max allowed price spread percentage to filter fake symbol collisions (default: 100)")
    parser.add_argument("--no-alias", action="store_true", help="Disable token alias matching (e.g. UBTC->BTC)")
    parser.add_argument("--limit", type=int, default=None, help="Limit CLI table output rows")
    parser.add_argument("--json", action="store_true", help="Fetch once and output JSON to stdout")

    args = parser.parse_args()

    client = HyperliquidClient()
    calculator = FundingRateCalculator(
        spot_taker_fee=args.spot_fee / 100.0,
        perp_taker_fee=args.perp_fee / 100.0,
        enable_aliases=not args.no_alias,
        max_spread_pct=args.max_spread
    )

    if args.json:
        perp_univ, perp_ctxs = client.get_perp_market_data()
        spot_toks, spot_univ, spot_ctxs = client.get_spot_market_data()
        results = calculator.match_and_calculate(perp_univ, perp_ctxs, spot_toks, spot_univ, spot_ctxs)
        print(json.dumps(results, indent=2))
        return

    if args.cli:
        run_cli_loop(client, calculator, args.interval, args.limit)
    else:
        from server import start_web_server
        start_web_server(args.port, client, calculator, args.interval)

if __name__ == "__main__":
    main()
