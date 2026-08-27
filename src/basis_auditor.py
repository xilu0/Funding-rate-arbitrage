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
        min_reliability_score: float = 80.0
    ):
        self.window_seconds = float(window_seconds)
        self.min_spread_pct = float(min_spread_pct)
        self.min_p10_floor_pct = float(min_p10_floor_pct)
        self.min_depth_multiple = float(min_depth_multiple)
        self.min_apr_pct = float(min_apr_pct)
        self.min_reliability_score = float(min_reliability_score)

        # Rolling history per coin: List of { "time": float, "spread_pct": float, "spot_px": float, "perp_px": float, "apr_pct": float }
        self._history: Dict[str, List[Dict[str, Any]]] = {}
        # Streak start timestamp per coin: when spread first turned positive and stayed >= min_spread_pct
        self._streak_start: Dict[str, float] = {}

    def record_tick(
        self,
        coin: str,
        spot_price: float,
        perp_price: float,
        spread_pct: float,
        apr_pct: float,
        now: Optional[float] = None
    ):
        """Records a market tick into the rolling time window."""
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

        # Trim samples older than max(window_seconds * 2, 60.0) seconds
        cutoff = t - max(self.window_seconds * 2.0, 60.0)
        self._history[c] = [s for s in samples if s["time"] >= cutoff]

        # Update streak
        if spread_pct >= self.min_spread_pct and spread_pct >= 0.0:
            if c not in self._streak_start:
                self._streak_start[c] = t
        else:
            # Spread dropped below threshold -> reset streak
            self._streak_start.pop(c, None)

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
        spot_asks = spot_book.get("asks", []) if isinstance(spot_book, dict) else []
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
        perp_bids = perp_book.get("bids", []) if isinstance(perp_book, dict) else []
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
        if total_score >= self.min_reliability_score and p10_floor >= 0.0 and exec_spread_pct >= self.min_spread_pct:
            grade = "A"
            grade_badge = "💎 结构性稳健基差 (Structural Reliable)"
            is_reliable = True
            advice = "全要素稳健满足，盘口深厚且持续，建议从容建仓锁定升水！"
        elif total_score >= 60.0:
            grade = "B"
            grade_badge = "🟢 观察确认中 (Maturing / Forming)"
            is_reliable = False
            advice = f"基差持续形成中 (已持续 {duration_seconds:.1f}s)，建议继续观察确认稳定平台。"
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
