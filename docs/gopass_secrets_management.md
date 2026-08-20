# Gopass 交易所 API 凭据安全管理与注入方案

使用 [gopass](https://www.gopass.pw/) 管理与加载交易所 API 凭据（特别是 **Hyperliquid 的 API Wallet 代理私钥与 Master 地址**、**Bybit 的 API Key/Secret**）是一种生产级的安全实践。

gopass 基于 GPG / Age 非对称加密，原生支持 Git 版本控制、多端同步以及多行键值对（Key-Value）元数据解析。它从根源上杜绝了在仓库中遗留明文 `.env` 导致私钥泄漏到代码仓库的安全隐患。

---

## 1. 核心设计原则 (Design Principles)

1. **磁盘零明文残留 (Zero Plain-text on Disk)**：
   - 绝不在项目根目录或磁盘中保留包含私钥的明文 `.env` 文件。
   - 所有凭据在内存中解密，并通过子进程环境变量直接注入，进程退出即销毁。
2. **最小权限原则 (Principle of Least Privilege)**：
   - **切勿将 Master 钱包的主私钥存入脚本或 gopass**！
   - 仅存储由 Master 在 Hyperliquid 链上授权生成的 **Agent Wallet (API Wallet) 私钥** 与 **Master 公共地址**。API Wallet 仅有交易与内部划转权限，无法发起提现。
3. **分层分网路径设计 (Namespaced Secrets Hierarchy)**：
   - 区分交易所、网络环境（Mainnet / Testnet）及子账户。

---

## 2. 凭据结构设计规范 (Secrets Schema)

gopass 支持以第一行为主私钥、后续行为 `Key: Value` 的多行 YAML/MIME 格式存储。推荐在 gopass 中建立以下层级结构：

```text
gopass/
└── trading/
    ├── hyperliquid/
    │   ├── mainnet
    │   └── testnet
    └── bybit/
        ├── mainnet
        └── subaccount-01
```

### 2.1 Hyperliquid 凭据模板 (`trading/hyperliquid/mainnet`)

> [!IMPORTANT]
> **Hyperliquid 最小权限规范**
> 只需存储 Master 公开地址（`HL_ACCOUNT_ADDRESS`）和 Agent 钱包私钥（`HL_AGENT_PRIVATE_KEY`）。

```yaml
0x<Agent_Wallet_Private_Key>
HL_ACCOUNT_ADDRESS: 0x<Master_Account_Address>
HL_AGENT_ADDRESS: 0x<Agent_Wallet_Address>
HL_AGENT_PRIVATE_KEY: 0x<Agent_Wallet_Private_Key>
HL_NETWORK: mainnet
```

### 2.2 Bybit 凭据模板 (`trading/bybit/mainnet`)

```yaml
<Bybit_API_Secret>
BYBIT_API_KEY: <your_bybit_api_key>
BYBIT_API_SECRET: <your_bybit_api_secret>
BYBIT_ACCOUNT_TYPE: UNIFIED
```

---

## 3. 凭据录入与管理操作 (SOP)

### 3.1 交互式多行录入 (推荐)

启动多行编辑器直接贴入上述模板：
```bash
# 录入 Hyperliquid 凭据
gopass insert -m trading/hyperliquid/mainnet

# 录入 Bybit 凭据
gopass insert -m trading/bybit/mainnet
```

### 3.2 管道化脚本录入 (非交互式示例)

```bash
cat << 'EOF' | gopass insert -f trading/hyperliquid/mainnet
0xcc20b6dddaf9df1fa8356d7483744fc420fbfcef1f6d41210e95560abd53f7cb
HL_ACCOUNT_ADDRESS: 0x87843f3E86B66bC369947aad95c7907cb49cDCB6
HL_AGENT_ADDRESS: 0x6464Ce6cCD3644a5dF9d4fac4aD7ba5486F1414d
HL_AGENT_PRIVATE_KEY: 0xcc20b6dddaf9df1fa8356d7483744fc420fbfcef1f6d41210e95560abd53f7cb
HL_NETWORK: mainnet
EOF
```

### 3.3 查看与编辑凭据

```bash
# 查看凭据详情 (含 Key-Value 元数据)
gopass show trading/hyperliquid/mainnet

# 编辑凭据
gopass edit trading/hyperliquid/mainnet
```

---

## 4. 仓库集成与执行方案 (Execution & Injection Workflow)

### 4.1 方案 A：原生 `--gopass` 参数自动加载 (最推荐，开箱即用)

本项目脚本已原生集成 gopass 解析器，直接传入 `--gopass` 参数或设置 `GOPASS_SECRET` 环境变量即可自动提取多行 Key-Value：

```bash
# 1. 运行 Hyperliquid 诊断与验活
python3 scripts/hl_ops.py check --gopass trading/hyperliquid/mainnet

# 2. 查询 Scheme D 仓位、质押折价与健康度大盘
python3 scripts/hl_ops.py status --gopass trading/hyperliquid/mainnet

# 3. 运行多交易所套利监控
python3 monitor.py --hl-check --gopass trading/hyperliquid/mainnet
```

也可以通过环境变量全局声明：
```bash
export GOPASS_SECRET="trading/hyperliquid/mainnet"
python3 scripts/hl_ops.py status
```

---

### 4.2 方案 B：Shell `eval` 一键环境注入

通过 Shell 命令将 gopass 中的 `Key: Value` 动态导出为当前命令的环境变量：

```bash
eval "$(gopass show -n trading/hyperliquid/mainnet | grep -E '^[A-Z0-9_]+:' | sed 's/: /=/g')" python3 scripts/hl_ops.py status
```

---

### 4.3 方案 C：使用 `gopass env` 的注意事项

> [!NOTE]
> **关于 `gopass env` 的工作机制**：
> - `gopass env <path>` 如果指向**单条 Secret 文件**，默认只会将第 1 行密码导出为大写变量（如 `MAINNET=0x...`），不会自动解析多行 `Key: Value`。
> - 若要使用原生的 `gopass env trading/hyperliquid/mainnet`，需将变量存为**目录结构**（即 `trading/hyperliquid/mainnet/` 目录下分别存放 `HL_ACCOUNT_ADDRESS` 和 `HL_AGENT_PRIVATE_KEY` 两个独立 secret）。

---

## 5. Shell 快捷别名配置 (可选)

为了日常运维更便捷，可在 `~/.zshrc` 或 `~/.bashrc` 中配置快捷 Alias：

```bash
# Hyperliquid 运维快捷别名 (使用原生 --gopass)
alias hl-ops='python3 scripts/hl_ops.py --gopass trading/hyperliquid/mainnet'
alias hl-mon='python3 monitor.py --gopass trading/hyperliquid/mainnet'

# 快捷使用示例
hl-ops check
hl-ops status
hl-ops orders
hl-ops cancel-all
hl-ops transfer --to perp --amount 1000
```

