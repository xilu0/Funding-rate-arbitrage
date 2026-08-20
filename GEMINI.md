# Capital Spot & Perp Funding Rate Arbitrage

A high-performance quantitative system for monitoring, analyzing, and executing delta-neutral Spot & Perpetual funding rate arbitrage across **Hyperliquid (DEX)** and **Bybit (CEX)**.

---

## 1. Architecture Overview

- **`src/hyperliquid_client.py`**: Client for Hyperliquid info API (`metaAndAssetCtxs`, `spotMetaAndAssetCtxs`, `l2Book`).
- **`src/bybit_client.py`**: Client for Bybit V5 Public/Private REST API (`tickers`, `funding/history`, `orderbook`, `order/create`).
- **`src/calculator.py`**: Mathematical models for:
  - Simple APR, compound APY, basis spread, taker fee payback duration (entry vs roundtrip).
  - Prefix multiplier parsing (e.g. `1000PEPE`, `1000000MOG`).
  - Historical funding rate stability metrics & negative funding penalty.
  - L2 orderbook depth capacity & estimated market impact / slippage.
- **`src/bybit_executor.py`**: Delta-neutral arbitrage builder with quantitative risk guards (slippage cap, payback cap, spread cap) and dry-run safety simulation.
- **`server.py`**: Lightweight REST API server built with Python standard library `http.server`, serving `/api/funding-rates`, `/api/bybit/funding-history`, `/api/depth-capacity`, `/api/bybit/build-arbitrage`, and static web assets.
- **`monitor.py`**: Terminal CLI interface with Rich formatting, live updating, and sorting/filtering.
- **`web/`**: Dashboard frontend (`index.html`, `app.js`, `style.css`).
- **`tests/`**: Unit test suite for clients, calculator, depth capacity, and executor.
- **`.agents/skills/`**: Domain skills (e.g., `altcoin-4v-research`).

---

## 2. Development & Testing Workflow

### Running Tests
Always use the built-in Python `unittest` runner:
```bash
python3 -m unittest discover -s tests
```
> Note: Do not assume `pytest` is installed in the local environment.

### Running CLI Monitor
```bash
# Hyperliquid & Bybit combined
python3 monitor.py --exchange all --limit 20

# Live refresh mode
python3 monitor.py --exchange bybit --live --interval 5
```

### Running Web Server
```bash
python3 server.py --port 8000
```

---

## 3. Engineering & Quantitative Invariants

### 3.1 Delta-Neutrality & Multipliers
- When constructing Spot Long + Perp Short positions, coin multipliers (e.g., `1000PEPEUSDT` has multiplier `1000.0`) must always scale the perpetual contract count:
  $$\text{Perp Contract Qty} = \frac{\text{Spot Token Qty}}{\text{Multiplier}}$$
- Spot and perpetual contract notionals must match within fractional lot size precision.

### 3.2 Quantitative Risk Guards
- **Dry-Run by Default**: All arbitrage construction tools and API endpoints must default to `dry_run = True`.
- **Slippage Ceiling**: Enforce `max_slippage_pct` (default $\le 0.50\%$) on combined spot ask and perp bid orderbooks.
- **Payback Time Ceiling**: Reject trades where roundtrip fee payback duration exceeds `max_payback_hours` (default $\le 72$ hours).
- **Basis Spread Guard**: Reject or warn when spot-perp price divergence exceeds allowable basis spread limits.

### 3.3 Dependencies & Code Quality
- **Lightweight Dependencies**: Maintain zero external framework dependency for the HTTP server (`http.server` only).
- **Type Annotations**: All public functions and methods must have Python type hints.
- **Robust Error Handling**: Handle API network timeouts, missing fields, rate limits, and non-200 responses gracefully without crashing server or monitor loops.
