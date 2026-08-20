#!/usr/bin/env python3
"""
Hyperliquid Market Auditor & Data Registry
Fetches full spot & perpetual market listings from Hyperliquid API,
saves them to local data registry, and performs an in-depth audit
of delta-neutral spot-perp hedge pairs.
"""

import os
import sys
import json
import time
from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.hyperliquid_client import HyperliquidClient
from src.calculator import FundingRateCalculator, COMMON_PREFIX_ALIASES

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
OUTPUT_FILE = os.path.join(DATA_DIR, "hyperliquid_markets.json")


def fetch_and_save_market_data() -> Dict[str, Any]:
    os.makedirs(DATA_DIR, exist_ok=True)
    client = HyperliquidClient(timeout=15)

    print("[1/3] Fetching perpetual contract data from Hyperliquid...")
    p_univ, p_ctxs = client.get_perp_market_data()

    print("[2/3] Fetching spot market data from Hyperliquid...")
    s_tokens, s_univ, s_ctxs = client.get_spot_market_data()

    timestamp_utc = datetime.now(timezone.utc).isoformat()

    # Build structured perp list
    perps = []
    for i, u in enumerate(p_univ):
        ctx = p_ctxs[i] if i < len(p_ctxs) else {}
        perps.append({
            "name": u.get("name"),
            "szDecimals": u.get("szDecimals"),
            "maxLeverage": u.get("maxLeverage"),
            "onlyIsolated": u.get("onlyIsolated"),
            "markPx": ctx.get("markPx"),
            "midPx": ctx.get("midPx"),
            "oraclePx": ctx.get("oraclePx"),
            "funding": ctx.get("funding"),
            "openInterest": ctx.get("openInterest"),
            "dayNtlVlm": ctx.get("dayNtlVlm"),
            "dayBaseVlm": ctx.get("dayBaseVlm"),
            "prevDayPx": ctx.get("prevDayPx"),
            "premium": ctx.get("premium")
        })

    # Token lookup
    token_by_idx = {t["index"]: t for t in s_tokens}
    spot_ctx_by_coin = {c["coin"]: c for c in s_ctxs if isinstance(c, dict) and "coin" in c}

    # Build structured spot pairs list
    spot_pairs = []
    for i, pair in enumerate(s_univ):
        pair_name = pair.get("name", "")
        ctx = spot_ctx_by_coin.get(pair_name, {})
        base_idx, quote_idx = pair["tokens"][0], pair["tokens"][1]
        base_token = token_by_idx.get(base_idx, {})
        quote_token = token_by_idx.get(quote_idx, {})

        base_name = base_token.get("name", "")
        quote_name = quote_token.get("name", "")
        human_pair_name = f"{base_name}/{quote_name}" if pair_name.startswith("@") else pair_name

        spot_pairs.append({
            "pair_name": pair_name,
            "human_pair_name": human_pair_name,
            "index": pair.get("index"),
            "isCanonical": pair.get("isCanonical", False),
            "base_token": {
                "name": base_name,
                "fullName": base_token.get("fullName"),
                "index": base_token.get("index"),
                "tokenId": base_token.get("tokenId"),
                "szDecimals": base_token.get("szDecimals"),
                "weiDecimals": base_token.get("weiDecimals"),
                "isCanonical": base_token.get("isCanonical"),
                "evmContract": base_token.get("evmContract"),
                "deployerTradingFeeShare": base_token.get("deployerTradingFeeShare"),
            },
            "quote_token": {
                "name": quote_name,
                "fullName": quote_token.get("fullName"),
                "index": quote_token.get("index"),
                "tokenId": quote_token.get("tokenId"),
            },
            "markPx": ctx.get("markPx"),
            "midPx": ctx.get("midPx"),
            "prevDayPx": ctx.get("prevDayPx"),
            "dayNtlVlm": ctx.get("dayNtlVlm"),
            "dayBaseVlm": ctx.get("dayBaseVlm"),
            "circulatingSupply": ctx.get("circulatingSupply"),
            "totalSupply": ctx.get("totalSupply")
        })

    registry = {
        "updated_at_utc": timestamp_utc,
        "summary": {
            "total_perp_markets": len(perps),
            "total_spot_tokens": len(s_tokens),
            "total_spot_pairs": len(spot_pairs),
        },
        "perpetuals": perps,
        "spot_tokens": s_tokens,
        "spot_pairs": spot_pairs
    }

    print(f"[3/3] Saving data registry to {OUTPUT_FILE}...")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)

    print(f"✅ Data saved successfully! Total Perps: {len(perps)}, Total Spot Tokens: {len(s_tokens)}, Total Spot Pairs: {len(spot_pairs)}")
    return registry


def audit_markets(registry: Dict[str, Any]):
    print("\n" + "=" * 80)
    print("🔍 AUDITING HYPERLIQUID SPOT & PERPETUAL MATCHING COVERAGE")
    print("=" * 80)

    perps = {p["name"]: p for p in registry["perpetuals"]}
    spot_pairs = registry["spot_pairs"]

    print(f"\nAnalyzing {len(spot_pairs)} spot pairs against {len(perps)} perpetual contracts...\n")

    # Current matching logic in calculator
    calc = FundingRateCalculator(enable_aliases=True, max_spread_pct=100.0)
    current_results = calc.match_and_calculate(
        perp_universe=[{"name": p["name"]} for p in registry["perpetuals"]],
        perp_ctxs=[{
            "funding": p.get("funding", 0.0),
            "markPx": p.get("markPx", 0.0),
            "midPx": p.get("midPx", 0.0),
            "openInterest": p.get("openInterest", 0.0),
            "dayNtlVlm": p.get("dayNtlVlm", 0.0)
        } for p in registry["perpetuals"]],
        spot_tokens=registry["spot_tokens"],
        spot_universe=[{
            "tokens": [p["base_token"]["index"], p["quote_token"]["index"]],
            "name": p["pair_name"],
            "isCanonical": p["isCanonical"]
        } for p in spot_pairs],
        spot_ctxs=[{
            "coin": p["pair_name"],
            "midPx": p.get("midPx"),
            "markPx": p.get("markPx"),
            "dayNtlVlm": p.get("dayNtlVlm")
        } for p in spot_pairs]
    )

    current_matched_coins = {r["coin"]: r for r in current_results}
    print(f"Current System Matched Arbitrage Pairs ({len(current_matched_coins)}):")
    for r in current_results:
        print(f"  - Perp: {r['coin']:<10} | Spot: {r['spot_symbol']:<8} ({r['spot_pair']}) | Basis: {r['spread_pct']:+.2f}% | Hourly: {r['hourly_funding_pct']:+.4f}% | 24h Vol: ${r['spot_24h_volume']:,.0f}")

    print("\n" + "-" * 80)
    print("🔬 COMPREHENSIVE SCAN FOR POTENTIAL UNMATCHED CANDIDATES")
    print("-" * 80)

    # Check various matching patterns
    exact_matches = []
    unit_matches = []
    k_mult_matches = []
    suffix_matches = []
    name_collisions = []

    for sp in spot_pairs:
        base_name = sp["base_token"]["name"]
        full_name = sp["base_token"].get("fullName") or ""
        spot_px = float(sp.get("midPx") or sp.get("markPx") or 0.0)
        vol = float(sp.get("dayNtlVlm") or 0.0)

        # Pattern 1: Exact Base == Perp Name
        if base_name in perps:
            perp_px = float(perps[base_name].get("midPx") or perps[base_name].get("markPx") or 0.0)
            diff_pct = abs(perp_px - spot_px) / spot_px * 100.0 if spot_px > 0 else 999999
            item = {
                "type": "EXACT",
                "spot_base": base_name,
                "spot_pair": sp["human_pair_name"],
                "raw_pair": sp["pair_name"],
                "perp_coin": base_name,
                "spot_px": spot_px,
                "perp_px": perp_px,
                "diff_pct": diff_pct,
                "vol": vol,
                "fullName": full_name
            }
            if diff_pct <= 50.0:
                exact_matches.append(item)
            else:
                name_collisions.append(item)

        # Pattern 2: U-prefix Unit assets (e.g. UBTC -> BTC, UETH -> ETH, etc.)
        if base_name.startswith("U") and len(base_name) > 1 and base_name[1:] in perps:
            target_perp = base_name[1:]
            perp_px = float(perps[target_perp].get("midPx") or perps[target_perp].get("markPx") or 0.0)
            diff_pct = abs(perp_px - spot_px) / spot_px * 100.0 if spot_px > 0 else 999999
            item = {
                "type": "UNIT_U_PREFIX",
                "spot_base": base_name,
                "spot_pair": sp["human_pair_name"],
                "raw_pair": sp["pair_name"],
                "perp_coin": target_perp,
                "spot_px": spot_px,
                "perp_px": perp_px,
                "diff_pct": diff_pct,
                "vol": vol,
                "fullName": full_name
            }
            if diff_pct <= 50.0:
                unit_matches.append(item)
            else:
                name_collisions.append(item)

        # Pattern 3: k-multiplier contracts (e.g. spot PEPE -> perp kPEPE, BONK -> kBONK)
        k_perp = f"k{base_name}"
        if k_perp in perps:
            perp_px = float(perps[k_perp].get("midPx") or perps[k_perp].get("markPx") or 0.0)
            scaled_spot_px = spot_px * 1000.0
            diff_pct = abs(perp_px - scaled_spot_px) / scaled_spot_px * 100.0 if scaled_spot_px > 0 else 999999
            item = {
                "type": "K_MULTIPLIER (1000x)",
                "spot_base": base_name,
                "spot_pair": sp["human_pair_name"],
                "raw_pair": sp["pair_name"],
                "perp_coin": k_perp,
                "spot_px": spot_px,
                "scaled_spot_px": scaled_spot_px,
                "perp_px": perp_px,
                "diff_pct": diff_pct,
                "vol": vol,
                "fullName": full_name
            }
            if diff_pct <= 50.0:
                k_mult_matches.append(item)
            else:
                name_collisions.append(item)

        # Pattern 4: Suffix variations (0 / 1 / HL / etc.)
        for sfx in ["0", "1", "HL", "X"]:
            if base_name.endswith(sfx) and len(base_name) > len(sfx):
                stem = base_name[:-len(sfx)]
                if stem in perps:
                    perp_px = float(perps[stem].get("midPx") or perps[stem].get("markPx") or 0.0)
                    diff_pct = abs(perp_px - spot_px) / spot_px * 100.0 if spot_px > 0 else 999999
                    item = {
                        "type": f"SUFFIX_{sfx}",
                        "spot_base": base_name,
                        "spot_pair": sp["human_pair_name"],
                        "raw_pair": sp["pair_name"],
                        "perp_coin": stem,
                        "spot_px": spot_px,
                        "perp_px": perp_px,
                        "diff_pct": diff_pct,
                        "vol": vol,
                        "fullName": full_name
                    }
                    if diff_pct <= 50.0:
                        suffix_matches.append(item)

    print("\n[Category 1] Valid Exact Matches (Price Diff <= 50%):")
    for m in exact_matches:
        print(f"  ✓ Spot: {m['spot_base']:<10} ({m['spot_pair']}) -> Perp: {m['perp_coin']:<10} | Spot: ${m['spot_px']:<10.4f} | Perp: ${m['perp_px']:<10.4f} | Diff: {m['diff_pct']:.2f}% | Vol: ${m['vol']:,.0f} | Name: {m['fullName']}")

    print("\n[Category 2] Valid U-Prefix Unit Assets (Price Diff <= 50%):")
    for m in unit_matches:
        print(f"  ✓ Spot: {m['spot_base']:<10} ({m['spot_pair']}) -> Perp: {m['perp_coin']:<10} | Spot: ${m['spot_px']:<10.4f} | Perp: ${m['perp_px']:<10.4f} | Diff: {m['diff_pct']:.2f}% | Vol: ${m['vol']:,.0f} | Name: {m['fullName']}")

    print("\n[Category 3] Valid k-Multiplier 1,000x Contracts (Price Diff <= 50%):")
    for m in k_mult_matches:
        print(f"  ✓ Spot: {m['spot_base']:<10} ({m['spot_pair']}) -> Perp: {m['perp_coin']:<10} | Spot: ${m['spot_px']:<10.6f} (1000x: ${m['scaled_spot_px']:.4f}) | Perp: ${m['perp_px']:<10.4f} | Diff: {m['diff_pct']:.2f}% | Vol: ${m['vol']:,.0f} | Name: {m['fullName']}")

    print("\n[Category 4] Valid Suffix / Bridged Token Matches (Price Diff <= 50%):")
    for m in suffix_matches:
        print(f"  ✓ Spot: {m['spot_base']:<10} ({m['spot_pair']}) -> Perp: {m['perp_coin']:<10} | Spot: ${m['spot_px']:<10.4f} | Perp: ${m['perp_px']:<10.4f} | Diff: {m['diff_pct']:.2f}% | Vol: ${m['vol']:,.0f} | Name: {m['fullName']}")

    print("\n[Category 5] Name Collisions / Fake Meme Tokens (Rejected by Diff > 50%):")
    for m in name_collisions:
        print(f"  ⚠️ Spot: {m['spot_base']:<10} ({m['spot_pair']}) vs Perp: {m['perp_coin']:<10} | Spot: ${m['spot_px']:<10.6f} | Perp: ${m['perp_px']:<10.4f} | Diff: {m['diff_pct']:,.0f}% | Name: '{m['fullName']}' (Meme Collision)")

    print("\n" + "=" * 80)
    print("📊 SUMMARY & COVERAGE COMPARISON")
    print("=" * 80)

    all_legit_candidates = {}
    for m in exact_matches + unit_matches + suffix_matches:
        coin = m["perp_coin"]
        if coin not in all_legit_candidates or m["vol"] > all_legit_candidates[coin]["vol"]:
            all_legit_candidates[coin] = m

    for m in k_mult_matches:
        coin = m["perp_coin"]
        if coin not in all_legit_candidates or m["vol"] > all_legit_candidates[coin]["vol"]:
            all_legit_candidates[coin] = m

    print(f"Total legitimate hedging candidates on Hyperliquid: {len(all_legit_candidates)}")
    print(f"Currently captured in calculator: {len(current_matched_coins)}")

    missing = [c for c in all_legit_candidates if c not in current_matched_coins]
    print(f"\nMissing Candidates to be added to Calculator ({len(missing)}):")
    for c in missing:
        item = all_legit_candidates[c]
        print(f"  ➕ Perp: {c:<10} <-> Spot: {item['spot_base']:<10} ({item['spot_pair']}) | Type: {item['type']} | Vol: ${item['vol']:,.0f}")


if __name__ == "__main__":
    registry_data = fetch_and_save_market_data()
    audit_markets(registry_data)
