import time
from typing import Dict, Any, List, Tuple, Optional
from src.calculator import FundingRateCalculator, parse_base_multiplier, safe_float

class BybitArbitrageExecutor:
    """
    Delta-Neutral Funding Rate Arbitrage Executor & Risk Guard for Bybit.
    Supports Dry-Run simulation, quantitative Risk Guard checks, and Force Overrides.
    """

    def __init__(self,
                 calculator: Optional[FundingRateCalculator] = None,
                 default_max_slippage_pct: float = 0.50,
                 default_max_payback_hours: float = 72.0,
                 default_max_spread_pct: float = 2.00):
        self.calc = calculator or FundingRateCalculator(spot_taker_fee=0.0010, perp_taker_fee=0.00055)
        self.default_max_slippage_pct = default_max_slippage_pct
        self.default_max_payback_hours = default_max_payback_hours
        self.default_max_spread_pct = default_max_spread_pct

    def parse_execution_size(self,
                             symbol: str,
                             amount_usd: Optional[float] = None,
                             amount_qty: Optional[float] = None,
                             spot_price: float = 0.0,
                             perp_price: float = 0.0) -> Dict[str, Any]:
        """
        Parses order sizing by USD capital or token quantity.
        Handles multiplier coins (e.g. 1000PEPEUSDT).
        Returns dict with spot_qty, perp_contracts_qty, target_usd, and multiplier.
        """
        base_coin = symbol[:-4] if symbol.endswith("USDT") or symbol.endswith("USDC") else symbol
        mult, clean_base = parse_base_multiplier(base_coin)
        spot_symbol = f"{clean_base}USDT"

        ref_px = spot_price if spot_price > 0 else perp_price
        if ref_px <= 0:
            raise ValueError(f"Invalid reference price for {symbol}: spot={spot_price}, perp={perp_price}")

        if amount_usd is not None and amount_usd > 0:
            target_usd = float(amount_usd)
            spot_qty = target_usd / ref_px
            perp_contracts_qty = spot_qty / mult
        elif amount_qty is not None and amount_qty > 0:
            spot_qty = float(amount_qty)
            perp_contracts_qty = spot_qty / mult
            target_usd = spot_qty * ref_px
        else:
            raise ValueError("Either amount_usd or amount_qty must be specified and > 0")

        return {
            "symbol": symbol,
            "spot_symbol": spot_symbol,
            "multiplier": mult,
            "clean_base": clean_base,
            "target_usd": target_usd,
            "spot_qty": spot_qty,
            "perp_contracts_qty": perp_contracts_qty,
            "ref_price": ref_px
        }

    def check_risk_guard(self,
                         hourly_funding: float,
                         combined_slippage_pct: Optional[float],
                         spot_mid_px: float,
                         perp_mid_px: float,
                         payback_hours: Optional[float],
                         exceeds_depth: bool,
                         max_slippage_pct: Optional[float] = None,
                         max_payback_hours: Optional[float] = None,
                         force: bool = False) -> Dict[str, Any]:
        """
        Evaluates 5 quantitative risk guard checks.
        Returns risk evaluation report with warnings, passed status, and block status.
        """
        max_slip = max_slippage_pct if max_slippage_pct is not None else self.default_max_slippage_pct
        max_pb = max_payback_hours if max_payback_hours is not None else self.default_max_payback_hours

        warnings = []

        # Check 1: Funding Rate Feasibility
        if hourly_funding <= 0:
            warnings.append(f"【资费异常】当前 1h 资金费率为 {hourly_funding*100:+.4f}%/h（非正资费），多头需要向空头支付资费！")

        # Check 2: Exceeds Orderbook Depth
        if exceeds_depth or combined_slippage_pct is None:
            warnings.append(f"【深度不足】目标建仓规模超出交易所当前 L2 盘口所能消化的实际挂单深度！")

        # Check 3: Combined Slippage Threshold
        elif combined_slippage_pct > max_slip:
            warnings.append(f"【滑点过高】综合开仓滑点 (+{combined_slippage_pct:.4f}%) 超过安全容忍阈值 (+{max_slip:.2f}%)！")

        # Check 4: Basis Spread Risk
        if spot_mid_px > 0 and perp_mid_px > 0:
            spread_pct = abs(perp_mid_px - spot_mid_px) / spot_mid_px * 100.0
            if spread_pct > self.default_max_spread_pct:
                warnings.append(f"【基差偏离】现货与永续价格偏离率 ({spread_pct:.2f}%) 超过安全界限 ({self.default_max_spread_pct:.2f}%)！")

        # Check 5: Payback Time Limit
        if payback_hours is not None and payback_hours > max_pb:
            warnings.append(f"【回本过慢】开仓成本预估回本时间 ({payback_hours:.1f}h) 超过最大允许时限 ({max_pb:.0f}h)！")

        has_risks = len(warnings) > 0
        if not has_risks:
            decision = "PASSED"
            status_text = "安全可行 (风控全部通过)"
        elif force:
            decision = "FORCED_OVERRIDE"
            status_text = "强制建仓 (风控拦截已被 --force 覆写)"
        else:
            decision = "BLOCKED"
            status_text = "建仓已被风控系统拦截"

        return {
            "decision": decision,
            "status_text": status_text,
            "has_risks": has_risks,
            "warnings": warnings,
            "force_enabled": force,
            "max_slippage_pct_allowed": max_slip,
            "max_payback_hours_allowed": max_pb,
        }

    def generate_try_run_plan(self,
                              symbol: str,
                              spot_symbol: str,
                              multiplier: float,
                              target_usd: float,
                              spot_qty: float,
                              perp_contracts_qty: float,
                              hourly_funding: float,
                              spot_asks: list,
                              spot_bids: list,
                              spot_mid_px: float,
                              perp_asks: list,
                              perp_bids: list,
                              perp_mid_px: float,
                              max_slippage_pct: Optional[float] = None,
                              max_payback_hours: Optional[float] = None,
                              force: bool = False,
                              execution_mode: str = "taker_taker") -> Dict[str, Any]:
        """
        Generates structured Try-Run simulation plan & risk report.
        Supports execution_mode:
        - 'taker_taker': Dual Market Taker (Zero adverse selection, deterministic delta) - Recommended SOP
        - 'maker_taker': Spot Maker (Post-Only) + Perp Taker (Trigger on Fill)
        """
        is_positive = (hourly_funding >= 0)
        spot_is_buy = is_positive
        perp_is_buy = not is_positive

        spot_levels = spot_asks if spot_is_buy else spot_bids
        perp_levels = perp_bids if spot_is_buy else perp_asks

        s_vwap, s_slip, s_depth = self.calc.simulate_orderbook_walk(spot_levels, target_usd, spot_is_buy, spot_mid_px)
        p_vwap, p_slip, p_depth = self.calc.simulate_orderbook_walk(perp_levels, target_usd, perp_is_buy, perp_mid_px)

        exceeds_depth = (s_vwap is None or p_vwap is None)

        if execution_mode == "maker_taker":
            spot_fee_pct = self.calc.spot_maker_fee * 100.0
            perp_fee_pct = self.calc.perp_taker_fee * 100.0
            effective_spot_slip = 0.0
            effective_perp_slip = p_slip if p_slip is not None else 0.0
        elif execution_mode == "maker_maker":
            spot_fee_pct = self.calc.spot_maker_fee * 100.0
            perp_fee_pct = self.calc.perp_maker_fee * 100.0
            effective_spot_slip = 0.0
            effective_perp_slip = 0.0
        else: # taker_taker
            spot_fee_pct = self.calc.spot_taker_fee * 100.0
            perp_fee_pct = self.calc.perp_taker_fee * 100.0
            effective_spot_slip = s_slip if s_slip is not None else 0.0
            effective_perp_slip = p_slip if p_slip is not None else 0.0

        base_fee_pct = spot_fee_pct + perp_fee_pct
        taker_taker_base_fee = (self.calc.spot_taker_fee + self.calc.perp_taker_fee) * 100.0
        fee_savings_pct = max(0.0, taker_taker_base_fee - base_fee_pct)
        fee_savings_usd = (fee_savings_pct / 100.0) * target_usd

        comb_slip = (effective_spot_slip + effective_perp_slip) if not exceeds_depth else None
        total_cost_pct = (base_fee_pct + comb_slip) if comb_slip is not None else None

        payback_hrs = self.calc.calculate_payback_hours(total_cost_pct / 100.0, abs(hourly_funding)) if total_cost_pct else None
        payback_str = self.calc.format_hours(payback_hrs) if payback_hrs else "深度不足"

        risk_report = self.check_risk_guard(
            hourly_funding=hourly_funding,
            combined_slippage_pct=comb_slip,
            spot_mid_px=spot_mid_px,
            perp_mid_px=perp_mid_px,
            payback_hours=payback_hrs,
            exceeds_depth=exceeds_depth,
            max_slippage_pct=max_slippage_pct,
            max_payback_hours=max_payback_hours,
            force=force
        )

        margin_required_usd = target_usd

        # Calculate best prices for Maker orders
        best_spot_bid = spot_bids[0][0] if spot_bids else spot_mid_px
        best_spot_ask = spot_asks[0][0] if spot_asks else spot_mid_px
        spot_maker_px = best_spot_bid if spot_is_buy else best_spot_ask

        if execution_mode == "maker_taker":
            spot_order_type = "Limit (Post-Only / Maker)"
            spot_expected_px = spot_maker_px
            spot_role = "Trigger Leg (主动买一挂单)"
            spot_tif = "PostOnly"

            perp_order_type = "Market / IOC (Taker)"
            perp_expected_px = p_vwap or perp_mid_px
            perp_role = "Hedge Leg (成交毫秒对冲)"
            perp_tif = "IOC"
            workflow_desc = "【Maker-Taker 触发对冲架构】第 1 步在现货买一价挂 Post-Only 限价单；第 2 步监听到现货成交事件后，毫秒级在合约端以 IOC/Market 市价开出等量空头对冲，省下昂贵的现货吃单费率与滑点，零单腿暴跌风险。"
        elif execution_mode == "maker_maker":
            spot_order_type = "Limit (Post-Only / Maker)"
            spot_expected_px = spot_maker_px
            spot_role = "Trigger Leg (主动挂单)"
            spot_tif = "PostOnly"

            best_perp_bid = perp_bids[0][0] if perp_bids else perp_mid_px
            best_perp_ask = perp_asks[0][0] if perp_asks else perp_mid_px
            perp_maker_px = best_perp_ask if spot_is_buy else best_perp_bid
            perp_order_type = "Limit (Post-Only / Maker)"
            perp_expected_px = perp_maker_px
            perp_role = "Passive Hedge (双边挂单)"
            perp_tif = "PostOnly"
            workflow_desc = "【双边 Maker 极低费率】现货与合约两端同时挂 Post-Only 挂单，享受最高手续费返还，但存在部分成交未对冲的单腿风险。"
        else: # taker_taker
            spot_order_type = "Market (Taker)"
            spot_expected_px = s_vwap or spot_mid_px
            spot_role = "Leg 1 (现货市价吃单)"
            spot_tif = "IOC"

            perp_order_type = "Market (Taker)"
            perp_expected_px = p_vwap or perp_mid_px
            perp_role = "Leg 2 (合约市价吃单)"
            perp_tif = "IOC"
            workflow_desc = "【双边 Taker 快速市价】现货与合约双边同时发出市价单，秒级完成建仓锁定 Delta 中性，承担双边 Taker 手续费与盘口冲击滑点。"

        orders_plan = [
            {
                "leg": "Spot Leg (现货)",
                "category": "spot",
                "symbol": spot_symbol,
                "side": "Buy" if spot_is_buy else "Sell",
                "order_type": spot_order_type,
                "role": spot_role,
                "time_in_force": spot_tif,
                "target_price": spot_expected_px,
                "quantity": spot_qty,
                "quantity_str": f"{spot_qty:,.4f} {spot_symbol.replace('USDT', '').replace('USDC', '').replace('@', '')}",
                "expected_vwap": spot_expected_px,
                "slippage_pct": effective_spot_slip,
                "fee_pct": spot_fee_pct,
                "usd_value": target_usd
            },
            {
                "leg": "Perp Leg (永续合约)",
                "category": "linear",
                "symbol": symbol,
                "side": "Sell" if spot_is_buy else "Buy",
                "order_type": perp_order_type,
                "role": perp_role,
                "time_in_force": perp_tif,
                "target_price": perp_expected_px,
                "quantity": perp_contracts_qty,
                "quantity_str": f"{perp_contracts_qty:,.4f} contracts ({symbol})",
                "expected_vwap": perp_expected_px,
                "slippage_pct": effective_perp_slip,
                "fee_pct": perp_fee_pct,
                "usd_value": target_usd
            }
        ]

        return {
            "execution_mode": execution_mode,
            "workflow_desc": workflow_desc,
            "symbol": symbol,
            "spot_symbol": spot_symbol,
            "multiplier": multiplier,
            "target_usd": target_usd,
            "spot_qty": spot_qty,
            "perp_contracts_qty": perp_contracts_qty,
            "hourly_funding_pct": hourly_funding * 100.0,
            "expected_spot_vwap": s_vwap,
            "spot_slippage_pct": effective_spot_slip,
            "expected_perp_vwap": p_vwap,
            "perp_slippage_pct": effective_perp_slip,
            "combined_slippage_pct": comb_slip,
            "base_fee_pct": base_fee_pct,
            "fee_savings_pct": fee_savings_pct,
            "fee_savings_usd": fee_savings_usd,
            "total_cost_pct": total_cost_pct,
            "payback_hours": payback_hrs,
            "payback_str": payback_str,
            "margin_required_usd": margin_required_usd,
            "exceeds_depth": exceeds_depth,
            "risk_guard": risk_report,
            "orders_plan": orders_plan
        }
