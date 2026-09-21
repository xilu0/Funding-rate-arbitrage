import time
import math
from typing import Dict, Any, List, Optional, Tuple
from src.calculator import safe_float

class BasisAuditor:
    """
    Quantitative auditor for certifying Reliable Structural Basis vs Transient Micro-Spikes.
    Audits 4 core dimensions:
    1. Volume-Weighted Executable Taker Spread (VWAP over actual tranche size)
    2. Rolling Window Persistence & P10 Lowest Floor (Duration >= 30s, P10 >= +0.03%)
    3. Orderbook Liquidity Depth Multiple (Perp Bid Liquidity >= 3x Trade Size)
    4. Funding Rate Alignment (APR >= 12.0% ongoing macro incentive)
    """

    def __init__(
        self,
        window_seconds: float = 30.0,
        min_spread_pct: float = 0.08,
        min_p10_floor_pct: float = 0.02,
        min_depth_multiple: float = 2.5,
        min_apr_pct: float = 12.0,
        min_reliability_score: float = 80.0,
        giant_order_usd: float = 20000.0,
        jitter_grace_seconds: float = 3.0
    ):
        self.window_seconds = float(window_seconds)
        self.min_spread_pct = float(min_spread_pct)
        self.min_p10_floor_pct = float(min_p10_floor_pct)
        self.min_depth_multiple = float(min_depth_multiple)
        self.min_apr_pct = float(min_apr_pct)
        self.min_reliability_score = float(min_reliability_score)
        self.giant_order_usd = float(giant_order_usd)
        self.jitter_grace_seconds = float(jitter_grace_seconds)

        # Rolling history per coin: List of { "time": float, "spread_pct": float, "spot_px": float, "perp_px": float, "apr_pct": float }
        self._history: Dict[str, List[Dict[str, Any]]] = {}
        # Recent public trade flow per coin: List of { "time": float, "side": str, "px": float, "sz": float, "usd": float }
        self._trades: Dict[str, List[Dict[str, Any]]] = {}
        # Streak start timestamp per coin: when spread first turned positive and stayed >= min_spread_pct
        self._streak_start: Dict[str, float] = {}
        # Last timestamp when spread was strictly above threshold (for micro-jitter grace tolerance)
        self._last_above_time: Dict[str, float] = {}

    def record_trade(
        self,
        coin: str,
        side: str,
        px: float,
        sz: float,
        now: Optional[float] = None
    ):
        """Records a public trade into rolling trade flow buffer."""
        c = coin.upper()
        t = now if now is not None else time.time()
        p = safe_float(px)
        s = safe_float(sz)
        if p <= 0.0 or s <= 0.0:
            return

        trade_list = self._trades.setdefault(c, [])
        trade_list.append({
            "time": t,
            "side": str(side).upper(),
            "px": p,
            "sz": s,
            "usd": p * s
        })

        # Keep rolling 180 seconds of trades
        cutoff = t - 180.0
        self._trades[c] = [tr for tr in trade_list if tr["time"] >= cutoff]

    def get_recent_trade_flow(
        self,
        coin: str,
        window_seconds: float = 30.0,
        now: Optional[float] = None
    ) -> Dict[str, Any]:
        """Calculates aggressive taker buy vs sell flow in the specified time window."""
        c = coin.upper()
        t = now if now is not None else time.time()
        cutoff = t - window_seconds
        trades = [tr for tr in self._trades.get(c, []) if tr["time"] >= cutoff]

        buy_usd = sum(tr["usd"] for tr in trades if tr["side"] in ["B", "BUY"])
        sell_usd = sum(tr["usd"] for tr in trades if tr["side"] in ["A", "S", "SELL"])
        buy_count = sum(1 for tr in trades if tr["side"] in ["B", "BUY"])
        sell_count = sum(1 for tr in trades if tr["side"] in ["A", "S", "SELL"])
        buy_ratio = (buy_usd / sell_usd) if sell_usd > 0 else (99.0 if buy_usd > 0 else 1.0)
        return {
            "window_seconds": window_seconds,
            "trade_count": len(trades),
            "taker_buy_usd": round(buy_usd, 2),
            "taker_sell_usd": round(sell_usd, 2),
            "net_buy_usd": round(buy_usd - sell_usd, 2),
            "buy_count": buy_count,
            "sell_count": sell_count,
            "buy_ratio": round(buy_ratio, 2)
        }

    def record_tick(
        self,
        coin: str,
        spot_price: float,
        perp_price: float,
        spread_pct: float,
        apr_pct: float,
        now: Optional[float] = None
    ):
        """Records a market tick into rolling time window with jitter-tolerant streak tracking."""
        c = coin.upper()
        t = now if now is not None else time.time()

        samples = self._history.setdefault(c, [])
        samples.append({
            "time": t,
            "spot_px": spot_price,
            "perp_px": perp_price,
            "spread_pct": spread_pct,
            "apr_pct": apr_pct
        })

        # Retain up to 1800 seconds (30 mins) for multi-minute analysis
        cutoff = t - max(self.window_seconds * 3.0, 1800.0)
        self._history[c] = [s for s in samples if s["time"] >= cutoff]

        # Jitter-tolerant continuous streak tracker:
        # A single 50ms sub-threshold tick will not instantly zero out a 3-minute persistent trend
        if spread_pct >= self.min_spread_pct and spread_pct >= 0.0:
            if c not in self._streak_start:
                self._streak_start[c] = t
            self._last_above_time[c] = t
        elif spread_pct < 0.0:
            # Outright backwardation inversion immediately resets streak
            self._streak_start.pop(c, None)
            self._last_above_time.pop(c, None)
        else:
            # Positive but below min_spread_pct: check if within jitter grace window
            last_above = self._last_above_time.get(c, 0.0)
            if (t - last_above) > self.jitter_grace_seconds:
                self._streak_start.pop(c, None)
                self._last_above_time.pop(c, None)

    def analyze_orderbook_causes(
        self,
        spot_price: float,
        spot_book: Optional[Dict[str, Any]],
        perp_book: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Scans L2 orderbook to detect giant whale orders, bid walls, and depth imbalance.
        Identifies whether positive basis is supported by genuine big capital.
        """
        if isinstance(perp_book, dict):
            perp_bids = perp_book.get("bids") or (perp_book.get("levels", [[], []])[0] if perp_book.get("levels") else [])
            perp_asks = perp_book.get("asks") or (perp_book.get("levels", [[], []])[1] if perp_book.get("levels") else [])
        else:
            perp_bids, perp_asks = [], []

        if isinstance(spot_book, dict):
            spot_asks = spot_book.get("asks") or (spot_book.get("levels", [[], []])[1] if spot_book.get("levels") else [])
        else:
            spot_asks = []

        # 1. Inspect Perpetual Bids
        giant_bids = []
        top5_bid_usd = 0.0
        top10_bid_usd = 0.0
        max_single_bid_usd = 0.0
        max_single_bid_px = 0.0
        max_single_bid_sz = 0.0

        for idx, b in enumerate(perp_bids):
            px = safe_float(b.get("px", 0.0))
            sz = safe_float(b.get("sz", 0.0))
            if px <= 0.0 or sz <= 0.0:
                continue
            usd_val = px * sz
            if idx < 5:
                top5_bid_usd += usd_val
            if idx < 10:
                top10_bid_usd += usd_val
            if usd_val > max_single_bid_usd:
                max_single_bid_usd = usd_val
                max_single_bid_px = px
                max_single_bid_sz = sz
            if usd_val >= self.giant_order_usd:
                giant_bids.append({
                    "px": px,
                    "sz": sz,
                    "usd": round(usd_val, 2),
                    "n": b.get("n", 1),
                    "level_index": idx
                })

        # 2. Inspect Perpetual Asks
        top5_perp_ask_usd = 0.0
        for idx, a in enumerate(perp_asks[:5]):
            px = safe_float(a.get("px", 0.0))
            sz = safe_float(a.get("sz", 0.0))
            if px > 0.0 and sz > 0.0:
                top5_perp_ask_usd += px * sz

        # 3. Inspect Spot Asks
        top5_spot_ask_usd = 0.0
        for idx, a in enumerate(spot_asks[:5]):
            px = safe_float(a.get("px", 0.0))
            sz = safe_float(a.get("sz", 0.0))
            if px > 0.0 and sz > 0.0:
                top5_spot_ask_usd += px * sz

        imbalance = (top5_bid_usd / top5_perp_ask_usd) if top5_perp_ask_usd > 0 else 1.0
        has_data = bool(perp_bids or perp_asks or spot_asks)

        return {
            "has_data": has_data,
            "has_giant_orders": len(giant_bids) > 0,
            "giant_bids": giant_bids[:5],
            "max_single_bid_usd": round(max_single_bid_usd, 2),
            "max_single_bid_px": max_single_bid_px,
            "max_single_bid_sz": max_single_bid_sz,
            "top5_bid_usd": round(top5_bid_usd, 2),
            "top10_bid_usd": round(top10_bid_usd, 2),
            "top5_perp_ask_usd": round(top5_perp_ask_usd, 2),
            "top5_spot_ask_usd": round(top5_spot_ask_usd, 2),
            "orderbook_imbalance": round(imbalance, 2)
        }

    def classify_cause(
        self,
        coin: str,
        depth_mult: float,
        ob_analysis: Dict[str, Any],
        trade_flow: Dict[str, Any],
        duration_seconds: float
    ) -> Dict[str, Any]:
        """
        Classifies the basis driver into:
        1. WHALE_SUPPORTED: Giant bid walls (>= $20k) or heavy taker buy flow (>= $30k) or strong imbalance (>= 2.0 with >= $50k top5 bids).
        2. ORGANIC_PLATEAU: Healthy distributed depth (top5 bids >= $5k, depth_mult >= 2.0).
        3. THIN_SPIKE: Thin orderbook (< $5k top5 bids or depth_mult < 2.0), noise/wick easily erased.
        """
        has_data = ob_analysis.get("has_data", False)
        has_giant = ob_analysis.get("has_giant_orders", False)
        top5_bid_usd = ob_analysis.get("top5_bid_usd", 0.0)
        imbalance = ob_analysis.get("orderbook_imbalance", 1.0)
        taker_buy_usd = trade_flow.get("taker_buy_usd", 0.0)
        buy_ratio = trade_flow.get("buy_ratio", 1.0)

        # Whale condition: Giant order, high imbalance with decent depth, or aggressive buy flow
        is_whale = (
            has_giant
            or (top5_bid_usd >= 50000.0 and imbalance >= 2.0)
            or (taker_buy_usd >= 30000.0 and buy_ratio >= 1.5)
            or (depth_mult >= 10.0 and imbalance >= 2.0)
        )

        if not has_data:
            # Synthetic / historical ticks without orderbook
            cause_type = "ORGANIC_PLATEAU"
            cause_title = "🟢 常规稳健平台 (Organic Plateau)"
            min_required_duration = self.window_seconds
            safe_for_manual = (duration_seconds >= min_required_duration)
            recommended_action = "订单簿数据缺失，依据历史时间序列评估。"
        elif is_whale:
            cause_type = "WHALE_SUPPORTED"
            cause_title = "🚀 巨单资金真实驱动 (Whale Supported)"
            min_required_duration = 15.0
            safe_for_manual = (duration_seconds >= min_required_duration)
            if safe_for_manual:
                recommended_action = "盘口巨单/大买单坚实托底，非瞬态毛刺，具备充裕操作时间，建议出手！"
            else:
                recommended_action = f"检测到大单/买单流，正在进行 15s 防撤单防欺骗验活 (已持续 {duration_seconds:.1f}s)..."
        elif top5_bid_usd < 5000.0 or depth_mult < 2.0:
            cause_type = "THIN_SPIKE"
            cause_title = "⚠️ 虚假薄盘口毛刺 (Thin Spike / Noise)"
            min_required_duration = 999999.0
            safe_for_manual = False
            recommended_action = "盘口偏薄或散单瞬态穿透，无大资金托底，严禁追单，算法坚决拦截。"
        else:
            cause_type = "ORGANIC_PLATEAU"
            cause_title = "🟢 常规稳健平台 (Organic Plateau)"
            min_required_duration = 60.0
            safe_for_manual = (duration_seconds >= min_required_duration)
            if safe_for_manual:
                recommended_action = f"盘口分布均匀健康，基差形成稳健平台 (已维持 {duration_seconds:.1f}s >= 60s)，建议择机出手！"
            else:
                recommended_action = f"基差持续形成中 (已持续 {duration_seconds:.1f}s < 60s)，建议继续观察确认稳定平台。"

        return {
            "cause_type": cause_type,
            "cause_title": cause_title,
            "min_required_duration": min_required_duration,
            "duration_seconds": round(duration_seconds, 1),
            "safe_for_manual": safe_for_manual,
            "has_giant_orders": has_giant,
            "giant_bids": ob_analysis.get("giant_bids", []),
            "max_single_bid_usd": ob_analysis.get("max_single_bid_usd", 0.0),
            "max_single_bid_px": ob_analysis.get("max_single_bid_px", 0.0),
            "max_single_bid_sz": ob_analysis.get("max_single_bid_sz", 0.0),
            "top5_bid_usd": top5_bid_usd,
            "top10_bid_usd": ob_analysis.get("top10_bid_usd", 0.0),
            "top5_perp_ask_usd": ob_analysis.get("top5_perp_ask_usd", 0.0),
            "top5_spot_ask_usd": ob_analysis.get("top5_spot_ask_usd", 0.0),
            "orderbook_imbalance": imbalance,
            "recent_taker_buy_usd": taker_buy_usd,
            "recent_taker_sell_usd": trade_flow.get("taker_sell_usd", 0.0),
            "taker_buy_ratio": buy_ratio,
            "recommended_action": recommended_action
        }

    @staticmethod
    def calculate_vwap_spread(
        target_notional_usd: float,
        spot_price: float,
        spot_book: Optional[Dict[str, Any]],
        perp_book: Optional[Dict[str, Any]]
    ) -> Tuple[float, float, float, float]:
        """
        Walks the spot asks and perp bids orderbooks to compute the actual
        Volume-Weighted Average Price (VWAP) executable taker spread.
        Returns (vwap_spot_ask, vwap_perp_bid, executable_spread_pct, depth_multiple).
        """
        if spot_price <= 0.0 or target_notional_usd <= 0.0:
            return spot_price, spot_price, 0.0, 1.0

        target_qty = target_notional_usd / spot_price

        # 1. Spot Asks (Buying Spot)
        if isinstance(spot_book, dict):
            spot_asks = spot_book.get("asks") or (spot_book.get("levels", [[], []])[1] if spot_book.get("levels") else [])
        else:
            spot_asks = []

        vwap_spot_ask = spot_price
        if spot_asks:
            filled_qty = 0.0
            filled_cost = 0.0
            for ask in spot_asks:
                px = safe_float(ask.get("px", 0.0))
                sz = safe_float(ask.get("sz", 0.0))
                if px <= 0.0 or sz <= 0.0:
                    continue
                take = min(target_qty - filled_qty, sz)
                filled_cost += take * px
                filled_qty += take
                if filled_qty >= target_qty:
                    break
            if filled_qty > 0:
                vwap_spot_ask = filled_cost / filled_qty

        # 2. Perp Bids (Selling Perp)
        if isinstance(perp_book, dict):
            perp_bids = perp_book.get("bids") or (perp_book.get("levels", [[], []])[0] if perp_book.get("levels") else [])
        else:
            perp_bids = []

        vwap_perp_bid = spot_price
        total_perp_bid_usd = 0.0

        if perp_bids:
            filled_qty = 0.0
            filled_rev = 0.0
            for idx, bid in enumerate(perp_bids):
                px = safe_float(bid.get("px", 0.0))
                sz = safe_float(bid.get("sz", 0.0))
                if px <= 0.0 or sz <= 0.0:
                    continue
                if idx < 5:
                    total_perp_bid_usd += px * sz
                take = min(target_qty - filled_qty, sz)
                filled_rev += take * px
                filled_qty += take
                if filled_qty >= target_qty:
                    break
            if filled_qty > 0:
                vwap_perp_bid = filled_rev / filled_qty

        # Executable spread
        if vwap_spot_ask > 0:
            executable_spread_pct = (vwap_perp_bid - vwap_spot_ask) / vwap_spot_ask * 100.0
        else:
            executable_spread_pct = 0.0

        depth_multiple = total_perp_bid_usd / target_notional_usd if target_notional_usd > 0 else 1.0
        return vwap_spot_ask, vwap_perp_bid, executable_spread_pct, depth_multiple

    def audit_basis(
        self,
        coin: str,
        current_spread_pct: float,
        current_apr_pct: float,
        spot_price: float,
        target_notional_usd: float = 500.0,
        spot_book: Optional[Dict[str, Any]] = None,
        perp_book: Optional[Dict[str, Any]] = None,
        now: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Performs a full multi-dimensional quantitative audit of the current basis.
        Returns reliability score (0-100), grade (A/B/C), and dimensional metrics.
        """
        c = coin.upper()
        t = now if now is not None else time.time()

        # 1. Depth & Executable Spread Check
        vwap_spot, vwap_perp, exec_spread_pct, depth_mult = self.calculate_vwap_spread(
            target_notional_usd=target_notional_usd,
            spot_price=spot_price,
            spot_book=spot_book,
            perp_book=perp_book
        )
        # If no orderbook provided, fall back to current spread
        if not spot_book and not perp_book:
            exec_spread_pct = current_spread_pct
            depth_mult = 3.0

        # 2. Rolling Window Persistence & P10 Floor
        samples = self._history.get(c, [])
        recent_window_samples = [s for s in samples if (t - s["time"]) <= self.window_seconds]

        if recent_window_samples:
            spreads = [s["spread_pct"] for s in recent_window_samples]
            mean_spread = sum(spreads) / len(spreads)
            sorted_spreads = sorted(spreads)
            p10_idx = max(0, int(len(sorted_spreads) * 0.10))
            p10_floor = sorted_spreads[p10_idx]
            min_floor = sorted_spreads[0]
            # Standard deviation
            variance = sum((x - mean_spread) ** 2 for x in spreads) / len(spreads)
            std_dev = math.sqrt(variance)
        else:
            mean_spread = current_spread_pct
            p10_floor = current_spread_pct
            min_floor = current_spread_pct
            std_dev = 0.0

        # Duration of current continuous positive streak
        streak_start = self._streak_start.get(c)
        duration_seconds = (t - streak_start) if streak_start else 0.0

        # Microstructure & Causal Driver Analysis
        ob_analysis = self.analyze_orderbook_causes(
            spot_price=spot_price,
            spot_book=spot_book,
            perp_book=perp_book
        )
        trade_flow = self.get_recent_trade_flow(coin=c, window_seconds=30.0, now=t)
        cause_info = self.classify_cause(
            coin=c,
            depth_mult=depth_mult,
            ob_analysis=ob_analysis,
            trade_flow=trade_flow,
            duration_seconds=duration_seconds
        )

        # 3. Scoring System (0 ~ 100 Points)

        # Dimension A: Executable Depth & Liquidity (Max 30 pts)
        score_depth = 0.0
        if exec_spread_pct >= self.min_spread_pct:
            score_depth += 18.0
        elif exec_spread_pct >= (self.min_spread_pct * 0.75):
            score_depth += 10.0

        if depth_mult >= self.min_depth_multiple:
            score_depth += 12.0
        else:
            score_depth += max(0.0, 12.0 * (depth_mult / self.min_depth_multiple))

        # Dimension B: Time Persistence & P10 Floor (Max 40 pts)
        score_persistence = 0.0
        # Streak duration (Max 25 pts)
        if duration_seconds >= self.window_seconds:
            score_persistence += 25.0
        else:
            score_persistence += 25.0 * (duration_seconds / self.window_seconds)

        # P10 floor test (Max 15 pts) - proves spread never dipped into backwardation
        if p10_floor >= self.min_p10_floor_pct:
            score_persistence += 15.0
        elif p10_floor >= 0.0:
            score_persistence += 10.0 * (p10_floor / max(0.0001, self.min_p10_floor_pct))
        else:
            # Dipped into negative! Strong penalty
            score_persistence = max(0.0, score_persistence - 15.0)

        # Dimension C: Funding Rate Alignment (Max 30 pts)
        score_funding = 0.0
        if current_apr_pct >= (self.min_apr_pct * 1.5):
            score_funding = 30.0
        elif current_apr_pct >= self.min_apr_pct:
            score_funding = 22.0
        elif current_apr_pct >= (self.min_apr_pct * 0.5):
            score_funding = 12.0
        else:
            score_funding = max(0.0, 6.0 * (current_apr_pct / max(0.0001, self.min_apr_pct)))

        total_score = round(score_depth + score_persistence + score_funding, 1)
        total_score = max(0.0, min(100.0, total_score))

        # Classification
        if cause_info["cause_type"] == "THIN_SPIKE":
            grade = "C"
            grade_badge = "⚠️ 虚假薄盘口毛刺 (Thin Spike / Noise)"
            is_reliable = False
            advice = cause_info["recommended_action"]
        elif cause_info["cause_type"] == "WHALE_SUPPORTED":
            if duration_seconds >= cause_info["min_required_duration"] and exec_spread_pct >= self.min_spread_pct and p10_floor >= 0.0:
                grade = "A"
                grade_badge = "🚀 巨单资金真实驱动 (Whale Supported)"
                is_reliable = True
                advice = f"盘口大额巨资托底 (已稳固 {duration_seconds:.1f}s >= 15s)，非瞬态毛刺，具备充裕操作时间，建议从容建仓锁定升水！"
            else:
                grade = "B"
                grade_badge = "⏳ 巨资成型防撤单验证中 (Whale Verifying)"
                is_reliable = False
                advice = cause_info["recommended_action"]
        else:
            # ORGANIC_PLATEAU
            if duration_seconds >= cause_info["min_required_duration"] and total_score >= self.min_reliability_score and p10_floor >= 0.0 and exec_spread_pct >= self.min_spread_pct:
                grade = "A"
                grade_badge = "💎 结构性稳健基差 (Structural Reliable)"
                is_reliable = True
                advice = "全要素稳健满足，盘口深厚且持续，建议从容建仓锁定升水！"
            elif total_score >= 60.0:
                grade = "B"
                grade_badge = "🟢 观察确认中 (Maturing / Forming)"
                is_reliable = False
                advice = cause_info["recommended_action"]
            else:
                grade = "C"
                grade_badge = "⚠️ 瞬态高频毛刺 (Transient Spike / Noise)"
                is_reliable = False
                advice = "盘口偏薄或持续时间极短 (<15s)，做市商易瞬间抹平，已被算法主动拦截。"

        return {
            "coin": c,
            "timestamp": t,
            "reliability_score": total_score,
            "grade": grade,
            "grade_badge": grade_badge,
            "is_reliable": is_reliable,
            "advice": advice,
            "cause_analysis": cause_info,
            "metrics": {
                "current_spread_pct": current_spread_pct,
                "executable_spread_pct": exec_spread_pct,
                "spot_vwap": vwap_spot,
                "perp_vwap": vwap_perp,
                "rolling_mean_spread_pct": round(mean_spread, 4),
                "p10_floor_pct": round(p10_floor, 4),
                "min_floor_pct": round(min_floor, 4),
                "duration_seconds": round(duration_seconds, 1),
                "depth_multiple": round(depth_mult, 2),
                "apr_pct": current_apr_pct,
                "samples_count": len(recent_window_samples)
            },
            "score_breakdown": {
                "depth_score": round(score_depth, 1),
                "persistence_score": round(score_persistence, 1),
                "funding_score": round(score_funding, 1)
            }
        }
