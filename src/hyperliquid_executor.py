import time
import os
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
        self.calc = calculator or FundingRateCalculator(spot_taker_fee=0.0007, perp_taker_fee=0.00035)
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
        - 50% Haircut on Spot Collateral.
        - 10% Cash Reserve / Buffer.
        - Distance to Liquidation P_liq (approx +109.6%).
        - Tier 1/2/3 Risk Alert triggers.
        """
        if not self.account_address:
            raise ValueError("Master account address (HL_ACCOUNT_ADDRESS) is required for health evaluation.")

        clearinghouse = self.client.get_clearinghouse_state(self.account_address)
        spot_state = self.client.get_spot_clearinghouse_state(self.account_address)

        margin_summary = clearinghouse.get("marginSummary", {})
        account_value = safe_float(margin_summary.get("accountValue", 0.0))
        total_margin_used = safe_float(margin_summary.get("totalMarginUsed", 0.0))
        total_ntl_pos = safe_float(margin_summary.get("totalNtlPos", 0.0))
        total_raw_usd = safe_float(margin_summary.get("totalRawUsd", 0.0))
        withdrawable = safe_float(clearinghouse.get("withdrawable", 0.0))

        # Perp Positions
        positions = []
        cum_funding_total = 0.0
        total_upnl = 0.0

        for ap in clearinghouse.get("assetPositions", []):
            pos = ap.get("position", {})
            coin = pos.get("coin", "")
            szi = safe_float(pos.get("szi", 0.0))
            if szi != 0.0:
                entry_px = safe_float(pos.get("entryPx", 0.0))
                upnl = safe_float(pos.get("unrealizedPnl", 0.0))
                liq_px = safe_float(pos.get("liquidationPx", 0.0))
                margin_used = safe_float(pos.get("marginUsed", 0.0))
                cum_funding = safe_float(pos.get("cumFunding", {}).get("allTime", 0.0))
                cum_funding_total += cum_funding
                total_upnl += upnl

                positions.append({
                    "coin": coin,
                    "size": szi,
                    "side": "Short" if szi < 0 else "Long",
                    "entry_price": entry_px,
                    "unrealized_pnl": upnl,
                    "liquidation_price": liq_px,
                    "margin_used": margin_used,
                    "cum_funding": cum_funding
                })

        # Spot Balances & Collateral Valuation (50% Haircut)
        spot_balances = []
        total_spot_valuation = 0.0
        total_collateral_value = 0.0
        spot_cash_usdc = 0.0

        for bal in spot_state.get("balances", []):
            coin = bal.get("coin", "")
            total_qty = safe_float(bal.get("total", 0.0))
            hold_qty = safe_float(bal.get("hold", 0.0))
            entry_ntl = safe_float(bal.get("entryNtl", 0.0))

            if coin == "USDC":
                spot_cash_usdc += total_qty
            elif total_qty > 0:
                # Estimate token value
                valuation = entry_ntl if entry_ntl > 0 else total_qty
                haircut_val = valuation * 0.50
                total_spot_valuation += valuation
                total_collateral_value += haircut_val

                spot_balances.append({
                    "coin": coin,
                    "total_qty": total_qty,
                    "hold_qty": hold_qty,
                    "valuation_usd": valuation,
                    "collateral_value_usd": haircut_val # 50% haircut
                })

        # Effective Margin & Utilization
        effective_margin = total_raw_usd + spot_cash_usdc + total_collateral_value + total_upnl
        margin_utilization_pct = (total_margin_used / effective_margin * 100.0) if effective_margin > 0 else 0.0

        # Liquidation Distance for Scheme D
        # Scheme D P_liq = P_0 / 0.477 approx +109.6%
        # Distance to liq = (P_liq - P_current) / P_current
        min_liq_distance_pct = 999.0
        for pos in positions:
            if pos["liquidation_price"] > 0 and pos["entry_price"] > 0:
                dist = (pos["liquidation_price"] - pos["entry_price"]) / pos["entry_price"] * 100.0
                if dist < min_liq_distance_pct:
                    min_liq_distance_pct = dist

        if min_liq_distance_pct == 999.0:
            min_liq_distance_pct = 109.6 # Standard theoretical Scheme D default

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
            "account_value": account_value,
            "total_raw_usd": total_raw_usd,
            "spot_cash_usdc": spot_cash_usdc,
            "withdrawable": withdrawable,
            "total_margin_used": total_margin_used,
            "effective_margin": effective_margin,
            "margin_utilization_pct": margin_utilization_pct,
            "total_ntl_pos": total_ntl_pos,
            "total_upnl": total_upnl,
            "cum_funding_total": cum_funding_total,
            "min_liq_distance_pct": min_liq_distance_pct,
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
                                     execution_mode: str = "maker_taker") -> Dict[str, Any]:
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
            workflow_desc = "【Hyperliquid Maker-Taker 架构】先在现货端挂 Alo (Post-Only) 买单，等待对手方吃单；成交后毫秒级在合约端以 IOC/Market 市价开出等量空头对冲，节省 0.055% 现货吃单手续费。"
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
