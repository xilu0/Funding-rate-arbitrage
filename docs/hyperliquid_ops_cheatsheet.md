# Hyperliquid API Wallet 运维命令速查手册 (Operations Cheatsheet)

本手册涵盖 Hyperliquid **API Wallet (Agent Wallet)** 的安全架构、验活 SOP、`scripts/hl_ops.py` 生产级运维命令集，以及方案 D（现货质押 + 永续对冲）阶梯风控指令规范。

---

## 1. 核心原理与安全边界 (Security Architecture)

```
┌──────────────────────────────────────────────────────────────────┐
│                   Master Account (主账户)                         │
│  • 拥有全部资产所有权与提现权限                                       │
│  • 发起 on-chain approveAgent 授权登记 Agent Wallet                │
└───────────────────────────────┬──────────────────────────────────┘
                                │ on-chain 授权
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│              Agent Wallet / API Wallet (代理交易钱包)              │
│  • 具备权限：下单、撤单、USD 内部划转、杠杆调整                        │
│  • 无提现权限 (No Withdrawal)：私钥即便泄露亦无法转出任何资产            │
│  • 运行原则：生产环境绝不在磁盘保存明文私钥，通过 Gopass / 内存注入     │
└──────────────────────────────────────────────────────────────────┘
```

1. **分离式权限架构**：
   - **主账户公钥地址 (`HL_ACCOUNT_ADDRESS` / `master_address`)**：用于查询账户权益、现货抵押品、持仓和已用保证金等所有 `/info` 请求。
   - **API 钱包私钥 (`HL_AGENT_PRIVATE_KEY` / `agent_private_key`)**：仅用于构造并签署交易动作（EIP-712 签名），直接提交至 `/exchange`。
2. **零提现风险 (No Withdrawal Permissions)**：
   - Agent Wallet 是 Hyperliquid 链上协议原生支持的代理机制，无法发起任何链上跨链提现或对外转账。
3. **凭据安全管理**：
   - 生产环境推荐使用 [gopass](file:///home/dave/src/github/xiluo/capital-rate-arbitrage/docs/gopass_secrets_management.md) 进行非对称加密存储与内存注入，杜绝明文 `.env` 落地。

---

## 2. API Wallet 验活 SOP (Verification SOP)

刚创建的 API Wallet 需通过两级自动化验活检测：

```
[1. 链上只读授权核验] ──► 查询 Master 链上 extraAgents 列表 ──► 确认 Agent 公钥已登记在册
                                      │ (Pass)
                                      ▼
[2. 零风险金丝雀挂撤单] ──► 构造远离盘口 90% 的 Post-Only 买单 ──► 提交获取 OID ──► 毫秒级即刻撤单
```

- **第一级：只读授权核验**：调用 `/info` 的 `extraAgents` 接口，核对当前 Agent 地址是否已在 Master 授权列表中。
- **第二级：零风险金丝雀挂撤单测试 (Canary Test)**：
  - 构造远离盘口 90% 的 Post-Only 限价单（如以现价 10% 挂单买入 PURR），**绝对不会意外成交**；
  - 签名提交至 `/exchange` 并获取 `oid`；
  - 毫秒级即刻调用 `cancelOrder` 撤单；
  - **判定标准**：两步均返回 `status: ok` 即 100% 验证了私钥签名、EIP-712 算法、网络连通性及交易所交互权限。

---

## 3. 运维命令快速参考 (`scripts/hl_ops.py`)

### 3.1 全局凭据配置与通用参数

支持原生 `gopass env` 动态注入、环境变量或 CLI 参数：

```bash
# 方式 A：gopass env 原生目录注入 (最推荐，12-Factor 标准)
gopass env trading/hyperliquid python3 scripts/hl_ops.py <subcommand>

# 方式 B：环境变量子 Shell 注入
HL_AGENT_PRIVATE_KEY=$(gopass show -o trading/hyperliquid/HL_AGENT_PRIVATE_KEY) python3 scripts/hl_ops.py <subcommand>

# 方式 C：CLI 参数显式传入
python3 scripts/hl_ops.py <subcommand> -a 0xMasterAddress... -k 0xAgentKey...
```

**通用全局选项**：
| 参数 | 缩写 | 说明 |
| :--- | :--- | :--- |
| `--account` | `-a` | Master 账户公开地址 (只读状态归属) |
| `--agent-key` | `-k` | Agent Wallet 私钥 (签名授权) |
| `--testnet` | - | 连接至 Hyperliquid Testnet 测试网 |
| `--json` | - | 输出原始 JSON 格式（便于脚本管道或程序解析） |
| `--version` | `-v` | 输出系统版本及 Python/Gopass/依赖环境诊断信息 |

---

### 3.2 命令集与实操速查

#### 1. 验活与自检诊断 (`check`)
验证 API Wallet 的链上授权状态及金丝雀安全挂撤单。
```bash
# 默认自检 (使用 PURR 标的测试)
python3 scripts/hl_ops.py check

# 指定金丝雀测试币种
python3 scripts/hl_ops.py check --coin PURR

# 通过 monitor.py 快捷入口执行自检
python3 monitor.py --hl-check
```

#### 2. 查看账户大盘与方案 D 阶梯风控 (`status` / `balance`)
输出账户总权益、有效保证金、现货 65% LTV (35% 折价) 质押估值、合约空头持仓、未实现盈亏、累计资金费及强平安全距离。
```bash
# 查看完整账户健康度与风控大盘
python3 scripts/hl_ops.py status

# 输出原始 JSON
python3 scripts/hl_ops.py status --json
```

#### 3. 活动挂单监控 (`orders`)
实时查看当前账户的所有未成交挂单（含 OID、币种、方向、限价值、挂单数量、挂单时间）。
```bash
python3 scripts/hl_ops.py orders
```

#### 4. 一键紧急撤单 (`cancel-all` / Panic Button)
紧急情况下撤销所有挂单，或撤销指定币种的挂单。
```bash
# 紧急一键撤销全账户所有活动挂单
python3 scripts/hl_ops.py cancel-all

# 仅撤销指定币种挂单
python3 scripts/hl_ops.py cancel-all --coin PURR
```

#### 5. 断线看门狗死人开关 (`deadman`)
设置 Hyperliquid 链上 Dead-man's Switch。如果在设定超时时间内未收到客户端心跳，交易所将自动撤销当前所有挂单。
```bash
# 激活 60 秒死人开关 (若 60s 内无心跳则自动全撤单)
python3 scripts/hl_ops.py deadman --timeout 60

# 取消/清除死人开关
python3 scripts/hl_ops.py deadman --cancel
```

#### 6. 内部 USDC 资金划转 (`transfer` / Level 1 缓冲注入)
在现货账户（Spot）与永续合约保证金（Perp Margin）之间即时划转 USDC。**0 交易手续费、0 冲击滑点**。
```bash
# 现货账户 -> 合约保证金 (Level 1 风险缓冲注入，拉大强平距离)
python3 scripts/hl_ops.py transfer --to perp --amount 1000

# 合约保证金 -> 现货账户 (资金回拨)
python3 scripts/hl_ops.py transfer --to spot --amount 1000

# 模拟试运行 (Dry-Run)
python3 scripts/hl_ops.py transfer --to perp --amount 1000 --dry-run
```

#### 7. 阶梯减仓与紧急熔断 (`deleverage`)
针对方案 D 风险梯队触发等比例同步平仓（现货卖出 + 合约空头买平），快速释放保证金流动性。
```bash
# Level 2: 减仓 25% (预警调仓)
python3 scripts/hl_ops.py deleverage --coin PURR --pct 25

# Level 3: 紧急熔断 50% 头寸
gopass env trading/hyperliquid python3 scripts/hl_ops.py deleverage --coin PURR --pct 50 --force
```

#### 8. 套利建仓演练与实盘执行 (`arbitrage`)
自动化构建 1:1 Delta 中性套利方案（现货质押 + 永续对冲），支持 Dual-IOC 极速对冲与风控矩阵计算。
```bash
# 演练建仓 1 个 HYPE (默认 Dry-Run，输出完整盘口、订单与风控数据)
python3 scripts/hl_ops.py arbitrage --coin HYPE --qty 1

# 实盘执行 1 个 HYPE (使用 Gopass 内存注入私钥)
gopass env trading/hyperliquid python3 scripts/hl_ops.py arbitrage --coin HYPE --qty 1 --force

# 按目标 USD 资金量建仓 (如 $1000 USD)
gopass env trading/hyperliquid python3 scripts/hl_ops.py arbitrage --coin HYPE --usd 1000 --force
```
> 详见实操手册：[docs/hyperliquid_hype_delta_neutral_arbitrage.md](file:///home/dave/src/github/xiluo/capital-rate-arbitrage/docs/hyperliquid_hype_delta_neutral_arbitrage.md)

#### 9. 双边市价平仓与变现 (`close` / `unwind`)
双边市价平仓锁定套利利润或释放资金（合约平空 + 现货卖出变现 USDC）：
```bash
# 演练平仓 1 个 HYPE (Dry-Run 模式，检查可平仓位与预估成交价)
python3 scripts/hl_ops.py close --coin HYPE --qty 1

# 实盘平仓指定数量 (如平仓 1 个 HYPE)
gopass env trading/hyperliquid python3 scripts/hl_ops.py close --coin HYPE --qty 1 --force

# 实盘按比例全平/清仓 (如 100% 全平)
gopass env trading/hyperliquid python3 scripts/hl_ops.py close --coin HYPE --pct 100 --force

# 实盘部分平仓 (如平仓 50%)
gopass env trading/hyperliquid python3 scripts/hl_ops.py close --coin HYPE --pct 50 --force
```

#### 10. 实时行情与基差费率查询 (`market` / `monitor.py`)
快速查看指定币种的盘口价格、实时基差、年化 APR/APY 与进出场回本测算：
```bash
# 查询单币种（如 HYPE）的实时基差与费率大盘
python3 scripts/hl_ops.py market --coin HYPE

# 全市场实时扫描（按资金费率排序，查看前 10 个高收益标的）
python3 monitor.py --exchange hyperliquid --limit 10

# 终端持续刷新模式 (每 5 秒刷新一次)
python3 monitor.py --exchange hyperliquid --live --interval 5
```

#### 11. 版本与环境诊断 (`version`)
输出当前量化系统版本、Python 环境、Gopass 状态及依赖诊断。
```bash
python3 scripts/hl_ops.py version
```

---

## 4. 方案 D 风险梯队与调仓标准 (Risk Framework)

| 梯队 | 触发条件 | 核心动作 | 交易损耗 | 冷却期规则 |
| :--- | :--- | :--- | :--- | :--- |
| **Normal 正常运行** | 保证金使用率 $\le 60\%$ 且 距强平 $\ge 25\%$ | 正常收取资金费，维持 90% 质押 + 90% 空头 | 0 | 持续监控 |
| **Level 1 (缓冲注入)** | 保证金使用率 $> 60\%$ 或 距强平 $< 25\%$ | 将现货保留的 10% USDC 划入 Perp 保证金 | **0 费率 / 0 滑点** | 即刻执行 |
| **Level 2 (等比减仓)** | 保证金使用率 $> 75\%$ 或 距强平 $< 15\%$ | 同步市价/IOC 卖出 $25\% \sim 30\%$ 现货并平掉 $25\% \sim 30\%$ 空头 | 产生 Taker 手续费与滑点 | **强制 12 小时** 冷静期，严禁频繁回补 |
| **Level 3 (紧急熔断)** | 距强平 $< 8\%$ | 紧急市价平掉 $\ge 50\%$ 头寸消除爆仓风险 | 紧急避险 | 需人工复盘后方可重启 |

> [!TIP]
> 方案 D 完整数学模型推导与回补策略详见：[.agents/skills/hyperliquid-collateral-arbitrage/SKILL.md](file:///home/dave/src/github/xiluo/capital-rate-arbitrage/.agents/skills/hyperliquid-collateral-arbitrage/SKILL.md)

---

## 5. Shell 快捷别名推荐 (Bash/Zsh Alias)

在 `~/.zshrc` 或 `~/.bashrc` 中添加以下配置，可大幅提升日常运维效率：

```bash
# Hyperliquid 运维快捷别名 (使用原生 gopass env 注入)
alias hl-ops='gopass env trading/hyperliquid python3 scripts/hl_ops.py'
alias hl-mon='gopass env trading/hyperliquid python3 monitor.py'

# 常用命令极速调用
alias hl-check='hl-ops check'
alias hl-status='hl-ops status'
alias hl-orders='hl-ops orders'
alias hl-panic='hl-ops cancel-all'
```

---

## 6. 测试与自动化验证

每次脚本或逻辑变更后，可运行完整自动化单元测试套件进行回归验证：

```bash
python3 -m unittest discover -s tests
```
