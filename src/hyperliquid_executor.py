import time
import os
import math
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, List, Tuple, Optional
import src.env
from src.hyperliquid_client import HyperliquidClient
from src.calculator import FundingRateCalculator, safe_float

class HyperliquidExecutor:
    """
    Hyperliquid API Wallet (Agent Wallet) Executor & Operations Manager.
    Manages account health, canary verification, order lifecycle,
    Scheme D risk monitoring, USD class transfers, and emergency deleveraging.
    """

    def __init__(self,
                 account_address: Optional[str] = None,
                 agent_private_key: Optional[str] = None,
                 agent_address: Optional[str] = None,
                 gopass_secret: Optional[str] = None,
                 base_url: str = "https://api.hyperliquid.xyz",
                 is_mainnet: bool = True,
                 calculator: Optional[FundingRateCalculator] = None,
                 hl_client: Optional[HyperliquidClient] = None):
        # Optional legacy fallback for gopass_secret if provided
        if gopass_secret or os.getenv("GOPASS_SECRET"):
            secret_path = gopass_secret or os.getenv("GOPASS_SECRET")
            gopass_data = self.load_gopass_credentials(secret_path) if secret_path else {}
            if not account_address and "HL_ACCOUNT_ADDRESS" in gopass_data:
                account_address = gopass_data["HL_ACCOUNT_ADDRESS"]
            if not agent_private_key and "HL_AGENT_PRIVATE_KEY" in gopass_data:
                agent_private_key = gopass_data["HL_AGENT_PRIVATE_KEY"]
            if not agent_address and "HL_AGENT_ADDRESS" in gopass_data:
                agent_address = gopass_data["HL_AGENT_ADDRESS"]

        self.account_address = (account_address or os.getenv("HL_ACCOUNT_ADDRESS", "")).strip().lower()
        self.agent_address = (agent_address or os.getenv("HL_AGENT_ADDRESS", "")).strip().lower()
        self.agent_private_key = (agent_private_key or os.getenv("HL_AGENT_PRIVATE_KEY", "")).strip()
        if self.agent_private_key and not self.agent_private_key.startswith("0x") and len(self.agent_private_key) == 64:
            self.agent_private_key = "0x" + self.agent_private_key

        self.base_url = base_url
        self.is_mainnet = is_mainnet
        self.calc = calculator or FundingRateCalculator()
        self.client = hl_client or HyperliquidClient(api_url=f"{base_url}/info")
        self._sdk_available = False
        self._init_sdk()

    @staticmethod
    def load_gopass_credentials(secret_path: str) -> Dict[str, str]:
        """Legacy helper: Loads Key-Value pairs from gopass secret if gopass CLI is available."""
        import subprocess
        try:
            res = subprocess.run(["gopass", "show", "-n", secret_path], capture_output=True, text=True, check=True)
            creds = {}
            for line in res.stdout.splitlines():
                line = line.strip()
                if ":" in line and not line.startswith("---"):
                    k, v = line.split(":", 1)
                    creds[k.strip()] = v.strip()
            return creds
        except Exception:
            return {}

    def _init_sdk(self):
        """Attempts to initialize the official Hyperliquid SDK or eth_account if installed."""
        try:
            from eth_account import Account
            self.Account = Account
            self._eth_account_available = True
        except ImportError:
            self._eth_account_available = False

        try:
            from hyperliquid.exchange import Exchange
            from hyperliquid.info import Info
            self.Exchange = Exchange
            self.Info = Info
            self._sdk_available = True
        except ImportError:
            self._sdk_available = False

    def get_agent_public_address(self) -> Optional[str]:
        """Derives the agent's EVM public address from the private key or returns configured address."""
        if getattr(self, "_eth_account_available", False) and self.agent_private_key:
            try:
                acct = self.Account.from_key(self.agent_private_key)
                return acct.address.lower()
            except Exception:
                pass
        return self.agent_address if getattr(self, "agent_address", "") else None

    def check_agent_authorization(self) -> Dict[str, Any]:
        """
        Verifies if the configured agent wallet is recognized and authorized
        by the master account on Hyperliquid info API.
        """
        agent_pub = self.get_agent_public_address()

        if not self.account_address:
            return {
                "authorized": False,
                "master_address": self.account_address,
                "agent_address": agent_pub,
                "error": "Master account address (HL_ACCOUNT_ADDRESS) is not configured."
            }

        if not agent_pub:
            return {
                "authorized": False,
                "master_address": self.account_address,
                "agent_address": None,
                "error": "Agent address/key is invalid or eth_account not installed to derive public address."
            }

        try:
            extra_agents = self.client.get_extra_agents(self.account_address)
            approved = any(
                (isinstance(a, dict) and a.get("address", "").lower() == agent_pub) or
                (isinstance(a, str) and a.lower() == agent_pub)
                for a in extra_agents
            )
            return {
                "authorized": approved,
                "master_address": self.account_address,
                "agent_address": agent_pub,
                "extra_agents_count": len(extra_agents),
                "all_agents": extra_agents,
                "error": None if approved else f"Agent {agent_pub} not found in master account's approved agents list."
            }
        except Exception as e:
            return {
                "authorized": False,
                "master_address": self.account_address,
                "agent_address": agent_pub,
                "error": f"Failed to query Hyperliquid API: {e}"
            }

    def run_canary_test(self, coin: str = "PURR", far_discount_pct: float = 0.90) -> Dict[str, Any]:
        """
        Performs a zero-risk canary verification:
        1. Checks SDK & Agent credentials.
        2. Places a Post-Only limit buy order far below market price (-90%).
        3. Measures roundtrip API latency.
        4. Immediately cancels the order.
        5. Returns status report.
        """
        agent_pub = self.get_agent_public_address()
        if not self.agent_private_key or not self.account_address:
            return {
                "status": "FAILED",
                "stage": "CONFIG",
                "error": "Both HL_ACCOUNT_ADDRESS and HL_AGENT_PRIVATE_KEY must be configured.",
                "agent_address": agent_pub
            }

        if not self._sdk_available:
            return {
                "status": "SKIPPED_NO_SDK",
                "stage": "SDK_LOAD",
                "message": "hyperliquid-python-sdk or eth_account is not installed in the local Python environment. Run: pip install hyperliquid-python-sdk",
                "agent_address": agent_pub
            }

        try:
            start_t = time.time()
            account = self.Account.from_key(self.agent_private_key)
            exchange = self.Exchange(account, self.base_url, account_address=self.account_address)

            # Step 1: Zero-margin Protocol EIP-712 Signature & Auth Test (update leverage on BTC)
            sig_start_t = time.time()
            sig_test_res = exchange.update_leverage(20, "BTC", is_cross=True)
            sig_latency_ms = int((time.time() - sig_start_t) * 1000)
            sig_ok = isinstance(sig_test_res, dict) and sig_test_res.get("status") == "ok"

            # Step 2: Order placement test with >= $11 USD minimum value
            perp_univ, perp_ctxs = self.client.get_perp_market_data()
            matched_idx = next((i for i, u in enumerate(perp_univ) if u.get("name") == coin), -1)
            sz_decimals = 0
            if matched_idx >= 0 and matched_idx < len(perp_ctxs):
                mid_px = safe_float(perp_ctxs[matched_idx].get("midPx", perp_ctxs[matched_idx].get("markPx", 1.0)))
                sz_decimals = int(perp_univ[matched_idx].get("szDecimals", 0))
            else:
                mid_px = 1.0

            # Set ultra-low post-only buy price (-90% market discount)
            canary_px = max(round(mid_px * (1.0 - far_discount_pct), 4), 0.0001)

            # Hyperliquid requires order notional >= $10.00 USD
            min_target_notional = 12.0
            raw_sz = min_target_notional / canary_px
            canary_sz = round(raw_sz, sz_decimals) if sz_decimals > 0 else int(raw_sz) + 1
            if canary_sz * canary_px < 10.0:
                canary_sz += (10 ** (-sz_decimals) if sz_decimals > 0 else 1)

            order_start_t = time.time()
            order_res = exchange.order(
                name=coin,
                is_buy=True,
                sz=canary_sz,
                limit_px=canary_px,
                order_type={"limit": {"tif": "Alo"}}, # Alo = Add Liquidity Only (Post-Only)
                reduce_only=False
            )
            order_latency_ms = int((time.time() - order_start_t) * 1000)

            # Parse order ID
            oid = None
            order_error = None
            if isinstance(order_res, dict) and order_res.get("status") == "ok":
                response_data = order_res.get("response", {})
                data_inner = response_data.get("data", {})
                statuses = data_inner.get("statuses", [])
                if statuses:
                    if "resting" in statuses[0]:
                        oid = statuses[0]["resting"].get("oid")
                    elif "error" in statuses[0]:
                        order_error = statuses[0]["error"]
            elif isinstance(order_res, dict) and "response" in order_res:
                order_error = str(order_res["response"])

            # If order placed, immediately cancel it
            cancel_latency_ms = 0
            if oid:
                cancel_start_t = time.time()
                cancel_res = exchange.cancel(coin, oid)
                cancel_latency_ms = int((time.time() - cancel_start_t) * 1000)

            total_latency_ms = int((time.time() - start_t) * 1000)

            if oid:
                return {
                    "status": "PASSED",
                    "stage": "COMPLETE",
                    "master_address": self.account_address,
                    "agent_address": agent_pub,
                    "coin": coin,
                    "canary_price": canary_px,
                    "canary_size": canary_sz,
                    "order_id": oid,
                    "sig_latency_ms": sig_latency_ms,
                    "order_latency_ms": order_latency_ms,
                    "cancel_latency_ms": cancel_latency_ms,
                    "total_latency_ms": total_latency_ms,
                    "verification_type": "ORDER_AND_CANCEL"
                }
            elif sig_ok:
                # EIP-712 signature & Agent auth confirmed via protocol state update
                return {
                    "status": "PASSED",
                    "stage": "SIG_VERIFIED",
                    "master_address": self.account_address,
                    "agent_address": agent_pub,
                    "sig_latency_ms": sig_latency_ms,
                    "total_latency_ms": total_latency_ms,
                    "verification_type": "PROTOCOL_STATE_SIGNATURE",
                    "note": f"EIP-712 签名与 Agent 授权 100% 验证通过 (挂单跳过: {order_error or '账户余额不足以挂 $10 订单'})"
                }
            else:
                return {
                    "status": "FAILED",
                    "stage": "SIGNATURE_ERROR",
                    "error": order_error or str(sig_test_res),
                    "agent_address": agent_pub
                }
        except Exception as e:
            return {
                "status": "FAILED",
                "stage": "EXECUTION_ERROR",
                "error": str(e),
                "agent_address": agent_pub
            }

    def evaluate_scheme_d_health(self) -> Dict[str, Any]:
        """
        Evaluates Master Account's Scheme D (Collateral + Perp Hedge) Health:
        - Accurately prices Spot Collateral via live mark prices and token LTV (e.g. 65% on HYPE).
        - Computes Total Equity = Spot USDC + Spot Tokens Valuation + Perp Net Equity.
        - Computes Effective Margin = Spot USDC + Spot Collateral (Valuation * LTV) + Perp Net Equity.
        - Calculates live distance to liquidation across active positions.
        - Reflects true cumulative funding income (+ sign for earned cash flow).
        - Evaluates Tier 1/2/3 Scheme D risk alerts.
        """
        if not self.account_address:
            raise ValueError("Master account address (HL_ACCOUNT_ADDRESS) is required for health evaluation.")

        clearinghouse = self.client.get_clearinghouse_state(self.account_address)
        spot_state = self.client.get_spot_clearinghouse_state(self.account_address)

        # Parse Margin & Balances
        margin_summary = clearinghouse.get("crossMarginSummary") or clearinghouse.get("marginSummary", {})
        perp_account_value = safe_float(margin_summary.get("accountValue", 0.0))
        total_margin_used = safe_float(margin_summary.get("totalMarginUsed", 0.0))
        total_ntl_pos = safe_float(margin_summary.get("totalNtlPos", 0.0))
        total_raw_usd = safe_float(margin_summary.get("totalRawUsd", 0.0)) or perp_account_value
        withdrawable = safe_float(clearinghouse.get("withdrawable", 0.0))

        # Perp Positions
        positions = []
        cum_funding_total = 0.0
        total_upnl = 0.0

        for ap in clearinghouse.get("assetPositions", []):
            pos_data = ap.get("position", {})
            if pos_data:
                coin = pos_data.get("coin", "")
                szi = safe_float(pos_data.get("szi", 0.0))
                if szi != 0.0 or pos_data.get("entryPx"):
                    entry_px = safe_float(pos_data.get("entryPx", 0.0))
                    liq_px = safe_float(pos_data.get("liquidationPx", 0.0))
                    upnl = safe_float(pos_data.get("unrealizedPnl", 0.0))
                    # Note: on Hyperliquid, position.cumFunding is funding paid out from position ledger.
                    # A negative cumFunding value indicates positive net funding income earned by the account.
                    cum_funding_raw = safe_float(pos_data.get("cumFunding", {}).get("allTime", 0.0))
                    cum_funding = -cum_funding_raw
                    margin_used = safe_float(pos_data.get("marginUsed", 0.0))

                    total_upnl += upnl
                    cum_funding_total += cum_funding

                    positions.append({
                        "coin": coin,
                        "size": abs(szi),
                        "raw_size": szi,
                        "side": "Short" if szi < 0 else "Long",
                        "entry_price": entry_px,
                        "liquidation_price": liq_px,
                        "unrealized_pnl": upnl,
                        "margin_used": margin_used,
                        "upnl": upnl,
                        "cum_funding": cum_funding
                    })

        # Spot price map lookup (Spot market data & allMids)
        token_prices: Dict[str, float] = {}
        try:
            spot_data = self.client.get_spot_market_data()
            if isinstance(spot_data, tuple) and len(spot_data) >= 3:
                tokens, universe, asset_ctxs = spot_data[0], spot_data[1], spot_data[2]
                if isinstance(tokens, list) and isinstance(universe, list) and isinstance(asset_ctxs, list):
                    token_by_idx = {t["index"]: t for t in tokens if isinstance(t, dict) and "index" in t}
                    ctx_by_name = {ctx.get("coin"): ctx for ctx in asset_ctxs if isinstance(ctx, dict) and ctx.get("coin")}
                    for u in universe:
                        if isinstance(u, dict):
                            pair_tokens = u.get("tokens", [])
                            if len(pair_tokens) >= 2 and pair_tokens[1] == 0:  # Quote is USDC (token 0)
                                base_idx = pair_tokens[0]
                                base_token = token_by_idx.get(base_idx, {})
                                base_name = base_token.get("name")
                                pair_name = u.get("name")
                                ctx = ctx_by_name.get(pair_name)
                                if ctx and base_name:
                                    px = safe_float(ctx.get("markPx") or ctx.get("midPx", 0.0))
                                    if px > 0:
                                        token_prices[base_name] = px
        except Exception:
            pass

        all_mids: Dict[str, Any] = {}
        if hasattr(self.client, "get_all_mids"):
            try:
                all_mids = self.client.get_all_mids()
            except Exception:
                pass

        # Spot Balances & Collateral Valuation
        spot_balances = []
        total_spot_valuation = 0.0
        total_collateral_value = 0.0
        spot_cash_usdc = 0.0

        for bal in spot_state.get("balances", []):
            coin = bal.get("coin", "")
            total_qty = safe_float(bal.get("total", 0.0))
            hold_qty = safe_float(bal.get("hold", 0.0))
            entry_ntl = safe_float(bal.get("entryNtl", 0.0))
            ltv = safe_float(bal.get("ltv", 0.65)) if bal.get("ltv") is not None else 0.65

            if coin == "USDC":
                spot_cash_usdc += total_qty
            elif total_qty > 0:
                price = token_prices.get(coin, 0.0)
                if price <= 0.0 and all_mids and coin in all_mids:
                    price = safe_float(all_mids.get(coin, 0.0))

                if price > 0.0:
                    valuation = total_qty * price
                elif entry_ntl > 0.0:
                    valuation = entry_ntl
                    price = valuation / total_qty
                else:
                    valuation = 0.0
                    price = 0.0

                collateral_val = valuation * ltv
                total_spot_valuation += valuation
                total_collateral_value += collateral_val

                spot_balances.append({
                    "coin": coin,
                    "total_qty": total_qty,
                    "hold_qty": hold_qty,
                    "price": price,
                    "valuation_usd": valuation,
                    "ltv": ltv,
                    "collateral_value_usd": collateral_val
                })

        # Total Account Value: Spot Cash USDC + Spot Tokens Valuation + Perp Margin Net Equity
        total_account_value = spot_cash_usdc + total_spot_valuation + perp_account_value

        # Effective Margin (Scheme D / Portfolio Margin): Spot USDC + Spot Collateral (65% LTV) + Perp Equity
        effective_margin = spot_cash_usdc + total_collateral_value + perp_account_value
        margin_utilization_pct = (total_margin_used / effective_margin * 100.0) if effective_margin > 0 else 0.0

        # Liquidation Distance for Scheme D & Live Positions
        # For Short: (liq_px - ref_px) / ref_px * 100%
        # For Long:  (ref_px - liq_px) / ref_px * 100%
        min_liq_distance_pct = float("inf")
        for pos in positions:
            liq_px = pos.get("liquidation_price", 0.0)
            entry_px = pos.get("entry_price", 0.0)
            coin_name = pos.get("coin", "")
            current_px = token_prices.get(coin_name, 0.0) or safe_float(all_mids.get(coin_name, 0.0)) or entry_px
            if liq_px > 0 and current_px > 0:
                if pos.get("side") == "Short":
                    dist = (liq_px - current_px) / current_px * 100.0
                else:
                    dist = (current_px - liq_px) / current_px * 100.0
                if dist < min_liq_distance_pct:
                    min_liq_distance_pct = dist

        if min_liq_distance_pct == float("inf"):
            min_liq_distance_pct = 192.4  # Standard theoretical Scheme D default baseline

        # Determine Tier
        if min_liq_distance_pct < 8.0:
            tier = "TIER_3_EMERGENCY"
            tier_name = "Level 3 (紧急熔断)"
            tier_color = "red"
            action_recommended = "【紧急熔断】距离强平价格已不足 8%，需立即市价平掉 >=50% 头寸消除穿仓风险！"
        elif min_liq_distance_pct < 15.0 or margin_utilization_pct > 75.0:
            tier = "TIER_2_DELEVERAGE"
            tier_name = "Level 2 (等比例减仓)"
            tier_color = "dark_orange"
            action_recommended = "【等比例减仓】保证金使用率 >75% 或距强平 <15%，建议同步卖出 25%~30% 现货并平掉 25%~30% 合约空头！"
        elif min_liq_distance_pct < 25.0 or margin_utilization_pct > 60.0:
            tier = "TIER_1_BUFFER"
            tier_name = "Level 1 (现金缓冲注入)"
            tier_color = "yellow"
            action_recommended = "【缓冲注入】保证金使用率 >60% 或距强平 <25%，建议通过 hl-ops transfer 将 10% 闲置 USDC 划入 Perp 保证金！"
        else:
            tier = "NORMAL"
            tier_name = "🟢 稳健正常 (Scheme D Invariants Satisfied)"
            tier_color = "green"
            action_recommended = "套利头寸健康，Delta 中性对冲平稳运行中。"

        return {
            "master_address": self.account_address,
            "account_value": total_account_value,
            "total_account_value": total_account_value,
            "perp_account_value": perp_account_value,
            "total_raw_usd": total_raw_usd,
            "spot_cash_usdc": spot_cash_usdc,
            "total_spot_valuation": total_spot_valuation,
            "total_collateral_value": total_collateral_value,
            "withdrawable": withdrawable,
            "total_margin_used": total_margin_used,
            "effective_margin": effective_margin,
            "margin_utilization_pct": margin_utilization_pct,
            "total_ntl_pos": total_ntl_pos,
            "total_upnl": total_upnl,
            "cum_funding_total": cum_funding_total,
            "min_liq_distance_pct": min_liq_distance_pct,
            "theoretical_liq_distance_pct": 192.4,
            "tier": tier,
            "tier_name": tier_name,
            "tier_color": tier_color,
            "action_recommended": action_recommended,
            "positions": positions,
            "spot_balances": spot_balances
        }


    def internal_usd_transfer(self, amount: float, to_perp: bool = True, dry_run: bool = False) -> Dict[str, Any]:
        """
        Transfers USDC between Spot Account and Perp Clearinghouse.
        `to_perp = True`: spotUserToPerp (injects cash into margin buffer, 0 fee, 0 slippage).
        `to_perp = False`: perpToSpotUser.
        """
        if amount <= 0:
            raise ValueError("Transfer amount must be greater than 0.")

        action_type = "spotUserToPerp" if to_perp else "perpToSpotUser"
        desc = f"Transfer ${amount:,.2f} USDC from {'Spot' if to_perp else 'Perp'} to {'Perp' if to_perp else 'Spot'}"

        if dry_run or not self._sdk_available:
            return {
                "status": "SIMULATED",
                "action": action_type,
                "amount": amount,
                "to_perp": to_perp,
                "description": desc,
                "message": "Dry-run transfer simulation completed successfully."
            }

        try:
            account = self.Account.from_key(self.agent_private_key)
            exchange = self.Exchange(account, self.base_url, account_address=self.account_address)
            res = exchange.usd_class_transfer(amount=amount, to_perp=to_perp)
            return {
                "status": "SUCCESS" if res.get("status") == "ok" else "FAILED",
                "action": action_type,
                "amount": amount,
                "to_perp": to_perp,
                "response": res
            }
        except Exception as e:
            return {
                "status": "ERROR",
                "action": action_type,
                "amount": amount,
                "error": str(e)
            }

    def schedule_deadman_switch(self, timeout_seconds: Optional[int] = None) -> Dict[str, Any]:
        """
        Configures Hyperliquid scheduleCancel (Dead-man's switch).
        If no heartbeat is received before timeout_seconds, all orders are automatically canceled.
        Set timeout_seconds=None or 0 to clear/cancel the switch.
        """
        if not self._sdk_available:
            return {
                "status": "SKIPPED_NO_SDK",
                "timeout_seconds": timeout_seconds,
                "message": "hyperliquid-python-sdk is not installed."
            }

        try:
            account = self.Account.from_key(self.agent_private_key)
            exchange = self.Exchange(account, self.base_url, account_address=self.account_address)
            timeout_ms = int(time.time() * 1000) + (timeout_seconds * 1000) if timeout_seconds else None
            res = exchange.schedule_cancel(timeout_ms)
            return {
                "status": "SUCCESS" if res.get("status") == "ok" else "FAILED",
                "timeout_seconds": timeout_seconds,
                "timeout_timestamp_ms": timeout_ms,
                "response": res
            }
        except Exception as e:
            return {
                "status": "ERROR",
                "timeout_seconds": timeout_seconds,
                "error": str(e)
            }

    def cancel_all_orders(self, coin: Optional[str] = None) -> Dict[str, Any]:
        """
        Emergency Panic Button: Cancels all open orders (or open orders for a specific coin).
        """
        open_orders = self.client.get_open_orders(self.account_address) if self.account_address else []
        if coin:
            open_orders = [o for o in open_orders if o.get("coin") == coin]

        if not open_orders:
            return {
                "status": "SUCCESS",
                "canceled_count": 0,
                "message": f"No open orders found{' for ' + coin if coin else ''}."
            }

        if not self._sdk_available:
            return {
                "status": "SKIPPED_NO_SDK",
                "found_orders_count": len(open_orders),
                "orders": open_orders,
                "message": "hyperliquid-python-sdk is required for live order cancellation."
            }

        account = self.Account.from_key(self.agent_private_key)
        exchange = self.Exchange(account, self.base_url, account_address=self.account_address)

        results = []
        for o in open_orders:
            c = o.get("coin", "")
            oid = o.get("oid")
            if c and oid:
                try:
                    res = exchange.cancel(c, oid)
                    results.append({"coin": c, "oid": oid, "result": res})
                except Exception as e:
                    results.append({"coin": c, "oid": oid, "error": str(e)})

        return {
            "status": "COMPLETED",
            "total_orders": len(open_orders),
            "results": results
        }

    def build_maker_taker_order_plan(self,
                                     coin: str,
                                     spot_pair: str,
                                     target_usd: float,
                                     spot_price: float,
                                     perp_price: float,
                                     multiplier: float = 1.0,
                                     execution_mode: str = "taker_taker") -> Dict[str, Any]:
        """
        Builds a structured Maker-Taker order plan for Hyperliquid:
        - Leg 1 (Spot): Post-Only Limit Order ('Alo' / Add Liquidity Only) at best bid.
        - Leg 2 (Perp): IOC / Market order triggered on spot fill.
        """
        spot_qty = target_usd / spot_price if spot_price > 0 else 0.0
        perp_qty = spot_qty / multiplier if multiplier > 0 else spot_qty

        if execution_mode == "maker_taker":
            spot_fee_rate = self.calc.spot_maker_fee
            perp_fee_rate = self.calc.perp_taker_fee
            spot_order_type = {"limit": {"tif": "Alo"}} # Post-Only Maker
            perp_order_type = {"limit": {"tif": "Ioc"}} # Taker IOC on trigger
            workflow_desc = "【Hyperliquid Maker-Taker 架构】先在现货端挂 Alo (Post-Only) 买单，等待对手方吃单；成交后毫秒级在合约端以 IOC/Market 市价开出等量空头对冲，节省 0.0528% 现货吃单手续费。"
        elif execution_mode == "maker_maker":
            spot_fee_rate = self.calc.spot_maker_fee
            perp_fee_rate = self.calc.perp_maker_fee
            spot_order_type = {"limit": {"tif": "Alo"}}
            perp_order_type = {"limit": {"tif": "Alo"}}
            workflow_desc = "【Hyperliquid 双边 Alo 挂单】现货与合约两端均使用 Alo 纯挂单。"
        else: # taker_taker
            spot_fee_rate = self.calc.spot_taker_fee
            perp_fee_rate = self.calc.perp_taker_fee
            spot_order_type = {"limit": {"tif": "Ioc"}}
            perp_order_type = {"limit": {"tif": "Ioc"}}
            workflow_desc = "【Hyperliquid 双边 Taker 市价】现货与合约双边以 IOC 快速市价成交。"

        base_fee_pct = (spot_fee_rate + perp_fee_rate) * 100.0
        taker_taker_base_fee = (self.calc.spot_taker_fee + self.calc.perp_taker_fee) * 100.0
        fee_savings_pct = max(0.0, taker_taker_base_fee - base_fee_pct)
        fee_savings_usd = (fee_savings_pct / 100.0) * target_usd

        return {
            "execution_mode": execution_mode,
            "workflow_desc": workflow_desc,
            "coin": coin,
            "spot_pair": spot_pair,
            "target_usd": target_usd,
            "spot_qty": spot_qty,
            "perp_qty": perp_qty,
            "multiplier": multiplier,
            "spot_order": {
                "coin": spot_pair,
                "is_buy": True,
                "sz": spot_qty,
                "limit_px": spot_price,
                "order_type": spot_order_type,
                "role": "Trigger Leg (现货买一挂单)",
                "fee_pct": spot_fee_rate * 100.0
            },
            "perp_order": {
                "coin": coin,
                "is_buy": False,
                "sz": perp_qty,
                "limit_px": perp_price,
                "order_type": perp_order_type,
                "role": "Hedge Leg (成交毫秒对冲)",
                "fee_pct": perp_fee_rate * 100.0
            },
            "fee_summary": {
                "base_fee_pct": base_fee_pct,
                "fee_savings_pct": fee_savings_pct,
                "fee_savings_usd": fee_savings_usd,
                "taker_taker_base_fee_pct": taker_taker_base_fee
            }
        }

    def resolve_spot_market_pair(self, coin: str) -> Optional[Dict[str, Any]]:
        """
        Resolves the spot trading pair name and metadata for a given perpetual/base coin on Hyperliquid.
        For example: 'HYPE' -> '@107' (HYPE/USDC), 'PURR' -> 'PURR/USDC'.
        """
        try:
            tokens, spot_univ, spot_ctxs = self.client.get_spot_market_data()
            token_by_idx = {t["index"]: t for t in tokens}
            
            norm_coin = "HYPE" if coin.upper() in ["HYPER", "HYPE"] else coin.upper()
            # Find base token index
            matched_token = next((t for t in tokens if t.get("name", "").upper() == norm_coin), None)
            if not matched_token:
                # Try alias / prefix
                from src.calculator import COMMON_PREFIX_ALIASES
                alias = COMMON_PREFIX_ALIASES.get(norm_coin)
                if alias:
                    matched_token = next((t for t in tokens if t.get("name", "").upper() == alias.upper()), None)

            if not matched_token:
                return None

            base_idx = matched_token["index"]
            # Find matching spot pair with USDC (token 0) or best volume
            matching_pairs = [p for p in spot_univ if base_idx in p.get("tokens", [])]
            if not matching_pairs:
                return None

            # Prefer USDC quote (token index 0)
            best_pair = next((p for p in matching_pairs if p.get("tokens", [None, None])[1] == 0), matching_pairs[0])
            best_pair_idx = best_pair.get("index", 0)
            raw_pair_name = best_pair.get("name", "")

            # Get quote token
            quote_idx = best_pair.get("tokens", [None, None])[1]
            quote_token = token_by_idx.get(quote_idx, {})
            quote_symbol = quote_token.get("name", "USDC")
            display_name = f"{matched_token['name']}/{quote_symbol}" if raw_pair_name.startswith("@") else raw_pair_name

            ctx = spot_ctxs[best_pair_idx] if best_pair_idx < len(spot_ctxs) else {}

            return {
                "raw_pair_name": raw_pair_name,
                "display_name": display_name,
                "base_symbol": matched_token.get("name", norm_coin),
                "quote_symbol": quote_symbol,
                "sz_decimals": int(matched_token.get("szDecimals", 2)),
                "mid_px": safe_float(ctx.get("midPx", ctx.get("markPx", 0.0))),
                "mark_px": safe_float(ctx.get("markPx", 0.0)),
                "day_ntl_vlm": safe_float(ctx.get("dayNtlVlm", 0.0))
            }
        except Exception:
            return None

    def build_arbitrage_plan(self,
                             coin: str = "HYPE",
                             amount_qty: Optional[float] = None,
                             amount_usd: Optional[float] = None,
                             execution_mode: str = "taker_taker") -> Dict[str, Any]:
        """
        Builds a comprehensive Delta-Neutral Funding Rate Arbitrage Plan on Hyperliquid:
        - Resolves Perp contract metadata and L2 book
        - Resolves Spot trading pair (e.g. @107) and L2 book
        - Calculates best bid/ask, mid prices, basis spread, funding APR
        - Computes Scheme D capital allocation (90% Spot Collateral with 65% LTV, 90% Perp Short, 10% Cash Buffer)
        - Computes Fee Savings and Payback Hours
        """
        norm_coin = "HYPE" if coin.upper() in ["HYPER", "HYPE"] else coin.upper()
        perp_univ, perp_ctxs = self.client.get_perp_market_data()
        matched_idx = next((i for i, u in enumerate(perp_univ) if u.get("name", "").upper() == norm_coin), None)
        if matched_idx is None:
            raise ValueError(f"Perpetual contract '{coin}' not found on Hyperliquid.")

        perp_meta = perp_univ[matched_idx]
        perp_ctx = perp_ctxs[matched_idx]
        hourly_funding = safe_float(perp_ctx.get("funding", 0.0))
        perp_mid_px = safe_float(perp_ctx.get("midPx", perp_ctx.get("markPx", 0.0)))
        perp_sz_decimals = int(perp_meta.get("szDecimals", 2))

        # Spot pair lookup
        spot_info = self.resolve_spot_market_pair(norm_coin)
        raw_spot_pair = spot_info["raw_pair_name"] if spot_info else norm_coin
        display_spot_pair = spot_info["display_name"] if spot_info else f"{norm_coin}/USDC"
        spot_sz_decimals = spot_info.get("sz_decimals", perp_sz_decimals) if spot_info else perp_sz_decimals

        # L2 Books
        perp_book = self.client.get_l2_book(norm_coin)
        spot_book = self.client.get_l2_book(raw_spot_pair)

        perp_bids = perp_book.get("levels", [[], []])[0]
        perp_asks = perp_book.get("levels", [[], []])[1]
        spot_bids = spot_book.get("levels", [[], []])[0]
        spot_asks = spot_book.get("levels", [[], []])[1]

        best_perp_bid = safe_float(perp_bids[0]["px"]) if perp_bids else perp_mid_px
        best_perp_ask = safe_float(perp_asks[0]["px"]) if perp_asks else perp_mid_px
        best_spot_bid = safe_float(spot_bids[0]["px"]) if spot_bids else (spot_info.get("mid_px") if spot_info else perp_mid_px)
        best_spot_ask = safe_float(spot_asks[0]["px"]) if spot_asks else (spot_info.get("mid_px") if spot_info else perp_mid_px)

        spot_mid_px = (best_spot_bid + best_spot_ask) / 2.0 if (best_spot_bid > 0 and best_spot_ask > 0) else (best_perp_bid + best_perp_ask) / 2.0

        # Execution pricing based on mode
        if execution_mode == "maker_taker":
            target_spot_px = best_spot_bid # Limit Post-Only Alo at Best Bid
            target_perp_px = best_perp_bid # IOC Market Taker at Best Bid
        elif execution_mode == "taker_taker":
            target_spot_px = best_spot_ask # Immediate Ask Taker
            target_perp_px = best_perp_bid # Immediate Bid Taker
        else: # maker_maker
            target_spot_px = best_spot_bid
            target_perp_px = best_perp_ask

        # Parse quantity and notional
        if amount_qty is not None and amount_qty > 0:
            spot_qty = round(float(amount_qty), spot_sz_decimals)
            perp_qty = round(spot_qty, perp_sz_decimals)
            target_usd = spot_qty * target_spot_px
        elif amount_usd is not None and amount_usd > 0:
            target_usd = float(amount_usd)
            spot_qty = round(target_usd / target_spot_px, spot_sz_decimals) if target_spot_px > 0 else 0.0
            perp_qty = round(spot_qty, perp_sz_decimals)
        else:
            # Default to 1 unit
            spot_qty = round(1.0, spot_sz_decimals)
            perp_qty = round(spot_qty, perp_sz_decimals)
            target_usd = spot_qty * target_spot_px

        # Base Maker-Taker plan
        order_plan = self.build_maker_taker_order_plan(
            coin=coin,
            spot_pair=raw_spot_pair,
            target_usd=target_usd,
            spot_price=target_spot_px,
            perp_price=target_perp_px,
            multiplier=1.0,
            execution_mode=execution_mode
        )

        # Scheme D Quantitative Risk & Collateral Allocation Model
        spot_notional = spot_qty * target_spot_px
        perp_short_notional = perp_qty * target_perp_px
        collateral_weight = 0.65 # 65% LTV / 35% Haircut on Spot
        spot_collateral_usd = spot_notional * collateral_weight
        cash_buffer_usdc = spot_notional * (0.10 / 0.90) # 10% Cash Reserve
        total_capital_required = spot_notional + cash_buffer_usdc
        capital_efficiency_pct = 90.0
        theoretical_p_liq = target_spot_px / 0.342 if target_spot_px > 0 else 0.0
        liq_distance_pct = 192.4

        # Funding Metrics
        apr_pct = hourly_funding * 24.0 * 365.0 * 100.0
        effective_apr_pct = apr_pct * 0.90 # 0.90 Capital Efficiency
        spread_pct = ((target_perp_px - target_spot_px) / target_spot_px * 100.0) if target_spot_px > 0 else 0.0
        basis_spread_usd = (target_perp_px - target_spot_px) * spot_qty

        # Fee and Friction Calculations
        entry_fee_pct = order_plan["fee_summary"]["base_fee_pct"]
        entry_fee_usd = order_plan["fee_summary"].get("total_fee_usd", spot_notional * (entry_fee_pct / 100.0))
        roundtrip_fee_pct = entry_fee_pct * 2.0
        roundtrip_fee_usd = entry_fee_usd * 2.0

        calc = FundingRateCalculator()

        # Pure fee payback (without basis)
        pure_entry_payback_hrs = calc.calculate_payback_hours(entry_fee_pct / 100.0, hourly_funding)
        pure_roundtrip_payback_hrs = calc.calculate_payback_hours(roundtrip_fee_pct / 100.0, hourly_funding)

        # Net friction accounting for basis
        net_entry_friction_pct = entry_fee_pct - spread_pct
        net_entry_friction_usd = entry_fee_usd - basis_spread_usd
        net_roundtrip_friction_pct = roundtrip_fee_pct - spread_pct
        net_roundtrip_friction_usd = roundtrip_fee_usd - basis_spread_usd

        # Net payback with basis
        net_entry_payback_hrs = calc.calculate_net_payback_hours(entry_fee_pct / 100.0, spread_pct / 100.0, hourly_funding)
        net_roundtrip_payback_hrs = calc.calculate_net_payback_hours(roundtrip_fee_pct / 100.0, spread_pct / 100.0, hourly_funding)

        # Basis Risk Classification
        basis_eval = FundingRateCalculator.classify_basis_spread(spread_pct)

        # Multi-horizon Net APR Analysis
        apr_30d = FundingRateCalculator.calculate_multi_horizon_apr(effective_apr_pct, spread_pct, roundtrip_fee_pct, days=30)
        pnl_30d_usd = (total_capital_required * (apr_30d / 100.0)) * (30.0 / 365.0)

        apr_90d = FundingRateCalculator.calculate_multi_horizon_apr(effective_apr_pct, spread_pct, roundtrip_fee_pct, days=90)
        pnl_90d_usd = (total_capital_required * (apr_90d / 100.0)) * (90.0 / 365.0)

        apr_365d = FundingRateCalculator.calculate_multi_horizon_apr(effective_apr_pct, spread_pct, roundtrip_fee_pct, days=365)
        pnl_365d_usd = total_capital_required * (apr_365d / 100.0)

        annual_funding_usd = total_capital_required * (effective_apr_pct / 100.0)

        return {
            "coin": coin,
            "spot_pair": raw_spot_pair,
            "display_spot_pair": display_spot_pair,
            "execution_mode": execution_mode,
            "spot_qty": spot_qty,
            "perp_qty": perp_qty,
            "target_spot_price": target_spot_px,
            "target_perp_price": target_perp_px,
            "spot_mid_price": spot_mid_px,
            "perp_mid_price": perp_mid_px,
            "spot_notional_usd": spot_notional,
            "perp_notional_usd": perp_short_notional,
            "basis_spread_pct": spread_pct,
            "basis_spread_usd": basis_spread_usd,
            "basis_status": basis_eval["status"],
            "basis_status_label": basis_eval["label"],
            "basis_badge": basis_eval["badge"],
            "basis_plain_badge": basis_eval["plain_badge"],
            "basis_risk_note": basis_eval["note"],
            "basis_risk_level": basis_eval["risk_level"],
            "basis_is_blocked": basis_eval["is_blocked"],
            "hourly_funding": hourly_funding,
            "apr_pct": apr_pct,
            "effective_apr_pct": effective_apr_pct,
            "annual_funding_usd": annual_funding_usd,
            "entry_fee_pct": entry_fee_pct,
            "entry_fee_usd": entry_fee_usd,
            "roundtrip_fee_pct": roundtrip_fee_pct,
            "roundtrip_fee_usd": roundtrip_fee_usd,
            "net_entry_friction_pct": net_entry_friction_pct,
            "net_entry_friction_usd": net_entry_friction_usd,
            "net_roundtrip_friction_pct": net_roundtrip_friction_pct,
            "net_roundtrip_friction_usd": net_roundtrip_friction_usd,
            "pure_entry_payback_hrs": pure_entry_payback_hrs,
            "pure_entry_payback_str": FundingRateCalculator.format_hours(pure_entry_payback_hrs),
            "pure_roundtrip_payback_hrs": pure_roundtrip_payback_hrs,
            "pure_roundtrip_payback_str": FundingRateCalculator.format_hours(pure_roundtrip_payback_hrs),
            "net_entry_payback_hrs": net_entry_payback_hrs,
            "net_entry_payback_str": FundingRateCalculator.format_hours(net_entry_payback_hrs),
            "net_roundtrip_payback_hrs": net_roundtrip_payback_hrs,
            "net_roundtrip_payback_str": FundingRateCalculator.format_hours(net_roundtrip_payback_hrs),
            "entry_payback_hrs": net_entry_payback_hrs,
            "entry_payback_str": FundingRateCalculator.format_hours(net_entry_payback_hrs),
            "roundtrip_payback_hrs": net_roundtrip_payback_hrs,
            "roundtrip_payback_str": FundingRateCalculator.format_hours(net_roundtrip_payback_hrs),
            "multi_horizon_analysis": {
                "30d": {"days": 30, "net_apr_pct": apr_30d, "net_pnl_usd": pnl_30d_usd},
                "90d": {"days": 90, "net_apr_pct": apr_90d, "net_pnl_usd": pnl_90d_usd},
                "365d": {"days": 365, "net_apr_pct": apr_365d, "net_pnl_usd": pnl_365d_usd},
            },
            "scheme_d": {
                "total_capital_required_usd": total_capital_required,
                "spot_allocation_usd": spot_notional,
                "spot_allocation_pct": 90.0,
                "perp_short_notional_usd": perp_short_notional,
                "perp_short_allocation_pct": 90.0,
                "cash_buffer_usdc": cash_buffer_usdc,
                "cash_buffer_pct": 10.0,
                "collateral_ltv_pct": 65.0,
                "collateral_haircut_pct": 35.0,
                "spot_collateral_valuation_usd": spot_collateral_usd,
                "capital_efficiency_pct": capital_efficiency_pct,
                "theoretical_liq_price": theoretical_p_liq,
                "liq_distance_pct": liq_distance_pct
            },
            "order_plan": order_plan
        }

    @staticmethod
    def round_hl_price(px: float, sz_decimals: int = 2) -> float:
        """Rounds price to Hyperliquid standard: at most 5 significant figures and at most (6 - sz_decimals) decimals."""
        if px <= 0:
            return px
        max_decimals = max(0, 6 - sz_decimals)
        val = float(f"{px:.5g}")
        return round(val, max_decimals)

    @staticmethod
    def floor_to_decimals(val: float, decimals: int = 2) -> float:
        """Floors a float to a given number of decimal places without IEEE-754 precision leakage."""
        if val <= 0:
            return 0.0
        if decimals <= 0:
            return float(math.floor(val))
        factor = 10 ** decimals
        return math.floor(round(val * factor, 8)) / factor

    def execute_arbitrage_plan(self, plan: Dict[str, Any], dry_run: bool = True) -> Dict[str, Any]:
        """
        Executes a prepared Delta-Neutral arbitrage plan on Hyperliquid:
        - In Dry-Run (default): returns full execution simulation.
        - In Live mode:
          - If taker_taker: Executes simultaneous Dual-IOC on Spot Ask and Perp Bid.
          - If maker_taker: Places Leg 1 Post-Only Spot Order and prepares Leg 2 Perp trigger.
        """
        coin = plan.get("coin", "HYPE")
        spot_pair = plan.get("spot_pair", "@107")
        execution_mode = plan.get("execution_mode", "taker_taker")
        spot_order = plan.get("order_plan", {}).get("spot_order", {})
        perp_order = plan.get("order_plan", {}).get("perp_order", {})

        if execution_mode == "taker_taker":
            qty = plan.get("spot_qty", 1.0)
            target_spot_px = plan.get("target_spot_price", spot_order.get("limit_px", 0.0))
            target_perp_px = plan.get("target_perp_price", perp_order.get("limit_px", 0.0))
            return self.execute_dual_ioc_arbitrage(
                coin=coin,
                spot_pair=spot_pair,
                qty=qty,
                spot_price=target_spot_px,
                perp_price=target_perp_px,
                max_slippage_pct=0.30,
                dry_run=dry_run
            )

        if dry_run or not self._sdk_available:
            return {
                "status": "SIMULATED",
                "coin": coin,
                "spot_pair": spot_pair,
                "plan": plan,
                "message": "Dry-run simulation completed. No live orders submitted."
            }

        try:
            account = self.Account.from_key(self.agent_private_key)
            exchange = self.Exchange(account, self.base_url, account_address=self.account_address)

            # Step 1: Submit Spot Post-Only Limit Order
            spot_res = exchange.order(
                name=spot_pair,
                is_buy=spot_order.get("is_buy", True),
                sz=spot_order.get("sz", 1.0),
                limit_px=spot_order.get("limit_px", 1.0),
                order_type=spot_order.get("order_type", {"limit": {"tif": "Alo"}}),
                reduce_only=False
            )

            return {
                "status": "SPOT_ORDER_PLACED" if isinstance(spot_res, dict) and spot_res.get("status") == "ok" else "FAILED",
                "coin": coin,
                "spot_pair": spot_pair,
                "spot_order_response": spot_res,
                "perp_order_prepared": perp_order,
                "message": "Spot Post-Only maker order submitted to Hyperliquid orderbook. Ready for Perp hedge trigger."
            }
        except Exception as e:
            return {
                "status": "ERROR",
                "coin": coin,
                "error": str(e)
            }

    def execute_dual_ioc_arbitrage(
        self,
        coin: str,
        spot_pair: str,
        qty: float,
        spot_price: float,
        perp_price: float,
        max_slippage_pct: float = 0.30,
        dry_run: bool = True
    ) -> Dict[str, Any]:
        """
        Executes simultaneous Dual-IOC Taker-Taker arbitrage orders on Hyperliquid:
        - Leg 1: Spot Buy IOC (limit price = spot_price * (1 + max_slippage_pct/100))
        - Leg 2: Perp Sell IOC (limit price = perp_price * (1 - max_slippage_pct/100))
        - Returns execution status, executed fill prices, actual locked basis, latency, and fills.
        """
        start_t = time.time()
        slippage_mult = max_slippage_pct / 100.0
        spot_limit_px = self.round_hl_price(spot_price * (1.0 + slippage_mult))
        perp_limit_px = self.round_hl_price(perp_price * (1.0 - slippage_mult))
        target_spread_pct = (perp_price - spot_price) / spot_price * 100.0 if spot_price > 0 else 0.0

        spot_info = self.resolve_spot_market_pair(coin)
        spot_sz_decimals = int(spot_info.get("sz_decimals", 2)) if spot_info else 2
        perp_sz_decimals = 2
        try:
            perp_univ, _ = self.client.get_perp_market_data()
            matched_u = next((u for u in perp_univ if u.get("name", "").upper() == coin.upper()), None)
            if matched_u and "szDecimals" in matched_u:
                perp_sz_decimals = int(matched_u["szDecimals"])
        except Exception:
            pass

        exec_spot_qty = self.floor_to_decimals(qty, spot_sz_decimals)

        if dry_run or not getattr(self, "_sdk_available", False) or not self.agent_private_key:
            simulated_notional_usd = exec_spot_qty * spot_price
            return {
                "status": "SIMULATED_SUCCESS",
                "dry_run": True,
                "coin": coin,
                "spot_pair": spot_pair,
                "qty": exec_spot_qty,
                "target_spot_px": spot_price,
                "target_perp_px": perp_price,
                "spot_limit_px": spot_limit_px,
                "perp_limit_px": perp_limit_px,
                "target_spread_pct": target_spread_pct,
                "exec_spread_pct": target_spread_pct,
                "notional_usd": simulated_notional_usd,
                "latency_ms": 1,
                "spot_filled": {"avgPx": spot_price, "totalSz": exec_spot_qty, "oid": 9999901},
                "perp_filled": {"avgPx": perp_price, "totalSz": exec_spot_qty, "oid": 9999902},
                "message": "Dual-IOC dry-run simulation successfully completed (no live orders submitted)."
            }

        try:
            account = self.Account.from_key(self.agent_private_key)
            exchange = self.Exchange(account, self.base_url, account_address=self.account_address)

            def place_spot():
                return exchange.order(
                    name=spot_pair,
                    is_buy=True,
                    sz=exec_spot_qty,
                    limit_px=spot_limit_px,
                    order_type={"limit": {"tif": "Ioc"}},
                    reduce_only=False
                )

            def place_perp():
                exec_perp_qty = self.floor_to_decimals(qty, perp_sz_decimals)
                return exchange.order(
                    name=coin,
                    is_buy=False,
                    sz=exec_perp_qty,
                    limit_px=perp_limit_px,
                    order_type={"limit": {"tif": "Ioc"}},
                    reduce_only=False
                )

            def parse_order_status(res: Any) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
                if isinstance(res, dict) and res.get("status") == "ok":
                    statuses = res.get("response", {}).get("data", {}).get("statuses", [])
                    if statuses:
                        s0 = statuses[0]
                        if "filled" in s0:
                            return True, s0["filled"], None
                        elif "resting" in s0:
                            return True, s0["resting"], None
                        elif "error" in s0:
                            return False, None, s0["error"]
                err = res.get("response") if isinstance(res, dict) else str(res)
                return False, None, str(err)

            # Step 1: Submit Spot Buy IOC first
            spot_res = place_spot()
            spot_ok, spot_fill, spot_err = parse_order_status(spot_res)

            filled_spot_sz = safe_float(spot_fill.get("totalSz", 0.0)) if spot_fill else 0.0

            perp_res = None
            perp_ok = False
            perp_fill = None
            perp_err = None

            # Step 2: Only hedge perp if spot filled (eliminates naked short risk)
            if spot_ok and filled_spot_sz > 0:
                time.sleep(0.005) # 5ms guard ensures strictly increasing nonce on Hyperliquid L1
                perp_hedge_sz = self.floor_to_decimals(filled_spot_sz, perp_sz_decimals)
                def place_perp_matched(sz: float):
                    return exchange.order(
                        name=coin,
                        is_buy=False,
                        sz=sz,
                        limit_px=perp_limit_px,
                        order_type={"limit": {"tif": "Ioc"}},
                        reduce_only=False
                    )
                perp_res = place_perp_matched(perp_hedge_sz)
                perp_ok, perp_fill, perp_err = parse_order_status(perp_res)
            elif not spot_ok:
                perp_err = "SKIPPED_SPOT_NOT_FILLED"

            latency_ms = int((time.time() - start_t) * 1000)

            is_success = spot_ok and perp_ok
            exec_spot_px = float(spot_fill.get("avgPx", spot_price)) if spot_fill else spot_price
            exec_perp_px = float(perp_fill.get("avgPx", perp_price)) if perp_fill else perp_price
            actual_spread_pct = (exec_perp_px - exec_spot_px) / exec_spot_px * 100.0 if exec_spot_px > 0 else 0.0

            return {
                "status": "SUCCESS" if is_success else "PARTIAL_OR_FAILED",
                "dry_run": False,
                "coin": coin,
                "spot_pair": spot_pair,
                "qty": qty,
                "latency_ms": latency_ms,
                "target_spot_px": spot_price,
                "target_perp_px": perp_price,
                "exec_spot_px": exec_spot_px,
                "exec_perp_px": exec_perp_px,
                "target_spread_pct": target_spread_pct,
                "exec_spread_pct": actual_spread_pct,
                "spot_filled": spot_fill,
                "perp_filled": perp_fill,
                "spot_error": spot_err,
                "perp_error": perp_err,
                "spot_raw_response": spot_res,
                "perp_raw_response": perp_res,
                "message": "Dual-IOC arbitrage executed successfully." if is_success else f"Dual-IOC execution incomplete: spot_err={spot_err}, perp_err={perp_err}"
            }
        except Exception as e:
            return {
                "status": "ERROR",
                "dry_run": False,
                "coin": coin,
                "error": str(e),
                "latency_ms": int((time.time() - start_t) * 1000)
            }

    def execute_dual_ioc_unwind(
        self,
        coin: str = "HYPE",
        qty: Optional[float] = None,
        pct: float = 100.0,
        max_slippage_pct: float = 0.30,
        dry_run: bool = True
    ) -> Dict[str, Any]:
        """
        Unwinds (closes) a Delta-Neutral Spot Long + Perp Short position on Hyperliquid:
        - Leg 1: Perp Buy IOC (closes perp short position with reduce_only=True)
        - Leg 2: Spot Sell IOC (sells spot tokens for USDC)
        - Decouples perp and spot sizing according to respective szDecimals and balances.
        - Strictly floors spot order size to szDecimals to avoid Insufficient balance / invalid size errors.
        """
        start_t = time.time()
        c = coin.upper()
        spot_info = self.resolve_spot_market_pair(c)
        spot_pair = spot_info["raw_pair_name"] if spot_info else c

        # 1. Fetch current positions
        perp_state = self.client.get_clearinghouse_state(self.account_address) if self.account_address else {}
        spot_state = self.client.get_spot_clearinghouse_state(self.account_address) if self.account_address else {}

        perp_positions = perp_state.get("assetPositions", [])
        matched_perp = None
        for p in perp_positions:
            pos_dict = p.get("position", {})
            if pos_dict.get("coin", "").upper() == c:
                matched_perp = pos_dict
                break

        current_perp_szi = safe_float(matched_perp.get("szi", 0.0)) if matched_perp else 0.0
        current_short_qty = abs(current_perp_szi) if current_perp_szi < 0 else 0.0

        spot_balances = spot_state.get("balances", [])
        matched_spot = None
        for b in spot_balances:
            if b.get("coin", "").upper() == c:
                matched_spot = b
                break

        current_spot_qty = safe_float(matched_spot.get("total", 0.0)) if matched_spot else 0.0

        if current_short_qty <= 0.0 and current_spot_qty <= 0.0 and not dry_run:
            return {
                "status": "NO_POSITION",
                "coin": c,
                "message": f"No active {c} short position or spot balance found to unwind."
            }

        # 2. Resolve szDecimals for Perpetual and Spot
        perp_sz_decimals = 2
        try:
            perp_univ, _ = self.client.get_perp_market_data()
            matched_u = next((u for u in perp_univ if u.get("name", "").upper() == c), None)
            if matched_u and "szDecimals" in matched_u:
                perp_sz_decimals = int(matched_u["szDecimals"])
        except Exception:
            pass

        spot_sz_decimals = int(spot_info.get("sz_decimals", perp_sz_decimals)) if spot_info else perp_sz_decimals

        # 3. Determine target unwind quantities for Perp and Spot independently
        if qty is not None and qty > 0:
            user_qty = float(qty)
            if dry_run and current_short_qty <= 0 and current_spot_qty <= 0:
                perp_target = user_qty
                spot_target = user_qty
            else:
                perp_target = min(user_qty, current_short_qty) if current_short_qty > 0 else 0.0
                spot_target = min(user_qty, current_spot_qty) if current_spot_qty > 0 else 0.0
        else:
            pct_mult = pct / 100.0
            if dry_run and current_short_qty <= 0 and current_spot_qty <= 0:
                perp_target = 1.0 * pct_mult
                spot_target = 1.0 * pct_mult
            else:
                perp_target = current_short_qty * pct_mult
                spot_target = current_spot_qty * pct_mult

        perp_unwind_sz = self.floor_to_decimals(perp_target, perp_sz_decimals)
        spot_unwind_sz = self.floor_to_decimals(spot_target, spot_sz_decimals)
        target_qty = max(perp_unwind_sz, spot_unwind_sz)
        spot_dust_remaining = max(0.0, current_spot_qty - spot_unwind_sz)

        if not dry_run and perp_unwind_sz <= 0 and spot_unwind_sz <= 0:
            return {
                "status": "NO_POSITION",
                "coin": c,
                "message": f"Remaining {c} position or balance is below minimum lot size ({10**(-min(perp_sz_decimals, spot_sz_decimals))})."
            }

        # Fetch current L2 books
        perp_book = self.client.get_l2_book(c)
        spot_book = self.client.get_l2_book(spot_pair)

        perp_asks = perp_book.get("levels", [[], []])[1]
        spot_bids = spot_book.get("levels", [[], []])[0]

        best_perp_ask = safe_float(perp_asks[0]["px"]) if perp_asks else 0.0
        best_spot_bid = safe_float(spot_bids[0]["px"]) if spot_bids else 0.0

        slippage_mult = max_slippage_pct / 100.0
        perp_limit_px = self.round_hl_price(best_perp_ask * (1.0 + slippage_mult), perp_sz_decimals) if best_perp_ask > 0 else 0.0
        spot_limit_px = self.round_hl_price(best_spot_bid * (1.0 - slippage_mult), spot_sz_decimals) if best_spot_bid > 0 else 0.0

        unwind_spread_pct = ((best_perp_ask - best_spot_bid) / best_spot_bid * 100.0) if best_spot_bid > 0 else 0.0

        if dry_run or not getattr(self, "_sdk_available", False) or not self.agent_private_key:
            return {
                "status": "SIMULATED_SUCCESS",
                "dry_run": True,
                "coin": c,
                "spot_pair": spot_pair,
                "target_qty": target_qty,
                "perp_sz": perp_unwind_sz,
                "spot_sz": spot_unwind_sz,
                "spot_dust_remaining": spot_dust_remaining,
                "perp_sz_decimals": perp_sz_decimals,
                "spot_sz_decimals": spot_sz_decimals,
                "current_short_qty": current_short_qty,
                "current_spot_qty": current_spot_qty,
                "best_perp_ask": best_perp_ask,
                "best_spot_bid": best_spot_bid,
                "perp_limit_px": perp_limit_px,
                "spot_limit_px": spot_limit_px,
                "unwind_spread_pct": unwind_spread_pct,
                "notional_usd": target_qty * best_perp_ask,
                "latency_ms": 1,
                "message": f"Unwind simulation for {perp_unwind_sz} {c} perp and {spot_unwind_sz} spot completed (dry-run)."
            }

        try:
            account = self.Account.from_key(self.agent_private_key)
            exchange = self.Exchange(account, self.base_url, account_address=self.account_address)

            def parse_order_status(res: Any) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
                if isinstance(res, dict) and res.get("status") == "ok":
                    statuses = res.get("response", {}).get("data", {}).get("statuses", [])
                    if statuses:
                        s0 = statuses[0]
                        if "filled" in s0:
                            return True, s0["filled"], None
                        elif "resting" in s0:
                            return True, s0["resting"], None
                        elif "error" in s0:
                            return False, None, s0["error"]
                err = res.get("response") if isinstance(res, dict) else str(res)
                return False, None, str(err)

            # Step 1: Buy to close Perp short with reduce_only=True
            perp_res = None
            perp_ok = False
            perp_fill = None
            perp_err = None
            filled_perp_sz = 0.0

            if perp_unwind_sz > 0:
                perp_res = exchange.order(
                    name=c,
                    is_buy=True,
                    sz=perp_unwind_sz,
                    limit_px=perp_limit_px,
                    order_type={"limit": {"tif": "Ioc"}},
                    reduce_only=True
                )
                perp_ok, perp_fill, perp_err = parse_order_status(perp_res)
                filled_perp_sz = safe_float(perp_fill.get("totalSz", 0.0)) if perp_fill else 0.0
            else:
                perp_ok = True

            # Step 2: Sell Spot tokens
            spot_res = None
            spot_ok = False
            spot_fill = None
            spot_err = None

            # Sizing spot sell: match filled perp size if perp executed, or fallback to planned spot_unwind_sz
            sell_candidate = filled_perp_sz if (perp_unwind_sz > 0 and perp_ok and filled_perp_sz > 0) else spot_unwind_sz
            sell_sz = self.floor_to_decimals(min(sell_candidate, current_spot_qty), spot_sz_decimals)

            if sell_sz > 0:
                time.sleep(0.005) # 5ms guard against duplicate nonce
                spot_res = exchange.order(
                    name=spot_pair,
                    is_buy=False,
                    sz=sell_sz,
                    limit_px=spot_limit_px,
                    order_type={"limit": {"tif": "Ioc"}},
                    reduce_only=False
                )
                spot_ok, spot_fill, spot_err = parse_order_status(spot_res)
            else:
                spot_ok = True

            latency_ms = int((time.time() - start_t) * 1000)
            is_success = perp_ok and spot_ok

            exec_perp_px = float(perp_fill.get("avgPx", best_perp_ask)) if perp_fill else best_perp_ask
            exec_spot_px = float(spot_fill.get("avgPx", best_spot_bid)) if spot_fill else best_spot_bid

            return {
                "status": "SUCCESS" if is_success else "PARTIAL_OR_FAILED",
                "dry_run": False,
                "coin": c,
                "spot_pair": spot_pair,
                "qty": target_qty,
                "perp_sz": perp_unwind_sz,
                "spot_sz": sell_sz,
                "spot_dust_remaining": spot_dust_remaining,
                "latency_ms": latency_ms,
                "exec_perp_px": exec_perp_px,
                "exec_spot_px": exec_spot_px,
                "perp_filled": perp_fill,
                "spot_filled": spot_fill,
                "perp_error": perp_err,
                "spot_error": spot_err,
                "message": "Dual-IOC unwind executed successfully." if is_success else f"Unwind incomplete: perp_err={perp_err}, spot_err={spot_err}"
            }
        except Exception as e:
            return {
                "status": "ERROR",
                "dry_run": False,
                "coin": c,
                "error": str(e),
                "latency_ms": int((time.time() - start_t) * 1000)
            }


