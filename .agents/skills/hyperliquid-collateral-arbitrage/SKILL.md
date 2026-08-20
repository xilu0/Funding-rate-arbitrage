---
name: hyperliquid-collateral-arbitrage
description: >-
  Hyperliquid 现货质押与合约对冲资金费率套利（方案 D）实操指南与量化风控规范。
  涵盖 50% 折价质押、10% USDC 现金缓冲、强平线量化推导、三级阶梯调仓与防频繁摩擦滞后回补策略。
---

# Hyperliquid Collateral Arbitrage (Scheme D)

本 Skill 指导在 Hyperliquid 上构建、监控和执行基于“现货质押 + 永续对冲”的 Delta-Neutral 资金费率套利方案。

---

## 1. 核心量化数学模型

### 1.1 资金分配与杠杆定义
- **总初始本金**：$C$ (USDC)
- **现货买入与质押**：投入 $0.90 \cdot C$ 买入现货 $Q = \frac{0.90 \cdot C}{P_0}$，全额转入质押抵押物（折价率 $\alpha = 0.50$）。
- **合约做空对冲**：以 $P_0$ 开出名义价值 $N = 0.90 \cdot C$ 的永续空头（Delta 绝对中性）。
- **闲置现金储备**：保留 $0.10 \cdot C$ 为流动性 USDC 缓冲池。
- **资金利用率 (Capital Efficiency)**：
  $$\text{Capital Efficiency} = \frac{N}{C} = \mathbf{90.0\%} \implies \text{实际收益} = 0.90 \times \text{Funding APR}$$

### 1.2 强平价格推导 ($P_{\text{liq}}$)
当价格上涨至 $P$ 时：
1. **现货抵押物估值（打 5 折）**：$V_{\text{collateral}}(P) = 0.50 \cdot Q \cdot P = 0.45 \cdot C \cdot \frac{P}{P_0}$
2. **合约空头未实现盈亏**：$\text{UPnL}_{\text{perp}}(P) = Q \cdot (P_0 - P) = 0.90 \cdot C - 0.90 \cdot C \cdot \frac{P}{P_0}$
3. **账户总有效保证金**：
   $$M(P) = 0.10 \cdot C + V_{\text{collateral}}(P) + \text{UPnL}_{\text{perp}}(P) = C \cdot \left(1.00 - 0.45 \cdot \frac{P}{P_0}\right)$$
4. **维持保证金需求（按 $\text{MMR} = 3\%$ 计算）**：
   $$\text{Req}(P) = \text{MMR} \times N(P) = 0.03 \times 0.90 \cdot C \cdot \frac{P}{P_0} = 0.027 \cdot C \cdot \frac{P}{P_0}$$

强平触发条件 $M(P) \le \text{Req}(P)$：
$$1.00 - 0.45 \cdot \frac{P}{P_0} \le 0.027 \cdot \frac{P}{P_0}$$
$$0.477 \cdot \frac{P}{P_0} \ge 1.00 \implies P_{\text{liq}} = \frac{P_0}{0.477} \approx \mathbf{2.096 \cdot P_0} \quad (\mathbf{+109.6\%})$$

---

## 2. 标准入场与建仓流程 (SOP)

### 2.1 入场前置过滤 (Pre-flight Checks)
1. **费率门槛**：
   - 7 天移动平均资金费率 $\ge 20\%$ Simple APR。
   - 进出场双边手续费回本周期（Payback Hours）$\le 48$ 小时。
2. **基差保护 (Basis Spread Guard)**：
   - $\text{Spread} = \frac{P_{\text{perp}} - P_{\text{spot}}}{P_{\text{spot}}} \ge 0.0\%$。
   - **严格禁止在负基差（合约折价）时建仓**，防止建仓即遭受基差亏损。
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
