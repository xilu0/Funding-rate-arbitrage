import math
from typing import Dict, List, Any, Optional, Tuple

DEFAULT_HL_SPOT_TAKER_FEE = 0.000672  # 0.0672% (0.070% base with 4% referral discount)
DEFAULT_HL_PERP_TAKER_FEE = 0.000432  # 0.0432% (0.045% base with 4% referral discount)
DEFAULT_HL_SPOT_MAKER_FEE = 0.000144  # 0.0144% (0.015% base with 4% referral discount)
DEFAULT_HL_PERP_MAKER_FEE = 0.000144  # 0.0144% (0.015% base with 4% referral discount)

DEFAULT_BYBIT_SPOT_TAKER_FEE = 0.0010  # 0.10%
DEFAULT_BYBIT_PERP_TAKER_FEE = 0.00055 # 0.055%
DEFAULT_BYBIT_SPOT_MAKER_FEE = 0.00020 # 0.02%
DEFAULT_BYBIT_PERP_MAKER_FEE = 0.00020 # 0.02%

# Backward compatibility defaults
DEFAULT_SPOT_TAKER_FEE = DEFAULT_HL_SPOT_TAKER_FEE
DEFAULT_PERP_TAKER_FEE = DEFAULT_HL_PERP_TAKER_FEE
DEFAULT_SPOT_MAKER_FEE = DEFAULT_HL_SPOT_MAKER_FEE
DEFAULT_PERP_MAKER_FEE = DEFAULT_HL_PERP_MAKER_FEE

COMMON_PREFIX_ALIASES = {
    # Hyperliquid Unit Protocol Assets (U-prefix)
    'UBTC': 'BTC',
    'UETH': 'ETH',
    'USOL': 'SOL',
    'UDOGE': 'DOGE',
    'UAVAX': 'AVAX',
    'UENA': 'ENA',
    'UPUMP': 'PUMP',
    'UXPL': 'XPL',
    'UMON': 'MON',
    'UZEC': 'ZEC',
    'UUUSPX': 'SPX',
    'UFART': 'FARTCOIN',
    'UVIRT': 'VIRTUAL',
    'UBONK': 'kBONK',
    # Hyperliquid HyBridge & Wagyu Bridged Assets (Suffix 0 / 1)
    'LINK0': 'LINK',
    'AAVE0': 'AAVE',
    'AVAX0': 'AVAX',
    'BNB1': 'BNB',
    'BNB0': 'BNB',
    'XMR1': 'XMR',
    'CFX0': 'CFX',
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


def classify_hyperliquid_token(base_token: dict, spot_pair: dict, perp_coin: str, multiplier: float = 1.0) -> dict:
    """
    Classifies a Hyperliquid token/contract by origin nature, deployment standard, and risk notes.
    Distinguishes Canonical official assets, Native HYPE, Unit protocol synthetics,
    HyBridge bridged assets, and HIP-1 permissionless tokens.
    """
    token_name = base_token.get("name", "") if isinstance(base_token, dict) else ""
    is_canonical_token = bool(base_token.get("isCanonical", False)) if isinstance(base_token, dict) else False
    is_canonical_pair = bool(spot_pair.get("is_canonical", False) or spot_pair.get("isCanonical", False)) if isinstance(spot_pair, dict) else False
    evm_contract = base_token.get("evmContract") if isinstance(base_token, dict) else None
    evm_address = evm_contract.get("address") if isinstance(evm_contract, dict) else None
    token_id = base_token.get("tokenId", "") if isinstance(base_token, dict) else ""
    deployer_fee_share = safe_float(base_token.get("deployerTradingFeeShare", "0") if isinstance(base_token, dict) else "0")
    raw_pair_name = (spot_pair.get("raw_pair_name") or spot_pair.get("name") or "") if isinstance(spot_pair, dict) else ""

    tags = []

    # 1. Determine origin type
    if is_canonical_token or token_name in ["PURR", "USDC"]:
        origin_type = "OFFICIAL_CANONICAL"
        origin_badge = "🏛️ 官方 Canonical"
        origin_desc = "Hyperliquid 官方创世/原生资产，官方维护撮合与定价"
        collateral_status = "支持质押 (65% LTV / 折算率，方案 D 核心标的)"
        risk_note = "预言机最稳健，流动性充裕；在 Portfolio Margin 账户中享有 65% 质押折算率。"
        tags.append("🏛️ 官方 Canonical")
    elif token_name == "HYPE":
        origin_type = "NATIVE_HYPE"
        origin_badge = "🏛️ 原生 HYPE"
        origin_desc = "Hyperliquid L1 原生核心生态资产"
        collateral_status = "支持质押 (65% LTV / 折算率，方案 D 核心标的)"
        risk_note = "Hyperliquid L1 核心代币，深度极佳；方案 D 现货质押核心标的。"
        tags.append("🏛️ 原生 HYPE")
    elif token_name.startswith("U") and token_name in COMMON_PREFIX_ALIASES:
        origin_type = "UNIT_BRIDGED"
        origin_badge = "🌉 Unit 映射"
        origin_desc = f"Unit Protocol 在 Hyperliquid L1 封装映射的外部资产 ({perp_coin})"
        collateral_status = "全款现货对冲 (目前非跨资产质押品)"
        risk_note = "通过 Unit Protocol 封装映射，适合 1:1 现货全款持有对冲；需关注 Unit 协议流动性与脱锚风险。"
        tags.append("🌉 Unit 映射")
    elif token_name in ['LINK0', 'AAVE0', 'AVAX0', 'BNB1', 'BNB0', 'XMR1', 'CFX0']:
        origin_type = "HYBRIDGE_BRIDGED"
        origin_badge = "🌉 跨链桥接"
        origin_desc = f"通过 HyBridge / Wagyu 跨链桥接至 Hyperliquid L1 的资产 ({perp_coin})"
        collateral_status = "全款现货对冲 (桥接映射资产)"
        risk_note = "存在跨链桥流动性与微小基差波动，建仓前需确认盘口滑点。"
        tags.append("🌉 跨链桥接")
    else:
        origin_type = "HIP1_PERMISSIONLESS"
        origin_badge = "⚡ HIP-1 无许可"
        origin_desc = "Hyperliquid L1 社区 HIP-1 无许可发币标准 (拍卖/联合曲线)"
        collateral_status = "全款现货对冲 (独立现货)"
        risk_note = "社区无许可创建代币，需核对底层 Pair ID 谨防同名撞车；关注部署者分成与抛压。"
        tags.append("⚡ HIP-1 发币")

    if evm_address:
        tags.append("⛓️ HyperEVM")

    if multiplier > 1.0:
        tags.append(f"🔢 {int(multiplier)}x 乘数")

    return {
        "origin_type": origin_type,
        "origin_badge": origin_badge,
        "origin_tags": tags,
        "origin_desc": origin_desc,
        "collateral_status": collateral_status,
        "risk_note": risk_note,
        "is_canonical": is_canonical_token or is_canonical_pair,
        "has_evm": bool(evm_address),
        "evm_address": evm_address,
        "token_id": token_id,
        "deployer_fee_share_pct": deployer_fee_share * 100.0,
        "raw_pair_id": raw_pair_name
    }


def classify_bybit_token(symbol: str, spot_symbol: str, multiplier: float = 1.0) -> dict:
    """
    Classifies a Bybit token/contract by origin nature and multiplier.
    """
    tags = []
    if multiplier > 1.0:
        origin_type = "BYBIT_MULTIPLIER"
        origin_badge = f"🔢 {int(multiplier)}x 乘数"
        origin_desc = f"Bybit 正向永续合约 ({int(multiplier)}倍放大乘数)"
        collateral_status = "统一交易账户 (UTA) 支持多币种质押"
        risk_note = f"1张合约代表 {int(multiplier)} 个现货代币，下单计算须按 1:{int(multiplier)} 换算。"
        tags.append(f"🔢 {int(multiplier)}x 乘数")
    else:
        origin_type = "BYBIT_OFFICIAL_LINEAR"
        origin_badge = "🏛️ 官方正向"
        origin_desc = "Bybit 官方主流正向 USDT/USDC 永续合约"
        collateral_status = "统一交易账户 (UTA) 支持多币种质押"
        risk_note = "主流正向合约，深度好、预言机公允；在 UTA 模式下注意维持保证金率。"
        tags.append("🏛️ 官方正向")

    return {
        "origin_type": origin_type,
        "origin_badge": origin_badge,
        "origin_tags": tags,
        "origin_desc": origin_desc,
        "collateral_status": collateral_status,
        "risk_note": risk_note,
        "is_canonical": True,
        "has_evm": False,
        "evm_address": None,
        "token_id": "",
        "deployer_fee_share_pct": 0.0,
        "raw_pair_id": spot_symbol
    }


class FundingRateCalculator:
    """Calculates APR, APY, fee payback times, and matches spot & perp markets."""

    def __init__(self, 
                 spot_taker_fee: float = DEFAULT_SPOT_TAKER_FEE, 
                 perp_taker_fee: float = DEFAULT_PERP_TAKER_FEE,
                 spot_maker_fee: float = DEFAULT_SPOT_MAKER_FEE,
                 perp_maker_fee: float = DEFAULT_PERP_MAKER_FEE,
                 enable_aliases: bool = True,
                 max_spread_pct: float = 15.0):
        self.spot_taker_fee = spot_taker_fee
        self.perp_taker_fee = perp_taker_fee
        self.spot_maker_fee = spot_maker_fee
        self.perp_maker_fee = perp_maker_fee
        self.enable_aliases = enable_aliases
        self.max_spread_pct = max_spread_pct

    def get_entry_fee_rate(self, mode: str = "taker_taker") -> float:
        """
        Single-side entry fee rate based on execution mode:
        - 'taker_taker': Spot Taker + Perp Taker (Zero adverse selection, deterministic delta - Recommended SOP)
        - 'maker_taker': Spot Maker + Perp Taker (Trigger on fill)
        - 'maker_maker': Spot Maker + Perp Maker (Dual post-only)
        """
        if mode == "maker_taker":
            return self.spot_maker_fee + self.perp_taker_fee
        elif mode == "maker_maker":
            return self.spot_maker_fee + self.perp_maker_fee
        return self.spot_taker_fee + self.perp_taker_fee

    def get_roundtrip_fee_rate(self, mode: str = "taker_taker") -> float:
        """Full round-trip fee rate based on execution mode (Entry + Exit)."""
        return self.get_entry_fee_rate(mode) * 2.0

    @property
    def entry_fee_rate(self) -> float:
        """Single-side entry market order fee rate (Spot Taker + Perp Taker) for backward compatibility."""
        return self.get_entry_fee_rate("taker_taker")

    @property
    def roundtrip_fee_rate(self) -> float:
        """Full round-trip market order fee rate (Entry + Exit) for backward compatibility."""
        return self.get_roundtrip_fee_rate("taker_taker")

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
                "base_token": base_token,
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
            mult = 1.0

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

            # Determine multiplier for k-contracts or parsed prefixes
            if matched_perp_coin.startswith("k") and len(matched_perp_coin) > 1 and matched_perp_coin[1:].isupper():
                mult = 1000.0
            else:
                mult, _ = parse_base_multiplier(matched_perp_coin)

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
            scaled_spot_px = spot_px * mult
            perp_px = perp_info["mid_px"]
            
            # Basis spread % (compared with scaled spot price for delta-neutrality)
            spread_pct = ((perp_px - scaled_spot_px) / scaled_spot_px * 100.0) if scaled_spot_px > 0 else 0.0

            # Filter out fake symbol collisions if max_spread_pct is set (>0)
            if self.max_spread_pct > 0 and abs(spread_pct) > self.max_spread_pct:
                continue

            # Fee Payback Hours
            entry_payback_hrs = self.calculate_payback_hours(self.entry_fee_rate, hourly_funding)
            roundtrip_payback_hrs = self.calculate_payback_hours(self.roundtrip_fee_rate, hourly_funding)

            token_meta = classify_hyperliquid_token(
                base_token=spot_info.get("base_token", {}),
                spot_pair=spot_info,
                perp_coin=matched_perp_coin,
                multiplier=mult
            )

            results.append({
                "exchange": "Hyperliquid",
                "coin": matched_perp_coin,
                "spot_symbol": base_symbol,
                "spot_pair": spot_info["spot_pair_name"],
                "raw_spot_pair": spot_info.get("raw_pair_name", spot_info["spot_pair_name"]),
                "multiplier": mult,
                "origin_type": token_meta["origin_type"],
                "origin_badge": token_meta["origin_badge"],
                "origin_tags": token_meta["origin_tags"],
                "origin_desc": token_meta["origin_desc"],
                "collateral_status": token_meta["collateral_status"],
                "risk_note": token_meta["risk_note"],
                "is_canonical": token_meta["is_canonical"],
                "has_evm": token_meta["has_evm"],
                "evm_address": token_meta["evm_address"],
                "token_id": token_meta["token_id"],
                "deployer_fee_share_pct": token_meta["deployer_fee_share_pct"],
                "raw_pair_id": token_meta["raw_pair_id"],
                "hourly_funding": hourly_funding,
                "hourly_funding_pct": hourly_funding * 100.0,
                "funding_interval_hr": 1.0,
                "period_funding_pct": hourly_funding * 100.0,
                "apr_pct": apr_pct,
                "apy_pct": apy_pct,
                "spot_price": spot_px,
                "scaled_spot_price": scaled_spot_px,
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

            bybit_meta = classify_bybit_token(
                symbol=perp_sym,
                spot_symbol=matched_spot_sym,
                multiplier=multiplier
            )

            results.append({
                "exchange": "Bybit",
                "coin": perp_sym,
                "spot_symbol": matched_spot_sym,
                "spot_pair": matched_spot_sym,
                "multiplier": multiplier,
                "origin_type": bybit_meta["origin_type"],
                "origin_badge": bybit_meta["origin_badge"],
                "origin_tags": bybit_meta["origin_tags"],
                "origin_desc": bybit_meta["origin_desc"],
                "collateral_status": bybit_meta["collateral_status"],
                "risk_note": bybit_meta["risk_note"],
                "is_canonical": bybit_meta["is_canonical"],
                "has_evm": bybit_meta["has_evm"],
                "evm_address": bybit_meta["evm_address"],
                "token_id": bybit_meta["token_id"],
                "deployer_fee_share_pct": bybit_meta["deployer_fee_share_pct"],
                "raw_pair_id": bybit_meta["raw_pair_id"],
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
        Compatible with Bybit (fundingRateTimestamp, symbol) and Hyperliquid (time, coin).
        """
        import datetime

        if not history_records:
            return {
                "symbol": "",
                "total_periods": 0,
                "days_requested": days,
                "funding_interval_hr": 8.0,
                "stats": {},
                "records": []
            }

        def _extract_ts(rec: Dict[str, Any]) -> float:
            return safe_float(rec.get("fundingRateTimestamp") if "fundingRateTimestamp" in rec else rec.get("time"))

        # Sort chronologically (oldest to newest)
        sorted_records = sorted(history_records, key=_extract_ts)

        # Filter by days if requested
        if days and days > 0 and sorted_records:
            latest_ts = _extract_ts(sorted_records[-1])
            cutoff_ts = latest_ts - (days * 24 * 3600 * 1000)
            filtered = [r for r in sorted_records if _extract_ts(r) >= cutoff_ts]
            if filtered:
                sorted_records = filtered

        total_periods = len(sorted_records)
        if total_periods == 0:
            return {
                "symbol": "",
                "total_periods": 0,
                "days_requested": days,
                "funding_interval_hr": 8.0,
                "stats": {},
                "records": []
            }

        symbol_name = sorted_records[0].get("symbol") or sorted_records[0].get("coin") or ""

        # Detect funding interval (hours)
        is_hl_format = ("time" in sorted_records[0] or "coin" in sorted_records[0])
        interval_hr = 1.0 if is_hl_format else 8.0

        if total_periods >= 2:
            ts0 = _extract_ts(sorted_records[0])
            ts1 = _extract_ts(sorted_records[1])
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
        max_ts = _extract_ts(sorted_records[max_idx])
        max_dt_str = datetime.datetime.fromtimestamp(max_ts / 1000.0, tz=datetime.timezone.utc).strftime("%Y-%m-%d %H:%M") if max_ts > 0 else "--"

        min_rate_pct = period_rates[min_idx] * 100.0
        min_ts = _extract_ts(sorted_records[min_idx])
        min_dt_str = datetime.datetime.fromtimestamp(min_ts / 1000.0, tz=datetime.timezone.utc).strftime("%Y-%m-%d %H:%M") if min_ts > 0 else "--"

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
            ts = _extract_ts(r)
            dt_str = datetime.datetime.fromtimestamp(ts / 1000.0, tz=datetime.timezone.utc).strftime("%Y-%m-%d %H:%M") if ts > 0 else "--"
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
            "symbol": symbol_name,
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
                                  custom_target_usd: Optional[float] = None,
                                  execution_mode: str = "taker_taker") -> Dict[str, Any]:
        """
        Evaluates capital capacity and orderbook slippage for Delta-neutral arbitrage.
        Supports execution_mode: 'taker_taker' (Dual Taker - Recommended SOP) or 'maker_taker' (Spot Maker + Perp Taker).
        """
        is_positive_arbitrage = (hourly_funding >= 0)
        abs_hourly_funding = abs(hourly_funding)

        entry_spot_is_buy = is_positive_arbitrage
        entry_perp_is_buy = not is_positive_arbitrage

        spot_levels = spot_asks if entry_spot_is_buy else spot_bids
        perp_levels = perp_bids if entry_spot_is_buy else perp_asks

        if execution_mode == "maker_taker":
            spot_fee_pct = self.spot_maker_fee * 100.0
            perp_fee_pct = self.perp_taker_fee * 100.0
        elif execution_mode == "maker_maker":
            spot_fee_pct = self.spot_maker_fee * 100.0
            perp_fee_pct = self.perp_maker_fee * 100.0
        else:
            spot_fee_pct = self.spot_taker_fee * 100.0
            perp_fee_pct = self.perp_taker_fee * 100.0

        base_fee_pct = spot_fee_pct + perp_fee_pct
        taker_taker_base_fee_pct = (self.spot_taker_fee + self.perp_taker_fee) * 100.0
        fee_savings_pct = max(0.0, taker_taker_base_fee_pct - base_fee_pct)

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
                # In maker_taker mode, spot leg is a resting limit order (0.0% slippage vs spot mid)
                effective_spot_slip = 0.0 if execution_mode in ["maker_taker", "maker_maker"] else s_slip
                effective_perp_slip = 0.0 if execution_mode == "maker_maker" else p_slip
                comb_slip = effective_spot_slip + effective_perp_slip

                total_cost_pct = base_fee_pct + comb_slip
                payback_hrs = self.calculate_payback_hours(total_cost_pct / 100.0, abs_hourly_funding)
                payback_str = self.format_hours(payback_hrs)

                fee_savings_usd = (fee_savings_pct / 100.0) * target_usd

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
                    "spot_slippage_pct": effective_spot_slip,
                    "perp_vwap": p_vwap,
                    "perp_slippage_pct": effective_perp_slip,
                    "combined_slippage_pct": comb_slip,
                    "total_cost_pct": total_cost_pct,
                    "fee_savings_usd": fee_savings_usd,
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
                    "fee_savings_usd": None,
                    "payback_hrs": None,
                    "payback_str": "深度不足",
                    "status": "超出盘口深度",
                    "exceeds_depth": True
                })

        # Calculate max capacity C_max for 0.1%, 0.2%, 0.5% slippage and 24h payback
        def find_max_capacity(check_fn) -> float:
            best = 0.0
            # Test coarse steps first
            for usd in range(1000, 1000000, 1000):
                s_vwap, s_slip, _ = self.simulate_orderbook_walk(spot_levels, float(usd), entry_spot_is_buy, spot_mid_px)
                p_vwap, p_slip, _ = self.simulate_orderbook_walk(perp_levels, float(usd), entry_perp_is_buy, perp_mid_px)
                if s_vwap is not None and p_vwap is not None:
                    effective_spot_slip = 0.0 if execution_mode in ["maker_taker", "maker_maker"] else s_slip
                    effective_perp_slip = 0.0 if execution_mode == "maker_maker" else p_slip
                    comb_slip = effective_spot_slip + effective_perp_slip
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
            "execution_mode": execution_mode,
            "is_positive_arbitrage": is_positive_arbitrage,
            "hourly_funding": hourly_funding,
            "hourly_funding_pct": hourly_funding * 100.0,
            "fees": {
                "spot_fee_pct": spot_fee_pct,
                "perp_fee_pct": perp_fee_pct,
                "base_fee_pct": base_fee_pct,
                "fee_savings_pct": fee_savings_pct,
                "taker_taker_base_fee_pct": taker_taker_base_fee_pct,
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



