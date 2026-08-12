import math
from typing import Dict, List, Any, Optional, Tuple

DEFAULT_SPOT_TAKER_FEE = 0.0007  # 0.07%
DEFAULT_PERP_TAKER_FEE = 0.00035 # 0.035%

COMMON_PREFIX_ALIASES = {
    'UBTC': 'BTC',
    'UETH': 'ETH',
    'USOL': 'SOL',
    'UDOGE': 'DOGE',
    'UAVAX': 'AVAX',
    'UENA': 'ENA',
}

class FundingRateCalculator:
    """Calculates APR, APY, fee payback times, and matches spot & perp markets."""

    def __init__(self, 
                 spot_taker_fee: float = DEFAULT_SPOT_TAKER_FEE, 
                 perp_taker_fee: float = DEFAULT_PERP_TAKER_FEE,
                 enable_aliases: bool = True,
                 max_spread_pct: float = 100.0):
        self.spot_taker_fee = spot_taker_fee
        self.perp_taker_fee = perp_taker_fee
        self.enable_aliases = enable_aliases
        self.max_spread_pct = max_spread_pct

    @property
    def entry_fee_rate(self) -> float:
        """Single-side entry market order fee rate (Spot Taker + Perp Taker)."""
        return self.spot_taker_fee + self.perp_taker_fee

    @property
    def roundtrip_fee_rate(self) -> float:
        """Full round-trip market order fee rate (Entry + Exit)."""
        return self.entry_fee_rate * 2.0

    def calculate_payback_hours(self, fee_rate: float, hourly_rate: float) -> Optional[float]:
        """
        Calculates time in hours for funding rate earnings to cover trading fees.
        Returns None if hourly funding rate is <= 0.
        """
        if hourly_rate <= 0:
            return None
        return fee_rate / hourly_rate

    @staticmethod
    def format_hours(hours: Optional[float]) -> str:
        """Formats hours into a human-readable string (e.g. '5.2h', '3.1d', 'N/A')."""
        if hours is None or math.isinf(hours) or math.isnan(hours):
            return "N/A"
        if hours < 1:
            mins = hours * 60
            return f"{mins:.1f} min"
        elif hours < 48:
            return f"{hours:.1f} hrs"
        else:
            days = hours / 24.0
            return f"{days:.1f} days"

    def match_and_calculate(self, 
                            perp_universe: list, 
                            perp_ctxs: list, 
                            spot_tokens: list, 
                            spot_universe: list, 
                            spot_ctxs: list) -> List[Dict[str, Any]]:
        """
        Matches spot tokens with perp coins and computes arbitrage metrics.
        """
        # Map perp coins
        perp_data = {}
        for i, u in enumerate(perp_universe):
            coin_name = u["name"]
            ctx = perp_ctxs[i] if i < len(perp_ctxs) else {}
            perp_data[coin_name] = {
                "coin": coin_name,
                "funding": float(ctx.get("funding", 0.0)),
                "mark_px": float(ctx.get("markPx", 0.0)),
                "mid_px": float(ctx.get("midPx", 0.0) or ctx.get("markPx", 0.0)),
                "oracle_px": float(ctx.get("oraclePx", 0.0)),
                "open_interest": float(ctx.get("openInterest", 0.0)),
                "day_ntl_vlm": float(ctx.get("dayNtlVlm", 0.0)),
            }

        # Token index to token object
        token_by_idx = {t["index"]: t for t in spot_tokens}

        # Map spot pairs (focus on canonical / USDC / USDT quote pairs)
        spot_pairs_by_base = {}
        for i, pair in enumerate(spot_universe):
            ctx = spot_ctxs[i] if i < len(spot_ctxs) else {}
            base_idx, quote_idx = pair["tokens"][0], pair["tokens"][1]
            base_token = token_by_idx.get(base_idx, {})
            quote_token = token_by_idx.get(quote_idx, {})

            base_symbol = base_token.get("name", "")
            quote_symbol = quote_token.get("name", "")
            
            # Prefer USDC or USDT quotes
            mid_px = float(ctx.get("midPx", 0.0) or ctx.get("markPx", 0.0) or 0.0)
            day_ntl_vlm = float(ctx.get("dayNtlVlm", 0.0) or 0.0)

            raw_pair_name = pair["name"]
            if raw_pair_name.startswith("@"):
                spot_pair_name = f"{base_symbol}/{quote_symbol}"
            else:
                spot_pair_name = raw_pair_name

            pair_info = {
                "spot_pair_name": spot_pair_name,
                "raw_pair_name": raw_pair_name,
                "base_symbol": base_symbol,
                "quote_symbol": quote_symbol,
                "mid_px": mid_px,
                "mark_px": float(ctx.get("markPx", 0.0) or mid_px),
                "day_ntl_vlm": day_ntl_vlm,
                "is_canonical": pair.get("isCanonical", False)
            }

            # If base symbol not yet mapped or current pair has higher volume/is canonical
            if base_symbol not in spot_pairs_by_base or (pair_info["is_canonical"] and quote_symbol in ["USDC", "USDT"]):
                spot_pairs_by_base[base_symbol] = pair_info

        # Match Spot and Perp
        results = []

        for base_symbol, spot_info in spot_pairs_by_base.items():
            matched_perp_coin = None

            # 1. Exact match
            if base_symbol in perp_data:
                matched_perp_coin = base_symbol
            # 2. Alias match
            elif self.enable_aliases:
                if base_symbol in COMMON_PREFIX_ALIASES and COMMON_PREFIX_ALIASES[base_symbol] in perp_data:
                    matched_perp_coin = COMMON_PREFIX_ALIASES[base_symbol]
                elif base_symbol.startswith("k") and base_symbol[1:] in perp_data:
                    matched_perp_coin = base_symbol[1:]

            if not matched_perp_coin:
                continue

            perp_info = perp_data[matched_perp_coin]

            hourly_funding = perp_info["funding"]
            # Annualized Simple APR (%)
            apr_pct = hourly_funding * 24 * 365 * 100.0
            # Compound APY (%)
            try:
                apy_pct = ((1.0 + hourly_funding) ** (24 * 365) - 1.0) * 100.0
            except OverflowError:
                apy_pct = float('inf')

            spot_px = spot_info["mid_px"]
            perp_px = perp_info["mid_px"]
            
            # Basis spread %
            spread_pct = ((perp_px - spot_px) / spot_px * 100.0) if spot_px > 0 else 0.0

            # Filter out fake symbol collisions if max_spread_pct is set (>0)
            if self.max_spread_pct > 0 and abs(spread_pct) > self.max_spread_pct:
                continue

            # Fee Payback Hours
            entry_payback_hrs = self.calculate_payback_hours(self.entry_fee_rate, hourly_funding)
            roundtrip_payback_hrs = self.calculate_payback_hours(self.roundtrip_fee_rate, hourly_funding)

            results.append({
                "coin": matched_perp_coin,
                "spot_symbol": base_symbol,
                "spot_pair": spot_info["spot_pair_name"],
                "hourly_funding": hourly_funding,
                "hourly_funding_pct": hourly_funding * 100.0,
                "apr_pct": apr_pct,
                "apy_pct": apy_pct,
                "spot_price": spot_px,
                "perp_price": perp_px,
                "spread_pct": spread_pct,
                "entry_payback_hrs": entry_payback_hrs,
                "entry_payback_str": self.format_hours(entry_payback_hrs),
                "roundtrip_payback_hrs": roundtrip_payback_hrs,
                "roundtrip_payback_str": self.format_hours(roundtrip_payback_hrs),
                "perp_24h_volume": perp_info["day_ntl_vlm"],
                "spot_24h_volume": spot_info["day_ntl_vlm"],
                "open_interest": perp_info["open_interest"],
            })

        # Sort descending by hourly funding / APR
        results.sort(key=lambda x: x["hourly_funding"], reverse=True)
        return results
