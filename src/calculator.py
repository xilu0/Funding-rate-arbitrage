import math
from typing import Dict, List, Any, Optional, Tuple

DEFAULT_HL_SPOT_TAKER_FEE = 0.0007   # 0.07%
DEFAULT_HL_PERP_TAKER_FEE = 0.00035  # 0.035%

DEFAULT_BYBIT_SPOT_TAKER_FEE = 0.0010  # 0.10%
DEFAULT_BYBIT_PERP_TAKER_FEE = 0.00055 # 0.055%

# Backward compatibility defaults
DEFAULT_SPOT_TAKER_FEE = DEFAULT_HL_SPOT_TAKER_FEE
DEFAULT_PERP_TAKER_FEE = DEFAULT_HL_PERP_TAKER_FEE

COMMON_PREFIX_ALIASES = {
    'UBTC': 'BTC',
    'UETH': 'ETH',
    'USOL': 'SOL',
    'UDOGE': 'DOGE',
    'UAVAX': 'AVAX',
    'UENA': 'ENA',
}

def safe_float(val: Any, default: float = 0.0) -> float:
    """Safely converts value to float, returning default if None, empty string, or invalid."""
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default

def parse_base_multiplier(base_coin: str) -> Tuple[float, str]:
    """
    Parses prefix multiplier for coins like '1000PEPE' -> (1000.0, 'PEPE').
    Returns (1.0, base_coin) for normal coins (including '0G', '1INCH').
    """
    if not base_coin:
        return 1.0, base_coin
    
    digits = ""
    for char in base_coin:
        if char.isdigit():
            digits += char
        else:
            break
    
    if digits and len(digits) < len(base_coin):
        try:
            mult = float(digits)
            if mult > 1.0:
                clean = base_coin[len(digits):]
                return mult, clean
        except ValueError:
            pass

    return 1.0, base_coin


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
        Matches Hyperliquid spot tokens with perp coins and computes arbitrage metrics.
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

        # Build spot_ctx lookup map if 'coin' is present
        spot_ctx_by_coin = {}
        for c in spot_ctxs:
            if isinstance(c, dict) and "coin" in c:
                spot_ctx_by_coin[c["coin"]] = c

        # Map spot pairs (focus on canonical / USDC / USDT quote pairs)
        spot_pairs_by_base = {}
        for i, pair in enumerate(spot_universe):
            pair_name = pair.get("name", "")
            if pair_name in spot_ctx_by_coin:
                ctx = spot_ctx_by_coin[pair_name]
            elif i < len(spot_ctxs):
                ctx = spot_ctxs[i]
            else:
                ctx = {}

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

            # If base symbol not yet mapped or current pair has higher volume/better quote
            existing = spot_pairs_by_base.get(base_symbol)
            if not existing:
                spot_pairs_by_base[base_symbol] = pair_info
            else:
                if quote_symbol == "USDC" and existing["quote_symbol"] != "USDC":
                    spot_pairs_by_base[base_symbol] = pair_info
                elif quote_symbol == existing["quote_symbol"] and day_ntl_vlm > existing["day_ntl_vlm"]:
                    spot_pairs_by_base[base_symbol] = pair_info
                elif existing["quote_symbol"] not in ["USDC", "USDT0", "USDT"] and quote_symbol in ["USDC", "USDT0", "USDT"]:
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
                "exchange": "Hyperliquid",
                "coin": matched_perp_coin,
                "spot_symbol": base_symbol,
                "spot_pair": spot_info["spot_pair_name"],
                "raw_spot_pair": spot_info.get("raw_pair_name", spot_info["spot_pair_name"]),
                "hourly_funding": hourly_funding,
                "hourly_funding_pct": hourly_funding * 100.0,
                "funding_interval_hr": 1.0,
                "period_funding_pct": hourly_funding * 100.0,
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

    def match_and_calculate_bybit(self,
                                  linear_tickers: list,
                                  spot_tickers: list,
                                  linear_instruments: Optional[list] = None,
                                  spot_instruments: Optional[list] = None) -> List[Dict[str, Any]]:
        """
        Matches Bybit spot tickers with linear perp tickers and computes arbitrage metrics.
        """
        spot_ticker_by_sym = {s["symbol"]: s for s in spot_tickers}
        spot_inst_by_sym = {s["symbol"]: s for s in (spot_instruments or [])}
        
        # Map baseCoin to list of spot symbols
        spot_syms_by_base = {}
        for s_sym, s_inst in spot_inst_by_sym.items():
            base = s_inst.get("baseCoin", "")
            if base:
                spot_syms_by_base.setdefault(base, []).append(s_sym)

        linear_inst_by_sym = {l["symbol"]: l for l in (linear_instruments or [])}

        results = []

        for perp in linear_tickers:
            perp_sym = perp.get("symbol", "")
            linear_meta = linear_inst_by_sym.get(perp_sym, {})

            base_coin = linear_meta.get("baseCoin", "")
            # Fallback if baseCoin not in instruments: strip quote coins
            if not base_coin:
                if perp_sym.endswith("USDT"):
                    base_coin = perp_sym[:-4]
                elif perp_sym.endswith("USDC"):
                    base_coin = perp_sym[:-4]
                elif perp_sym.endswith("PERP"):
                    base_coin = perp_sym[:-4]
                else:
                    base_coin = perp_sym

            multiplier, clean_base = parse_base_multiplier(base_coin)

            matched_spot_sym = None

            # 1. Exact symbol match (e.g. BTCUSDT perp <-> BTCUSDT spot)
            if perp_sym in spot_ticker_by_sym:
                matched_spot_sym = perp_sym
            # 2. Clean base + USDT/USDC match
            elif f"{clean_base}USDT" in spot_ticker_by_sym:
                matched_spot_sym = f"{clean_base}USDT"
            elif f"{clean_base}USDC" in spot_ticker_by_sym:
                matched_spot_sym = f"{clean_base}USDC"
            # 3. BaseCoin lookup match
            elif clean_base in spot_syms_by_base:
                for s_candidate in spot_syms_by_base[clean_base]:
                    if s_candidate in spot_ticker_by_sym:
                        matched_spot_sym = s_candidate
                        break

            if not matched_spot_sym:
                continue

            spot_ticker = spot_ticker_by_sym[matched_spot_sym]
            raw_spot_px = safe_float(spot_ticker.get("lastPrice"))
            if raw_spot_px <= 0:
                continue

            spot_px = raw_spot_px * multiplier
            perp_px = safe_float(perp.get("lastPrice") or perp.get("markPrice"))

            period_funding_rate = safe_float(perp.get("fundingRate"))
            interval_hr = safe_float(perp.get("fundingIntervalHour"), default=8.0)
            if interval_hr <= 0:
                interval_hr = 8.0

            hourly_funding = period_funding_rate / interval_hr
            hourly_funding_pct = hourly_funding * 100.0

            apr_pct = hourly_funding * 24 * 365 * 100.0
            try:
                apy_pct = ((1.0 + hourly_funding) ** (24 * 365) - 1.0) * 100.0
            except OverflowError:
                apy_pct = float('inf')

            spread_pct = ((perp_px - spot_px) / spot_px * 100.0) if spot_px > 0 else 0.0

            if self.max_spread_pct > 0 and abs(spread_pct) > self.max_spread_pct:
                continue

            entry_payback_hrs = self.calculate_payback_hours(self.entry_fee_rate, hourly_funding)
            roundtrip_payback_hrs = self.calculate_payback_hours(self.roundtrip_fee_rate, hourly_funding)

            perp_vol = safe_float(perp.get("turnover24h"))
            spot_vol = safe_float(spot_ticker.get("turnover24h"))
            open_interest = safe_float(perp.get("openInterestValue") or perp.get("openInterest"))

            results.append({
                "exchange": "Bybit",
                "coin": perp_sym,
                "spot_symbol": matched_spot_sym,
                "spot_pair": matched_spot_sym,
                "hourly_funding": hourly_funding,
                "hourly_funding_pct": hourly_funding_pct,
                "funding_interval_hr": interval_hr,
                "period_funding_pct": period_funding_rate * 100.0,
                "apr_pct": apr_pct,
                "apy_pct": apy_pct,
                "spot_price": spot_px,
                "perp_price": perp_px,
                "spread_pct": spread_pct,
                "entry_payback_hrs": entry_payback_hrs,
                "entry_payback_str": self.format_hours(entry_payback_hrs),
                "roundtrip_payback_hrs": roundtrip_payback_hrs,
                "roundtrip_payback_str": self.format_hours(roundtrip_payback_hrs),
                "perp_24h_volume": perp_vol,
                "spot_24h_volume": spot_vol,
                "open_interest": open_interest,
            })

        results.sort(key=lambda x: x["hourly_funding"], reverse=True)
        return results

    @staticmethod
    def calculate_funding_history_stats(history_records: list, days: Optional[int] = None) -> Dict[str, Any]:
        """
        Calculates comprehensive statistics for historical funding rate data.
        """
        import datetime

        if not history_records:
            return {
                "total_periods": 0,
                "days_requested": days,
                "stats": {},
                "records": []
            }

        # Sort chronologically (oldest to newest)
        sorted_records = sorted(history_records, key=lambda x: safe_float(x.get("fundingRateTimestamp")))

        # Filter by days if requested
        if days and days > 0:
            latest_ts = safe_float(sorted_records[-1].get("fundingRateTimestamp"))
            cutoff_ts = latest_ts - (days * 24 * 3600 * 1000)
            filtered = [r for r in sorted_records if safe_float(r.get("fundingRateTimestamp")) >= cutoff_ts]
            if filtered:
                sorted_records = filtered

        total_periods = len(sorted_records)

        # Detect funding interval (hours)
        interval_hr = 8.0
        if total_periods >= 2:
            ts0 = safe_float(sorted_records[0].get("fundingRateTimestamp"))
            ts1 = safe_float(sorted_records[1].get("fundingRateTimestamp"))
            diff_ms = abs(ts1 - ts0)
            if diff_ms > 0:
                detected = round(diff_ms / (3600.0 * 1000.0))
                if detected in [1, 2, 4, 8, 12, 24]:
                    interval_hr = float(detected)

        period_rates = [safe_float(r.get("fundingRate")) for r in sorted_records]
        hourly_rates = [r / interval_hr for r in period_rates]

        avg_period_rate = sum(period_rates) / total_periods
        avg_hourly_rate = sum(hourly_rates) / total_periods
        cumulative_rate = sum(period_rates)

        apr_simple_pct = avg_hourly_rate * 24 * 365 * 100.0
        try:
            apy_compound_pct = ((1.0 + avg_hourly_rate) ** (24 * 365) - 1.0) * 100.0
        except OverflowError:
            apy_compound_pct = float('inf')

        # Max / Min
        max_idx = max(range(total_periods), key=lambda i: period_rates[i])
        min_idx = min(range(total_periods), key=lambda i: period_rates[i])

        max_rate_pct = period_rates[max_idx] * 100.0
        max_ts = safe_float(sorted_records[max_idx].get("fundingRateTimestamp"))
        max_dt_str = datetime.datetime.fromtimestamp(max_ts / 1000.0, tz=datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")

        min_rate_pct = period_rates[min_idx] * 100.0
        min_ts = safe_float(sorted_records[min_idx].get("fundingRateTimestamp"))
        min_dt_str = datetime.datetime.fromtimestamp(min_ts / 1000.0, tz=datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")

        # Counts
        pos_count = sum(1 for r in period_rates if r > 0)
        neg_count = sum(1 for r in period_rates if r < 0)
        zero_count = sum(1 for r in period_rates if r == 0)

        pos_pct = (pos_count / total_periods) * 100.0
        neg_pct = (neg_count / total_periods) * 100.0
        zero_pct = (zero_count / total_periods) * 100.0

        # Standard deviation (Volatility)
        variance = sum((r - avg_period_rate) ** 2 for r in period_rates) / total_periods
        std_dev_period_pct = math.sqrt(variance) * 100.0
        std_dev_hourly_pct = (math.sqrt(variance) / interval_hr) * 100.0

        # Build formatted records
        formatted_records = []
        for r in sorted_records:
            ts = safe_float(r.get("fundingRateTimestamp"))
            dt_str = datetime.datetime.fromtimestamp(ts / 1000.0, tz=datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")
            p_rate = safe_float(r.get("fundingRate"))
            h_rate = p_rate / interval_hr
            formatted_records.append({
                "timestamp": int(ts),
                "datetime_utc": dt_str,
                "period_funding_rate": p_rate,
                "period_funding_pct": p_rate * 100.0,
                "hourly_funding_pct": h_rate * 100.0,
                "apr_pct": h_rate * 24 * 365 * 100.0,
            })

        return {
            "symbol": sorted_records[0].get("symbol", ""),
            "days_requested": days,
            "total_periods": total_periods,
            "funding_interval_hr": interval_hr,
            "stats": {
                "avg_period_funding_pct": avg_period_rate * 100.0,
                "avg_hourly_funding_pct": avg_hourly_rate * 100.0,
                "apr_simple_pct": apr_simple_pct,
                "apy_compound_pct": apy_compound_pct,
                "cumulative_funding_pct": cumulative_rate * 100.0,
                "max_rate_pct": max_rate_pct,
                "max_rate_time": max_dt_str,
                "min_rate_pct": min_rate_pct,
                "min_rate_time": min_dt_str,
                "pos_count": pos_count,
                "pos_pct": pos_pct,
                "neg_count": neg_count,
                "neg_pct": neg_pct,
                "zero_count": zero_count,
                "zero_pct": zero_pct,
                "std_dev_period_pct": std_dev_period_pct,
                "std_dev_hourly_pct": std_dev_hourly_pct,
            },
            "records": formatted_records
        }

    @staticmethod
    def simulate_orderbook_walk(levels: List[Tuple[float, float]], target_usd: float, is_buy: bool, mid_price: float) -> Tuple[Optional[float], Optional[float], float]:
        """
        Simulates walking the orderbook levels for a target USD size.
        Returns (vwap_price, slippage_pct, total_depth_usd_found)
        """
        if not levels or target_usd <= 0 or mid_price <= 0:
            return None, None, 0.0

        cum_usd = 0.0
        cum_qty = 0.0

        for px, sz in levels:
            if px <= 0 or sz <= 0:
                continue
            level_usd = px * sz
            if cum_usd + level_usd >= target_usd:
                rem_usd = target_usd - cum_usd
                cum_usd += rem_usd
                cum_qty += rem_usd / px
                break
            else:
                cum_usd += level_usd
                cum_qty += sz

        total_depth_found = cum_usd

        if cum_usd < target_usd or cum_qty <= 0:
            return None, None, total_depth_found

        vwap = cum_usd / cum_qty
        if is_buy:
            slippage_pct = ((vwap - mid_price) / mid_price) * 100.0
        else:
            slippage_pct = ((mid_price - vwap) / mid_price) * 100.0

        slippage_pct = max(0.0, slippage_pct)
        return vwap, slippage_pct, total_depth_found

    def evaluate_capital_capacity(self,
                                  spot_asks: List[Tuple[float, float]],
                                  spot_bids: List[Tuple[float, float]],
                                  spot_mid_px: float,
                                  perp_asks: List[Tuple[float, float]],
                                  perp_bids: List[Tuple[float, float]],
                                  perp_mid_px: float,
                                  hourly_funding: float,
                                  custom_target_usd: Optional[float] = None) -> Dict[str, Any]:
        """
        Evaluates capital capacity and orderbook slippage for Delta-neutral arbitrage.
        """
        is_positive_arbitrage = (hourly_funding >= 0)
        abs_hourly_funding = abs(hourly_funding)

        entry_spot_is_buy = is_positive_arbitrage
        entry_perp_is_buy = not is_positive_arbitrage

        spot_levels = spot_asks if entry_spot_is_buy else spot_bids
        perp_levels = perp_bids if entry_spot_is_buy else perp_asks

        spot_fee_pct = self.spot_taker_fee * 100.0
        perp_fee_pct = self.perp_taker_fee * 100.0
        base_fee_pct = spot_fee_pct + perp_fee_pct

        # Multi-scale position preset targets
        preset_targets = [1000, 5000, 10000, 25000, 50000, 100000, 250000, 500000]
        if custom_target_usd and custom_target_usd not in preset_targets and custom_target_usd > 0:
            eval_targets = sorted(list(set(preset_targets + [custom_target_usd])))
        else:
            eval_targets = preset_targets

        simulations = []
        for target_usd in eval_targets:
            s_vwap, s_slip, s_depth = self.simulate_orderbook_walk(spot_levels, target_usd, entry_spot_is_buy, spot_mid_px)
            p_vwap, p_slip, p_depth = self.simulate_orderbook_walk(perp_levels, target_usd, entry_perp_is_buy, perp_mid_px)

            if s_vwap is not None and p_vwap is not None:
                comb_slip = s_slip + p_slip
                total_cost_pct = base_fee_pct + comb_slip
                payback_hrs = self.calculate_payback_hours(total_cost_pct / 100.0, abs_hourly_funding)
                payback_str = self.format_hours(payback_hrs)

                if comb_slip <= 0.10:
                    status = "高度推荐 (极低滑点)"
                elif comb_slip <= 0.30:
                    status = "良好 (可容忍滑点)"
                elif comb_slip <= 0.60:
                    status = "中等 (较高滑点)"
                else:
                    status = "高风险 (严重滑点)"

                simulations.append({
                    "target_usd": target_usd,
                    "spot_vwap": s_vwap,
                    "spot_slippage_pct": s_slip,
                    "perp_vwap": p_vwap,
                    "perp_slippage_pct": p_slip,
                    "combined_slippage_pct": comb_slip,
                    "total_cost_pct": total_cost_pct,
                    "payback_hrs": payback_hrs,
                    "payback_str": payback_str,
                    "status": status,
                    "exceeds_depth": False
                })
            else:
                simulations.append({
                    "target_usd": target_usd,
                    "spot_vwap": s_vwap,
                    "spot_slippage_pct": s_slip,
                    "perp_vwap": p_vwap,
                    "perp_slippage_pct": p_slip,
                    "combined_slippage_pct": None,
                    "total_cost_pct": None,
                    "payback_hrs": None,
                    "payback_str": "深度不足",
                    "status": "超出盘口深度",
                    "exceeds_depth": True
                })

        # Calculate max capacity C_max for 0.1%, 0.2%, 0.5% slippage and 24h payback
        def find_max_capacity(check_fn) -> float:
            low = 100.0
            high = 1000000.0
            best = 0.0

            # Test coarse steps first
            for usd in range(1000, 1000000, 1000):
                s_vwap, s_slip, _ = self.simulate_orderbook_walk(spot_levels, float(usd), entry_spot_is_buy, spot_mid_px)
                p_vwap, p_slip, _ = self.simulate_orderbook_walk(perp_levels, float(usd), entry_perp_is_buy, perp_mid_px)
                if s_vwap is not None and p_vwap is not None:
                    comb_slip = s_slip + p_slip
                    total_cost_pct = base_fee_pct + comb_slip
                    payback_hrs = self.calculate_payback_hours(total_cost_pct / 100.0, abs_hourly_funding)
                    if check_fn(comb_slip, payback_hrs):
                        best = float(usd)
                    else:
                        break
                else:
                    break
            return best

        max_cap_01_pct = find_max_capacity(lambda s, pb: s <= 0.10)
        max_cap_02_pct = find_max_capacity(lambda s, pb: s <= 0.20)
        max_cap_05_pct = find_max_capacity(lambda s, pb: s <= 0.50)
        max_cap_24h_payback = find_max_capacity(lambda s, pb: pb is not None and pb <= 24.0)

        # Extract custom target simulation if requested
        custom_sim = None
        if custom_target_usd and custom_target_usd > 0:
            custom_sim = next((s for s in simulations if s["target_usd"] == custom_target_usd), None)

        return {
            "is_positive_arbitrage": is_positive_arbitrage,
            "hourly_funding": hourly_funding,
            "hourly_funding_pct": hourly_funding * 100.0,
            "fees": {
                "spot_fee_pct": spot_fee_pct,
                "perp_fee_pct": perp_fee_pct,
                "base_fee_pct": base_fee_pct,
            },
            "mid_prices": {
                "spot_mid_px": spot_mid_px,
                "perp_mid_px": perp_mid_px,
            },
            "max_capacities": {
                "max_cap_01_pct_usd": max_cap_01_pct,
                "max_cap_02_pct_usd": max_cap_02_pct,
                "max_cap_05_pct_usd": max_cap_05_pct,
                "max_cap_24h_payback_usd": max_cap_24h_payback,
            },
            "custom_target_usd": custom_target_usd,
            "custom_simulation": custom_sim,
            "simulations": simulations
        }



