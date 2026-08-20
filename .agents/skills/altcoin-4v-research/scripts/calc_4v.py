#!/usr/bin/env python3
"""
Altcoin 4-V Evaluation Helper Script
Calculates key metrics for Altcoin 4-V Research Skill.
"""

import sys

def evaluate_4v(mcap: float, fdv: float, tvl: float, dex_liquidity: float, top10_percent: float):
    print("==========================================")
    print("      ALTCOIN 4-V EVALUATION METRICS      ")
    print("==========================================")
    
    # 1. Vesting: FDV / MCAP
    fdv_mcap_ratio = fdv / mcap if mcap > 0 else float('inf')
    circulating_pct = (mcap / fdv) * 100 if fdv > 0 else 0
    print(f"[Vesting] FDV / MCAP Ratio: {fdv_mcap_ratio:.2f} (Circulating: {circulating_pct:.1f}%)")
    if fdv_mcap_ratio > 5.0:
        print("  ⚠️  WARNING / EXCLUDE: FDV/MCAP > 5.0 (High future unlocking pressure!)")
    elif fdv_mcap_ratio <= 2.5:
        print("  ✅ PASS: Safe FDV/MCAP ratio (<= 2.5)")
    else:
        print("  ⚡ NEUTRAL: Moderate FDV/MCAP ratio (2.5 < ratio <= 5.0)")
        
    # 2. Value: TVL / FDV
    tvl_fdv_ratio = (tvl / fdv) * 100 if fdv > 0 else 0
    print(f"\n[Value] TVL / FDV Ratio: {tvl_fdv_ratio:.2f}% (TVL: ${tvl:,.0f}, FDV: ${fdv:,.0f})")
    if tvl_fdv_ratio < 2.0:
        print("  ⚠️  WARNING: TVL efficiency is extremely low relative to FDV valuation.")
    else:
        print("  ✅ PASS: Healthy TVL vs Valuation ratio.")

    # 3. Volume & Depth: DEX Liquidity / MCAP
    liquidity_ratio = (dex_liquidity / mcap) * 100 if mcap > 0 else 0
    print(f"\n[Volume & Depth] Liquidity / MCAP Ratio: {liquidity_ratio:.2f}%")
    if liquidity_ratio < 2.0:
        print("  ⚠️  EXCLUDE: DEX Liquidity < 2% of MCAP! Extremely shallow depth.")
    else:
        print("  ✅ PASS: Liquidity ratio >= 2%.")

    # 4. Volume & Depth: Concentration
    print(f"\n[Volume & Depth] Top 10 Holder Concentration: {top10_percent:.1f}%")
    if top10_percent > 80.0:
        print("  ⚠️  EXCLUDE: Top 10 non-exchange addresses hold > 80% of supply!")
    else:
        print("  ✅ PASS: Token concentration <= 80%.")
    print("==========================================")

if __name__ == "__main__":
    if len(sys.argv) < 6:
        print("Usage: python3 calc_4v.py <MCAP> <FDV> <TVL> <DEX_LIQUIDITY> <TOP10_HOLD_PCT>")
        print("Example: python3 calc_4v.py 50000000 500000000 20000000 800000 85.5")
        sys.exit(1)
        
    mcap = float(sys.argv[1])
    fdv = float(sys.argv[2])
    tvl = float(sys.argv[3])
    dex_liq = float(sys.argv[4])
    top10 = float(sys.argv[5])
    
    evaluate_4v(mcap, fdv, tvl, dex_liq, top10)
