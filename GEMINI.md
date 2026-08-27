# Capital Spot & Perp Funding Rate Arbitrage

A high-performance quantitative system for monitoring, analyzing, and executing delta-neutral Spot & Perpetual funding rate arbitrage across **Hyperliquid (DEX)** and **Bybit (CEX)**.

---

## 1. Architecture Overview

- **`src/env.py`**: Zero-dependency environment loader with strict Twelve-Factor priority (`os.environ` / `gopass env` > `.env` decoy fallback).
- **`src/hyperliquid_client.py`**: Client for Hyperliquid info API (`metaAndAssetCtxs`, `spotMetaAndAssetCtxs`, `l2Book`, `clearinghouseState`, `spotClearinghouseState`, `openOrders`, `extraAgents`).
- **`src/hyperliquid_ws.py`**: High-performance real-time WebSocket client (`allMids`, `l2Book`, `activeAssetCtx`) with error logging, auto-reconnect, and sub-second basis spread calculation.
- **`src/hyperliquid_executor.py`**: Agent Wallet (API Wallet) executor, zero-risk canary testing, Scheme D health & liquidation distance evaluation, deadman switch, USD transfer, and emergency deleveraging.
- **`src/bybit_client.py`**: Client for Bybit V5 Public/Private REST API (`tickers`, `funding/history`, `orderbook`, `order/create`).
- **`src/calculator.py`**: Mathematical models for:
  - Simple APR, compound APY, basis spread, taker fee payback duration (entry vs roundtrip).
  - Prefix multiplier parsing (e.g. `1000PEPE`, `1000000MOG`).
  - Historical funding rate stability metrics & negative funding penalty.
  - L2 orderbook depth capacity & estimated market impact / slippage.
- **`src/bybit_executor.py`**: Delta-neutral arbitrage builder with quantitative risk guards (slippage cap, payback cap, spread cap) and dry-run safety simulation.
- **`src/telegram_notifier.py`**: Client for Telegram Bot API notifications with HTTP/SOCKS5 proxy support, Markdown formatting, and entity parsing fallback.
- **`src/telegram_alert_monitor.py`**: Quantitative background monitor auditing basis spread and funding rates, evaluating separated reference triggers, and dispatching actionable alerts.
- **`src/auto_arbitrage_engine.py`**: Strategy 2 automated basis-sniping engine with Scheme D dynamic capital sizing, anti-flicker persistence filtering, Dual-IOC taker execution, and Telegram reporting.
- **`src/basis_auditor.py`**: Reliable Structural Basis Auditor certifying Volume-Weighted executable spread (VWAP), 30s rolling persistence, P10 lowest floor, depth multiples, and R-Score matrix.
- **`src/version.py`**: Version management and runtime environment diagnostics (Python, Gopass, dependencies, virtualenv status).
- **`server.py`**: Lightweight REST API server built with Python standard library `http.server`, serving `/api/funding-rates`, `/api/funding-history` (Hyperliquid & Bybit), `/api/storage/summary`, `/api/depth-capacity`, `/api/bybit/build-arbitrage`, and static web assets.
- **`scripts/hl_ops.py`**: Production CLI tool for Hyperliquid API Wallet diagnostics, portfolio health monitoring, emergency panic cancel, USD transfers, and deleveraging.
- **`monitor.py`**: Terminal CLI interface with Rich formatting, live updating, and sorting/filtering.
- **`web/`**: Dashboard frontend (`index.html`, `app.js`, `style.css`).
- **`tests/`**: Unit test suite for clients, calculator, depth capacity, Bybit executor, and Hyperliquid ops.
- **`docs/hyperliquid_node_guide.md`**: Self-hosted Hyperliquid node (`hl2`) architecture, capability matrix, latency/freshness benchmarks, and ops runbook.
- **`.agents/skills/`**: Domain skills (e.g., `altcoin-4v-research`, `hyperliquid-collateral-arbitrage`).

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

### Running Web Server (默认 Tmux 会话: capital-monitor)
本项目默认在 tmux 会话 `capital-monitor` 中运行，严禁干扰 `cex-monitor`：
```bash
# 1. 检查或进入本项目会话
tmux attach -t capital-monitor

# 2. 会话内启动服务 (加载 .env 配置与策略二)
python3 server.py --port 8000

# 3. 平滑重启本项目服务 (向 capital-monitor 发送重载)
tmux send-keys -t capital-monitor C-c
sleep 1
tmux send-keys -t capital-monitor "python3 server.py --port 8000" C-m
```

### Hyperliquid API 运维与实盘操作 (需 Gopass 注入凭据)
```bash
# 1. 运行 API Wallet 诊断与金丝雀验活
gopass env trading/hyperliquid python3 scripts/hl_ops.py check

# 2. 查询账户健康状态与现货/合约仓位
gopass env trading/hyperliquid python3 scripts/hl_ops.py status

# 3. 提交实盘对冲建仓 (Taker-Taker 双边吃单)
gopass env trading/hyperliquid python3 scripts/hl_ops.py arbitrage --coin HYPE --qty 1 --force
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

### 3.4 Hyperliquid Default Arbitrage Architecture (Scheme D)
- **Account Mode & Eligibility Requirement**:
  - Requires **Portfolio Margin** mode (Standard/Unified modes do not support cross-asset collateralization for perps).
  - **Eligibility Gate**: Requires Account Value $\ge \$10,000$ OR Total/Weighted Trading Volume $\ge \$5,000,000$ (and $<\$25\text{M}$ during Beta).
- **Standard Capital Allocation (Scheme D)**:
  - 90% Spot Token Allocation (pledged as collateral with 65% LTV / collateral weight, i.e. 35% haircut).
  - 90% Perp Short Notional (Delta-neutral 1:1 hedge against spot holdings).
  - 10% Liquid USDC Cash Reserve (acts as liquidity shock absorber against borrow interest and sudden pumps).
  - Capital Efficiency: $90.0\%$ ($0.90 \times \text{Funding APR}$).
  - Liquidation Threshold: $P_{\text{liq}} \ge +192.4\%$ ($2.924 \times P_0$, nearly tripling without liquidation).
- **Entry Quality Guards**:
  - Filter: 7D Simple APR $\ge 20\%$ & Payback Hours $\le 48\text{h}$ & Basis Spread $\ge 0.0\%$ (strictly forbid entering at negative basis).
  - Minimum Horizon: $\ge 30$ days recommended to amortize roundtrip taker and slippage frictions.
- **Tiered Dynamic Deleveraging & Risk Response**:
  - **Level 1 (Buffer Injection)**: Margin utilization $> 60\%$ or distance to liquidation $< 25\%$ $\to$ transfer the 10% idle USDC into Perp margin (0 transaction fees).
  - **Level 2 (Proportional Deleveraging)**: Margin utilization $> 75\%$ or distance to liquidation $< 15\%$ $\to$ simultaneously close $25\% \sim 30\%$ of Spot and Perp positions to release cash.
  - **Level 3 (Emergency Breaker)**: Distance to liquidation $< 8\%$ $\to$ emergency market unwind $\ge 50\%$ positions.
  - **Cool-down Period**: 12 hours cool-down before re-leveraging after deleveraging to prevent high-frequency churn.

### 3.5 Execution Paradigm (Default: Taker-Taker Dual IOC)
- **Standard Execution Paradigm (Default: Taker-Taker)**:
  - **Zero Adverse Selection / No Option Exposure**: Dual IOC / Market taker orders executed simultaneously on Spot Ask and Perp Bid after L2 depth pre-check.
  - **Deterministic Delta Neutrality**: Eliminates legging latency and toxic pick-off risk during rapid market drops.
  - **Slippage & Risk Guard**: Combined slippage pre-calculated and enforced $\le 0.50\%$.
- **Alternative Paradigm (Maker-Taker Trigger Hedge)**:
  - **Spot Leg**: Place Post-Only Maker limit orders (`Alo` on Hyperliquid / `PostOnly` on Bybit) at Best Bid.
  - **Perp Leg**: Trigger millisecond IOC/Market taker orders upon spot fill.
  - **Trade-off**: Lower fee friction but exposed to limit order adverse selection / toxic flow in high-volatility regimes.
- **Reference**: See [`docs/maker_taker_execution_architecture.md`](file:///home/dave/src/github/xiluo/capital-rate-arbitrage/docs/maker_taker_execution_architecture.md) and [`docs/hyperliquid_hype_delta_neutral_arbitrage.md`](file:///home/dave/src/github/xiluo/capital-rate-arbitrage/docs/hyperliquid_hype_delta_neutral_arbitrage.md).

### 3.6 凭据安全与 Gopass 原生注入铁律 (Twelve-Factor Invariant)
- **Gopass 注入第一优先级**：当前开发与运行环境使用 `gopass` 托管核心交易密钥。
  - Hyperliquid 凭据路径：`trading/hyperliquid/` (包含 `HL_AGENT_PRIVATE_KEY`)
  - Bybit 凭据路径：`trading/bybit/` (包含 `BYBIT_API_SECRET`)
- **严禁裸跑依赖 `.env` 私钥**：
  - 本地 `.env` 中的 `HL_AGENT_PRIVATE_KEY` 属于诱饵防误触假 Key（Decoy/Honeypot）。
  - Agent 在执行任何涉及账户签名、金丝雀验活（`hl_ops.py check`）、仓位划转或实盘下单的操作时，**必须强制前缀 `gopass env trading/hyperliquid`**。

### 3.7 双机自建节点接入优先级铁律 (Dual-Node Network Invariants)
- **成交推流第一优先级 (WebSocket Priority 1)**：
  - 实时成交（`user_fills`）订阅**必须优先接入 `hl1` 内网专线**：`ws://10.1.3.164:8000/ws`（同 VPC 延迟 $< 0.5\text{ms}$，极值领先官方 83ms）。
  - 官方 `wss://api.hyperliquid.xyz/ws` 仅作为全量盘口（`l2Book`）、卖单与断线自动兜底。
- **状态查询第一优先级 (Info API Priority 1)**：
  - 账户资产与持仓（`clearinghouseState`、`openOrders`）**必须优先查询 `hl2` 内网端口**：`http://10.1.3.165:3001/info`（响应 1.2ms，提速 23 倍且免 Rate Limit）。
  - 必须配合同步滞后守卫（`SyncLag <= 2.0s`），异常时无感回退官方 `https://api.hyperliquid.xyz/info`。
- **详细参考**：完整双机拓扑、报文契约、实测基准与 Runbook 参见 [`docs/hyperliquid_node_guide.md`](file:///home/dave/src/github/xiluo/capital-rate-arbitrage/docs/hyperliquid_node_guide.md)。

### 3.8 Tmux 会话管理与隔离铁律 (Tmux Session Invariants)
- **本项目默认会话 (`capital-monitor`)**：
  - 本项目（`capital-rate-arbitrage`）的 REST API 服务（`server.py`）、WebSocket 监听、Telegram 告警监控以及策略二自动化引擎，**必须默认且只能运行在 `capital-monitor` tmux session 中**。
  - 若需重载配置、重启服务或捕获控制台日志，必须唯一指定目标 `-t capital-monitor`。
- **生产隔离禁区 (`cex-monitor`)**：
  - 服务器上的 `cex-monitor` 会话承载着既有的生产交易监控，**严禁触碰、中断、重启或向其发送任何按键（`C-c` / `send-keys`）**。




