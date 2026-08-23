# Gopass 凭据管理与环境变量注入方案 (Twelve-Factor 标准)

使用 [gopass](https://www.gopass.pw/) 管理与注入交易所关键敏感凭据（特别是 **Hyperliquid Agent Wallet 代理私钥**、**Bybit API Secret**）是一种生产级的安全实践。

本项目遵循 **Twelve-Factor App** 现代软件设计原则，实现了**代码与密件管理彻底解耦**：
1. **最小加密原则**：只在 gopass 中加密存储核心私钥，公开地址与网络配置由 `.env` 或环境统一声明；
2. **原生 `gopass env` 注入**：利用 gopass 原生能力将私钥动态注入为子进程环境变量，代码端零 subprocess 耦合；
3. **优先级保护机制**：系统环境变量（`os.environ` / `gopass env`）严格拥有最高优先级，覆盖 `.env` 配置；
4. **`.env` 诱饵 Key (Honeypot / Decoy)**：在本地 `.env` 中放置无效假私钥，既提供配置模板，又在代码仓库泄露或未授权执行时起到迷惑与熔断防误触保护。

---

## 1. 凭据存储结构规范 (Secrets Schema)

为了配合 `gopass env` 的目录扫描机制，推荐在 gopass 中按「交易所/币种/变量名」的单文件结构存储：

```text
gopass/
└── trading/
    ├── hyperliquid/
    │   └── HL_AGENT_PRIVATE_KEY       # 单条 secret 文件，内容仅为 0x... 私钥
    └── bybit/
        └── BYBIT_API_SECRET           # 单条 secret 文件，内容仅为 API Secret
```

### 1.1 录入 Hyperliquid Agent 私钥 (单行录入)

```bash
# 录入 Agent Wallet 代理私钥
gopass insert trading/hyperliquid/HL_AGENT_PRIVATE_KEY

# 或者管道化非交互式录入
echo "0xcc20b6dddaf9df1fa8356d7483744fc420fbfcef1f6d41210e95560abd53fccc" | gopass insert -f trading/hyperliquid/HL_AGENT_PRIVATE_KEY
```

### 1.2 录入 Bybit API 凭据

```bash
gopass insert trading/bybit/BYBIT_API_SECRET
```

---

## 2. 本地 `.env` 配置与诱饵防护 (Honeypot / Decoy)

在项目根目录的 `.env` 文件中，仅保留公开配置项与无效的假 Key 作为诱饵：

```bash
# .env 模板文件

# 1. 公开 Master 钱包地址与网络配置（非敏感信息）
HL_ACCOUNT_ADDRESS=0x87843f3E86B66bC369947aad95c7907cb49cDCB6
HL_AGENT_ADDRESS=0x6464Ce6cCD3644a5dF9d4fac4aD7ba5486F1414d
HL_NETWORK=mainnet

# 2. 诱饵假 Key (Honeypot / Decoy)
# 作用：
# a. 若未通过 gopass env 注入真实私钥，本地脚本会使用该假 Key 并在自检签名时立即报错，防止误操作真实资金；
# b. 防止开发环境 .env 意外泄漏导致真实私钥暴露。
HL_AGENT_PRIVATE_KEY=0x0000000000000000000000000000000000000000000000000000000000000000
```

---

## 3. 环境变量优先级规则 (Priority Invariant)

`src/env.py` 内部加载器保证了以下严格的加载优先级：

```
┌────────────────────────────────────────────────────────┐
│ 1. gopass env / Shell export 注入的进程级系统环境变量  │ (最高优先级)
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│ 2. 本地 .env 配置文件 (仅作为 Fallback 填充缺失项)     │ (最低优先级)
└────────────────────────────────────────────────────────┘
```

当使用 `gopass env` 运行脚本时，注入的真实 `HL_AGENT_PRIVATE_KEY` 会直接占据 `os.environ`，`.env` 中的假 Key 会被完全忽略。

---

## 4. 生产执行与常用命令 (CLI Workflow)

### 4.1 方案 A：原生 `gopass env` 目录注入 (推荐)

直接将 gopass 的目录路径传给 `gopass env`，它会自动将该目录下的所有文件名转换为对应的大写环境变量并注入子进程：

```bash
# 1. 运行 Hyperliquid 诊断与金丝雀验活
gopass env trading/hyperliquid python3 scripts/hl_ops.py check

# 2. 查询 Scheme D 仓位与健康度状态
gopass env trading/hyperliquid python3 scripts/hl_ops.py status

# 3. 运行多交易所费率监控 CLI
gopass env trading/hyperliquid python3 monitor.py --hl-check
```

### 4.2 方案 B：标准 Shell 子命令注入

无论 gopass 中 secret 存储路径为何，均可使用标准 Shell 语法单次赋值：

```bash
HL_AGENT_PRIVATE_KEY=$(gopass show -o trading/hyperliquid/HL_AGENT_PRIVATE_KEY) python3 scripts/hl_ops.py status
```

---

## 5. Shell 快捷别名配置 (Aliases SOP)

为了日常运维最高效，推荐在 `~/.zshrc` 或 `~/.bashrc` 中配置极简 Alias：

```bash
# ~/.zshrc or ~/.bashrc

# Hyperliquid 运维与监控快捷指令
alias hl-ops='gopass env trading/hyperliquid python3 scripts/hl_ops.py'
alias hl-mon='gopass env trading/hyperliquid python3 monitor.py'

# 重新加载配置
# source ~/.zshrc
```

配置后可直接像原生 CLI 一样操作：
```bash
hl-ops check
hl-ops status
hl-ops orders
hl-ops cancel-all
hl-ops transfer --to perp --amount 1000
```
