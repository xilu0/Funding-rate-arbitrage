---
name: hyperliquid-collateral-arbitrage
description: >-
  Hyperliquid 现货质押与合约对冲资金费率套利（方案 D）实操指南与量化风控规范。
  涵盖 65% LTV 质押折算率 (35% 折价)、10% USDC 现金缓冲、强平线量化推导 (+192.4%)、三级阶梯调仓与防频繁摩擦滞后回补策略。
---

# Hyperliquid Collateral Arbitrage (Scheme D)

本 Skill 指导在 Hyperliquid 上构建、监控和执行基于“现货质押 + 永续对冲”的 Delta-Neutral 资金费率套利方案。

---

## 1. 核心量化数学模型

### 1.1 资金分配与杠杆定义
- **总初始本金**：$C$ (USDC)
- **现货买入与质押**：投入 $0.90 \cdot C$ 买入现货 $Q = \frac{0.90 \cdot C}{P_0}$，全额转入质押抵押物（质押折算率 $\alpha = 0.65$ / LTV 65%，即 35% 折价）。
- **合约做空对冲**：以 $P_0$ 开出名义价值 $N = 0.90 \cdot C$ 的永续空头（Delta 绝对中性）。
- **闲置现金储备**：保留 $0.10 \cdot C$ 为流动性 USDC 缓冲池。
- **资金利用率 (Capital Efficiency)**：
  $$\text{Capital Efficiency} = \frac{N}{C} = \mathbf{90.0\%} \implies \text{实际收益} = 0.90 \times \text{Funding APR}$$

### 1.2 强平价格推导 ($P_{\text{liq}}$)
当价格上涨至 $P$ 时：
1. **现货抵押物估值（LTV 65% 折算）**：$V_{\text{collateral}}(P) = 0.65 \cdot Q \cdot P = 0.585 \cdot C \cdot \frac{P}{P_0}$
2. **合约空头未实现盈亏**：$\text{UPnL}_{\text{perp}}(P) = Q \cdot (P_0 - P) = 0.90 \cdot C - 0.90 \cdot C \cdot \frac{P}{P_0}$
3. **账户总有效保证金**：
   $$M(P) = 0.10 \cdot C + V_{\text{collateral}}(P) + \text{UPnL}_{\text{perp}}(P) = C \cdot \left(1.00 - 0.315 \cdot \frac{P}{P_0}\right)$$
4. **维持保证金需求（按 $\text{MMR} = 3\%$ 计算）**：
   $$\text{Req}(P) = \text{MMR} \times N(P) = 0.03 \times 0.90 \cdot C \cdot \frac{P}{P_0} = 0.027 \cdot C \cdot \frac{P}{P_0}$$

强平触发条件 $M(P) \le \text{Req}(P)$：
$$1.00 - 0.315 \cdot \frac{P}{P_0} \le 0.027 \cdot \frac{P}{P_0}$$
$$0.342 \cdot \frac{P}{P_0} \ge 1.00 \implies P_{\text{liq}} = \frac{P_0}{0.342} \approx \mathbf{2.924 \cdot P_0} \quad (\mathbf{+192.4\%})$$

### 1.3 基差与真实净回本周期模型 (Basis & Net Payback)
在现货做多 + 合约做空中，建仓基差 $\text{Spread} = \frac{P_{\text{perp}} - P_{\text{spot}}}{P_{\text{spot}}} \times 100\%$ 直接影响建仓摩擦：
- **净进场摩擦**：$\text{Net Entry Friction} = \text{Entry Fee Rate} - \text{Basis Spread}$
- **净双边摩擦**：$\text{Net Roundtrip Friction} = \text{Roundtrip Fee Rate} - \text{Basis Spread}$
- **真实净回本周期**：
  $$\text{Net Payback Hours} = \begin{cases} 0.0\text{h} \quad (\text{开仓即回本}), & \text{if } \text{Net Friction} \le 0 \\ \frac{\text{Net Friction}}{\text{Hourly Funding Rate}}, & \text{if } \text{Net Friction} > 0 \end{cases}$$

### 1.4 多持有期综合净实际年化 (Multi-Horizon Net Realized APR)
基差收益与手续费摩擦在开平仓时固定发生，年化收益随持有天数 $T_{\text{days}}$ 摊薄：
$$\text{Annualized Basis Yield}(T) = \text{Basis Spread \%} \times \frac{365}{T_{\text{days}}}$$
$$\text{Annualized Fee Friction}(T) = \text{Roundtrip Fee Rate \%} \times \frac{365}{T_{\text{days}}}$$
$$\text{Net Realized APR}(T) = \text{Scheme D Funding APR} + \text{Annualized Basis Yield}(T) - \text{Annualized Fee Friction}(T)$$

---

## 2. 标准入场与建仓流程 (SOP)

### 2.1 入场前置过滤 (Pre-flight Checks)
0. **账户模式与资格门槛 (Account Mode & Eligibility)**：
   - 必须激活 **Portfolio Margin（组合保证金模式）**（Unified / Standard 模式不支持跨币种现货质押）。
   - **准入条件**：账户总净值 $\ge \$10,000$ 或 历史总交易量 $\ge \$5,000,000$（Beta 阶段同时要求账户价值 $<\$25\text{M}$）。
1. **费率门槛**：
   - 7 天移动平均资金费率 $\ge 20\%$ Simple APR。
   - 进出场双边手续费回本周期（Payback Hours）$\le 48$ 小时。
2. **基差状态分级与准入铁律 (Basis Spread Guard)**：
   - $\text{Spread} \ge +0.05\%$：🟢 `[Contango Bonus 升水红利]`，增厚年化，缩短回本。
   - $0.00\% \le \text{Spread} < +0.05\%$：🟢 `[Fair Spread 基差平价]`，满足标准建仓准入。
   - $-0.05\% \le \text{Spread} < 0.00\%$：🟡 `[Drag Warning 轻微贴水]`，回本时间延长，警示关注。
   - $\text{Spread} < -0.05\%$：🔴 `[Severe Backwardation 严重贴水]`，**严格禁止建仓**（阻止提交实盘订单，建仓即承受确定性亏损）。
3. **订单簿深度评估**：
   - 检查 L2 盘口，单次建仓规模产生的综合滑点必须 $\le 0.15\%$。

### 2.2 TWAP 冰山分批建仓
- 将总目标资金拆分为 $5 \sim 10$ 个批次。
- 采用 Maker（挂单）或深度范围内的 IOC 限价单买入现货，并在成交后同步开出等量合约空头。
- 现货买入后自动转为抵押品，保留 $10\%$ USDC 现金于现货/保证金账户。

---

## 3. 三级阶梯调仓与防暴涨守护机制 (Tiered Risk Defense)

### 3.1 阶梯详情
- **Level 1 (现金缓冲注入)**：
  - **触发**：距强平价格 $< 25\%$ 或保证金使用率 $> 60\%$。
  - **动作**：将 10% 闲置 USDC 划入 Perp Margin，**不产生任何交易手续费与滑点**，迅速拉大安全距离。
- **Level 2 (等比例减仓变现)**：
  - **触发**：距强平价格 $< 15\%$ 或保证金使用率 $> 75\%$。
  - **动作**：按比例同时卖出 $25\% \sim 30\%$ 现货并平掉 $25\% \sim 30\%$ 合约空头。变现的 USDC 留在账户中，将有效杠杆拉低至 $1.2\times$ 附近。
- **Level 3 (紧急熔断)**：
  - **触发**：距强平价格 $< 8\%$。
  - **动作**：市价平掉 $\ge 50\%$ 头寸，消除穿仓风险。

### 3.2 冷静期与防过度交易磨损 (Hysteresis & Anti-churning)
- 在触发 Level 2 减仓后，**严禁在价格刚回落时立即加仓**。
- **强制冷静期**：至少等待 **12 小时**，且 1 小时 ATR 波动率回归常态、资金费率维持为正时，方可重新分批加仓。

---

## 4. 离场机制 (Exit Criteria)

- **费率衰减**：3 天移动平均资金费率 $< 5\%$ APR 或持续转负时，择机平仓离场。
- **平仓顺序**：分批以 Maker 挂单平仓，先平合约空头并同步卖出现货，回收 100% USDC 本息。
