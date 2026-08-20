import os
import json
import urllib.parse
from http.server import HTTPServer, SimpleHTTPRequestHandler
from typing import Dict, Any, List

from src.hyperliquid_client import HyperliquidClient
from src.bybit_client import BybitClient
from src.calculator import (
    FundingRateCalculator,
    DEFAULT_HL_SPOT_TAKER_FEE,
    DEFAULT_HL_PERP_TAKER_FEE,
    DEFAULT_BYBIT_SPOT_TAKER_FEE,
    DEFAULT_BYBIT_PERP_TAKER_FEE
)

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

class ArbitrageServerHandler(SimpleHTTPRequestHandler):
    hl_client: HyperliquidClient = None
    bybit_client: BybitClient = None
    calculator: FundingRateCalculator = None
    cache: Dict[str, Any] = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/funding-rates":
            self.handle_api_funding_rates(parsed.query)
        elif path == "/api/bybit/funding-history":
            self.handle_api_bybit_funding_history(parsed.query)
        elif path == "/api/depth-capacity":
            self.handle_api_depth_capacity(parsed.query)
        elif path in ["/api/build-arbitrage", "/api/bybit/build-arbitrage"]:
            self.handle_api_build_arbitrage(parsed.query)
        else:
            # Fallback to serving static files from web/ directory
            super().do_GET()

    def handle_api_build_arbitrage(self, query_str: str):
        params = urllib.parse.parse_qs(query_str)
        exchange = params.get("exchange", [""])[0].strip().lower()
        symbol = params.get("symbol", ["BTCUSDT"])[0].strip()
        spot_symbol = params.get("spot_symbol", [""])[0].strip()
        raw_spot_pair = params.get("raw_spot_pair", [""])[0].strip()
        amount_usd_str = params.get("amount_usd", [""])[0].strip()
        amount_qty_str = params.get("amount_qty", [""])[0].strip()
        dry_run = params.get("dry_run", ["true"])[0].lower() != "false"
        force = params.get("force", ["false"])[0].lower() == "true"
        max_slippage_str = params.get("max_slippage", ["0.50"])[0].strip()
        max_payback_str = params.get("max_payback", ["72.0"])[0].strip()

        try:
            amount_usd = float(amount_usd_str) if amount_usd_str else None
        except ValueError:
            amount_usd = None

        try:
            amount_qty = float(amount_qty_str) if amount_qty_str else None
        except ValueError:
            amount_qty = None

        try:
            max_slippage = float(max_slippage_str) if max_slippage_str else 0.50
        except ValueError:
            max_slippage = 0.50

        try:
            max_payback = float(max_payback_str) if max_payback_str else 72.0
        except ValueError:
            max_payback = 72.0

        if amount_usd is None and amount_qty is None:
            amount_usd = 10000.0

        try:
            from src.bybit_executor import BybitArbitrageExecutor
            from src.calculator import parse_base_multiplier, safe_float

            # Determine exchange if not explicit
            is_hl = exchange == "hyperliquid" or (not exchange and not symbol.endswith("USDT") and not symbol.endswith("USDC"))

            if is_hl:
                if not self.hl_client:
                    self.hl_client = HyperliquidClient()

                calc = self.calculator or FundingRateCalculator(
                    spot_taker_fee=DEFAULT_HL_SPOT_TAKER_FEE,
                    perp_taker_fee=DEFAULT_HL_PERP_TAKER_FEE
                )

                executor = BybitArbitrageExecutor(
                    calculator=calc,
                    default_max_slippage_pct=max_slippage,
                    default_max_payback_hours=max_payback
                )

                perp_univ, perp_ctxs = self.hl_client.get_perp_market_data()
                matched_idx = next((i for i, u in enumerate(perp_univ) if u.get("name") == symbol), None)
                if matched_idx is None:
                    raise ValueError(f"Perpetual contract '{symbol}' not found on Hyperliquid")

                ctx = perp_ctxs[matched_idx]
                hourly_funding = safe_float(ctx.get("funding"))

                if symbol.startswith("k") and len(symbol) > 1 and symbol[1:].isupper():
                    mult = 1000.0
                else:
                    mult, _ = parse_base_multiplier(symbol)

                spot_coin = raw_spot_pair or spot_symbol or symbol
                perp_book = self.hl_client.get_l2_book(symbol)
                spot_book = self.hl_client.get_l2_book(spot_coin)

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

                spot_mid_px = (spot_asks[0][0] + spot_bids[0][0]) / 2.0 if spot_asks and spot_bids else safe_float(ctx.get("midPx"))
                perp_mid_px = (perp_asks[0][0] + perp_bids[0][0]) / 2.0 if perp_asks and perp_bids else spot_mid_px

                ref_px = spot_mid_px if spot_mid_px > 0 else perp_mid_px
                if amount_usd is not None and amount_usd > 0:
                    target_usd = float(amount_usd)
                    spot_qty = target_usd / ref_px
                    perp_contracts_qty = spot_qty / mult
                elif amount_qty is not None and amount_qty > 0:
                    spot_qty = float(amount_qty)
                    perp_contracts_qty = spot_qty / mult
                    target_usd = spot_qty * ref_px
                else:
                    target_usd = 10000.0
                    spot_qty = target_usd / ref_px
                    perp_contracts_qty = spot_qty / mult

                size_info = {
                    "symbol": symbol,
                    "spot_symbol": spot_symbol or symbol,
                    "multiplier": mult,
                    "target_usd": target_usd,
                    "spot_qty": spot_qty,
                    "perp_contracts_qty": perp_contracts_qty,
                    "ref_price": ref_px
                }

                display_spot_sym = spot_symbol if ("/" in spot_symbol or spot_symbol.startswith("@")) else f"{spot_symbol}/USDC"

                try_run_plan = executor.generate_try_run_plan(
                    symbol=f"{symbol}-PERP",
                    spot_symbol=display_spot_sym,
                    multiplier=mult,
                    target_usd=size_info["target_usd"],
                    spot_qty=size_info["spot_qty"],
                    perp_contracts_qty=size_info["perp_contracts_qty"],
                    hourly_funding=hourly_funding,
                    spot_asks=spot_asks,
                    spot_bids=spot_bids,
                    spot_mid_px=spot_mid_px,
                    perp_asks=perp_asks,
                    perp_bids=perp_bids,
                    perp_mid_px=perp_mid_px,
                    max_slippage_pct=max_slippage,
                    max_payback_hours=max_payback,
                    force=force
                )

                payload = {
                    "status": "success",
                    "exchange": "Hyperliquid",
                    "mode": "DRY_RUN" if dry_run else "LIVE",
                    "symbol": symbol,
                    "spot_symbol": display_spot_sym,
                    "size_info": size_info,
                    "try_run_plan": try_run_plan
                }

            else:
                # Bybit
                if not self.bybit_client:
                    self.bybit_client = BybitClient()

                calc = self.calculator or FundingRateCalculator(
                    spot_taker_fee=DEFAULT_BYBIT_SPOT_TAKER_FEE,
                    perp_taker_fee=DEFAULT_BYBIT_PERP_TAKER_FEE
                )

                executor = BybitArbitrageExecutor(
                    calculator=calc,
                    default_max_slippage_pct=max_slippage,
                    default_max_payback_hours=max_payback
                )

                linear_tickers, _ = self.bybit_client.get_linear_market_data()
                perp_ticker = next((t for t in linear_tickers if t.get("symbol") == symbol), {})
                
                funding_rate = safe_float(perp_ticker.get("fundingRate"))
                interval_hr = safe_float(perp_ticker.get("fundingIntervalHour"), default=8.0)
                hourly_funding = funding_rate / (interval_hr if interval_hr > 0 else 8.0)
                perp_price = safe_float(perp_ticker.get("lastPrice"))

                base_coin = symbol[:-4] if symbol.endswith("USDT") or symbol.endswith("USDC") else symbol
                mult, clean_base = parse_base_multiplier(base_coin)
                calc_spot_symbol = spot_symbol or f"{clean_base}USDT"

                spot_tickers, _ = self.bybit_client.get_spot_market_data()
                spot_ticker = next((t for t in spot_tickers if t.get("symbol") == calc_spot_symbol), {})
                spot_price = safe_float(spot_ticker.get("lastPrice")) * mult

                size_info = executor.parse_execution_size(
                    symbol=symbol,
                    amount_usd=amount_usd,
                    amount_qty=amount_qty,
                    spot_price=spot_price,
                    perp_price=perp_price
                )

                spot_book = self.bybit_client.get_orderbook("spot", calc_spot_symbol, limit=200)
                perp_book = self.bybit_client.get_orderbook("linear", symbol, limit=200)

                spot_raw_asks = spot_book.get("a", [])
                spot_raw_bids = spot_book.get("b", [])
                perp_raw_asks = perp_book.get("a", [])
                perp_raw_bids = perp_book.get("b", [])

                spot_asks = [(safe_float(px) * mult, safe_float(sz) / mult) for px, sz in spot_raw_asks]
                spot_bids = [(safe_float(px) * mult, safe_float(sz) / mult) for px, sz in spot_raw_bids]
                perp_asks = [(safe_float(px), safe_float(sz)) for px, sz in perp_raw_asks]
                perp_bids = [(safe_float(px), safe_float(sz)) for px, sz in perp_raw_bids]

                spot_mid_px = (spot_asks[0][0] + spot_bids[0][0]) / 2.0 if spot_asks and spot_bids else spot_price
                perp_mid_px = (perp_asks[0][0] + perp_bids[0][0]) / 2.0 if perp_asks and perp_bids else perp_price

                try_run_plan = executor.generate_try_run_plan(
                    symbol=symbol,
                    spot_symbol=calc_spot_symbol,
                    multiplier=mult,
                    target_usd=size_info["target_usd"],
                    spot_qty=size_info["spot_qty"],
                    perp_contracts_qty=size_info["perp_contracts_qty"],
                    hourly_funding=hourly_funding,
                    spot_asks=spot_asks,
                    spot_bids=spot_bids,
                    spot_mid_px=spot_mid_px,
                    perp_asks=perp_asks,
                    perp_bids=perp_bids,
                    perp_mid_px=perp_mid_px,
                    max_slippage_pct=max_slippage,
                    max_payback_hours=max_payback,
                    force=force
                )

                payload = {
                    "status": "success",
                    "exchange": "Bybit",
                    "mode": "DRY_RUN" if dry_run else "LIVE",
                    "symbol": symbol,
                    "spot_symbol": calc_spot_symbol,
                    "size_info": size_info,
                    "try_run_plan": try_run_plan
                }

            response_bytes = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)
        except Exception as e:
            err_payload = {"status": "error", "message": str(e)}
            err_bytes = json.dumps(err_payload).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(err_bytes)


    def handle_api_depth_capacity(self, query_str: str):
        params = urllib.parse.parse_qs(query_str)
        exchange = params.get("exchange", ["bybit"])[0].lower()
        symbol = params.get("symbol", ["BTCUSDT"])[0].strip()
        spot_symbol = params.get("spot_symbol", [""])[0].strip()
        target_usd_str = params.get("target_usd", [""])[0].strip()
        
        custom_target_usd = None
        if target_usd_str:
            try:
                custom_target_usd = float(target_usd_str)
            except ValueError:
                pass

        default_spot_fee = 0.10 if exchange == "bybit" else 0.07
        default_perp_fee = 0.055 if exchange == "bybit" else 0.035
        spot_fee_pct = float(params.get("spot_fee", [default_spot_fee])[0])
        perp_fee_pct = float(params.get("perp_fee", [default_perp_fee])[0])

        calc = FundingRateCalculator(
            spot_taker_fee=spot_fee_pct / 100.0,
            perp_taker_fee=perp_fee_pct / 100.0
        )

        try:
            from src.calculator import parse_base_multiplier, safe_float

            if exchange in ["bybit"]:
                if not self.bybit_client:
                    self.bybit_client = BybitClient()

                linear_tickers, _ = self.bybit_client.get_linear_market_data()
                perp_ticker = next((t for t in linear_tickers if t.get("symbol") == symbol), {})
                
                funding_rate = safe_float(perp_ticker.get("fundingRate"))
                interval_hr = safe_float(perp_ticker.get("fundingIntervalHour"), default=8.0)
                hourly_funding = funding_rate / (interval_hr if interval_hr > 0 else 8.0)

                if not spot_symbol:
                    base_coin = symbol[:-4] if symbol.endswith("USDT") or symbol.endswith("USDC") else symbol
                    mult, clean_base = parse_base_multiplier(base_coin)
                    spot_symbol = f"{clean_base}USDT"
                else:
                    mult, clean_base = parse_base_multiplier(symbol[:-4] if symbol.endswith("USDT") else symbol)

                spot_book = self.bybit_client.get_orderbook("spot", spot_symbol, limit=200)
                perp_book = self.bybit_client.get_orderbook("linear", symbol, limit=200)

                spot_raw_asks = spot_book.get("a", [])
                spot_raw_bids = spot_book.get("b", [])
                perp_raw_asks = perp_book.get("a", [])
                perp_raw_bids = perp_book.get("b", [])

                spot_asks = [(safe_float(px) * mult, safe_float(sz) / mult) for px, sz in spot_raw_asks]
                spot_bids = [(safe_float(px) * mult, safe_float(sz) / mult) for px, sz in spot_raw_bids]
                perp_asks = [(safe_float(px), safe_float(sz)) for px, sz in perp_raw_asks]
                perp_bids = [(safe_float(px), safe_float(sz)) for px, sz in perp_raw_bids]

                spot_mid_px = (spot_asks[0][0] + spot_bids[0][0]) / 2.0 if spot_asks and spot_bids else safe_float(perp_ticker.get("lastPrice"))
                perp_mid_px = (perp_asks[0][0] + perp_bids[0][0]) / 2.0 if perp_asks and perp_bids else safe_float(perp_ticker.get("lastPrice"))

            else:
                # Hyperliquid
                if not self.hl_client:
                    self.hl_client = HyperliquidClient()

                perp_univ, perp_ctxs = self.hl_client.get_perp_market_data()
                matched_idx = next((i for i, u in enumerate(perp_univ) if u.get("name") == symbol), 0)
                ctx = perp_ctxs[matched_idx] if matched_idx < len(perp_ctxs) else {}
                hourly_funding = safe_float(ctx.get("funding"))

                perp_book = self.hl_client.get_l2_book(symbol)
                perp_levels = perp_book.get("levels", [[], []])
                perp_bids_raw = perp_levels[0] if len(perp_levels) > 0 else []
                perp_asks_raw = perp_levels[1] if len(perp_levels) > 1 else []

                spot_coin = spot_symbol or symbol
                spot_book = self.hl_client.get_l2_book(spot_coin)
                spot_levels = spot_book.get("levels", [[], []])
                spot_bids_raw = spot_levels[0] if len(spot_levels) > 0 and len(spot_levels[0]) > 0 else perp_bids_raw
                spot_asks_raw = spot_levels[1] if len(spot_levels) > 1 and len(spot_levels[1]) > 0 else perp_asks_raw

                perp_asks = [(safe_float(item.get("px")), safe_float(item.get("sz"))) for item in perp_asks_raw]
                perp_bids = [(safe_float(item.get("px")), safe_float(item.get("sz"))) for item in perp_bids_raw]
                spot_asks = [(safe_float(item.get("px")), safe_float(item.get("sz"))) for item in spot_asks_raw]
                spot_bids = [(safe_float(item.get("px")), safe_float(item.get("sz"))) for item in spot_bids_raw]

                spot_mid_px = (spot_asks[0][0] + spot_bids[0][0]) / 2.0 if spot_asks and spot_bids else safe_float(ctx.get("midPx"))
                perp_mid_px = (perp_asks[0][0] + perp_bids[0][0]) / 2.0 if perp_asks and perp_bids else spot_mid_px

            capacity_eval = calc.evaluate_capital_capacity(
                spot_asks=spot_asks,
                spot_bids=spot_bids,
                spot_mid_px=spot_mid_px,
                perp_asks=perp_asks,
                perp_bids=perp_bids,
                perp_mid_px=perp_mid_px,
                hourly_funding=hourly_funding,
                custom_target_usd=custom_target_usd
            )

            payload = {
                "status": "success",
                "exchange": exchange,
                "symbol": symbol,
                "spot_symbol": spot_symbol or symbol,
                "data": capacity_eval
            }

            response_bytes = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)
        except Exception as e:
            err_payload = {"status": "error", "message": str(e)}
            err_bytes = json.dumps(err_payload).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(err_bytes)


    def handle_api_bybit_funding_history(self, query_str: str):
        params = urllib.parse.parse_qs(query_str)
        symbol = params.get("symbol", ["BTCUSDT"])[0].strip()
        days_str = params.get("days", ["30"])[0].strip()
        limit_str = params.get("limit", ["200"])[0].strip()

        try:
            days = int(days_str) if days_str else 30
        except ValueError:
            days = 30

        try:
            limit = int(limit_str) if limit_str else 200
        except ValueError:
            limit = 200

        try:
            if not self.bybit_client:
                self.bybit_client = BybitClient()

            raw_history = self.bybit_client.get_funding_rate_history(symbol, limit=limit)
            result_payload = FundingRateCalculator.calculate_funding_history_stats(raw_history, days=days)

            payload = {
                "status": "success",
                "symbol": symbol,
                "data": result_payload
            }

            response_bytes = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)
        except Exception as e:
            err_payload = {"status": "error", "message": str(e)}
            err_bytes = json.dumps(err_payload).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(err_bytes)


    def handle_api_funding_rates(self, query_str: str):
        params = urllib.parse.parse_qs(query_str)
        
        exchange = params.get("exchange", ["all"])[0].lower()
        
        # Default fee logic per exchange if not explicitly passed
        default_spot_fee = 0.10 if exchange == "bybit" else 0.07
        default_perp_fee = 0.055 if exchange == "bybit" else 0.035

        spot_fee_pct = float(params.get("spot_fee", [default_spot_fee])[0])
        perp_fee_pct = float(params.get("perp_fee", [default_perp_fee])[0])
        enable_aliases = params.get("enable_aliases", ["true"])[0].lower() == "true"
        max_spread_pct = float(params.get("max_spread", [10.0])[0])

        # Initialize calculator with requested fees
        calc = FundingRateCalculator(
            spot_taker_fee=spot_fee_pct / 100.0,
            perp_taker_fee=perp_fee_pct / 100.0,
            enable_aliases=enable_aliases,
            max_spread_pct=max_spread_pct
        )

        try:
            results: List[Dict[str, Any]] = []

            # 1. Hyperliquid
            if exchange in ["hyperliquid", "all", "hl"]:
                if self.hl_client:
                    perp_univ, perp_ctxs = self.hl_client.get_perp_market_data()
                    spot_toks, spot_univ, spot_ctxs = self.hl_client.get_spot_market_data()
                    hl_results = calc.match_and_calculate(
                        perp_univ, perp_ctxs, spot_toks, spot_univ, spot_ctxs
                    )
                    results.extend(hl_results)

            # 2. Bybit
            if exchange in ["bybit", "all"]:
                if self.bybit_client:
                    linear_tickers, linear_insts = self.bybit_client.get_linear_market_data()
                    spot_tickers, spot_insts = self.bybit_client.get_spot_market_data()
                    bybit_results = calc.match_and_calculate_bybit(
                        linear_tickers, spot_tickers, linear_insts, spot_insts
                    )
                    results.extend(bybit_results)

            # Sort combined results descending by hourly funding rate
            results.sort(key=lambda x: x["hourly_funding"], reverse=True)

            payload = {
                "status": "success",
                "exchange": exchange,
                "count": len(results),
                "params": {
                    "exchange": exchange,
                    "spot_fee_pct": spot_fee_pct,
                    "perp_fee_pct": perp_fee_pct,
                    "entry_fee_pct": spot_fee_pct + perp_fee_pct,
                    "roundtrip_fee_pct": (spot_fee_pct + perp_fee_pct) * 2.0,
                    "enable_aliases": enable_aliases
                },
                "data": results
            }

            response_bytes = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)
        except Exception as e:
            err_payload = {"status": "error", "message": str(e)}
            err_bytes = json.dumps(err_payload).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(err_bytes)

def start_web_server(port: int, 
                     hl_client: HyperliquidClient, 
                     bybit_client: BybitClient,
                     calculator: FundingRateCalculator, 
                     interval: int = 30):
    ArbitrageServerHandler.hl_client = hl_client
    ArbitrageServerHandler.bybit_client = bybit_client
    ArbitrageServerHandler.calculator = calculator

    server = HTTPServer(("0.0.0.0", port), ArbitrageServerHandler)
    print(f"===========================================================")
    print(f"🔥 Capital Funding Rate Arbitrage Monitor Web Dashboard Live!")
    print(f"👉 Access UI at: http://localhost:{port}")
    print(f"===========================================================")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down web server...")
        server.server_close()

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Capital Spot & Perp Funding Rate Arbitrage Web Server")
    parser.add_argument("--port", type=int, default=8000, help="Web server port (default: 8000)")
    parser.add_argument("--interval", type=int, default=30, help="Refresh interval (default: 30)")
    parser.add_argument("--spot-fee", type=float, default=None, help="Spot taker fee percentage (e.g. 0.07)")
    parser.add_argument("--perp-fee", type=float, default=None, help="Perp taker fee percentage (e.g. 0.035)")
    args = parser.parse_args()

    hl_client = HyperliquidClient()
    bybit_client = BybitClient()

    spot_fee = args.spot_fee / 100.0 if args.spot_fee is not None else DEFAULT_HL_SPOT_TAKER_FEE
    perp_fee = args.perp_fee / 100.0 if args.perp_fee is not None else DEFAULT_HL_PERP_TAKER_FEE
    calculator = FundingRateCalculator(
        spot_taker_fee=spot_fee,
        perp_taker_fee=perp_fee
    )
    start_web_server(args.port, hl_client, bybit_client, calculator, args.interval)

if __name__ == "__main__":
    main()

