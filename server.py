import os
import json
import urllib.parse
from http.server import HTTPServer, SimpleHTTPRequestHandler
from typing import Dict, Any

from src.hyperliquid_client import HyperliquidClient
from src.calculator import FundingRateCalculator

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

class ArbitrageServerHandler(SimpleHTTPRequestHandler):
    client: HyperliquidClient = None
    calculator: FundingRateCalculator = None
    cache: Dict[str, Any] = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/funding-rates":
            self.handle_api_funding_rates(parsed.query)
        else:
            # Fallback to serving static files from web/ directory
            super().do_GET()

    def handle_api_funding_rates(self, query_str: str):
        params = urllib.parse.parse_qs(query_str)
        
        spot_fee_pct = float(params.get("spot_fee", [0.07])[0])
        perp_fee_pct = float(params.get("perp_fee", [0.035])[0])
        enable_aliases = params.get("enable_aliases", ["true"])[0].lower() == "true"
        max_spread_pct = float(params.get("max_spread", [100.0])[0])

        # Initialize calculator with requested fees
        calc = FundingRateCalculator(
            spot_taker_fee=spot_fee_pct / 100.0,
            perp_taker_fee=perp_fee_pct / 100.0,
            enable_aliases=enable_aliases,
            max_spread_pct=max_spread_pct
        )

        try:
            perp_univ, perp_ctxs = self.client.get_perp_market_data()
            spot_toks, spot_univ, spot_ctxs = self.client.get_spot_market_data()

            results = calc.match_and_calculate(
                perp_univ, perp_ctxs, spot_toks, spot_univ, spot_ctxs
            )

            payload = {
                "status": "success",
                "count": len(results),
                "params": {
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
                     client: HyperliquidClient, 
                     calculator: FundingRateCalculator, 
                     interval: int):
    ArbitrageServerHandler.client = client
    ArbitrageServerHandler.calculator = calculator

    server = HTTPServer(("0.0.0.0", port), ArbitrageServerHandler)
    print(f"===========================================================")
    print(f"🔥 Hyperliquid Funding Rate Monitor Web Dashboard Live!")
    print(f"👉 Access UI at: http://localhost:{port}")
    print(f"===========================================================")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down web server...")
        server.server_close()
