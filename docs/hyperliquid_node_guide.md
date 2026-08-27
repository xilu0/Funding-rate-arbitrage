# Hyperliquid 自建非验证节点架构与量化接入指南 (hl1 & hl2)

本文档系统性梳理自建 Hyperliquid 主网非验证双节点（`hl1` 与 `hl2`，EC2 均位于 AWS 东京区域）的基础设施信息、API/RPC 接口支持矩阵、延迟与数据新鲜度实测基准、量化接入优先级规范及生产级运维指南。

---

## 1. 节点基础设施与双机拓扑

我们运维的两台 Hyperliquid 专用节点位于同一 AWS 物理可用区（`ap-northeast-1a`），构成分工协作的高可用节点集群：

### 1.1 双机硬件与部署对比

| 属性 | `hl1` (初号节点 / 核心推流) | `hl2` (新一代纯净节点 / 状态查询) |
| :--- | :--- | :--- |
| **EC2 实例名** | `xlink-unstable-hl-node-new` | `xlink-unstable-hl-node-new-2` |
| **SSH 快捷别名** | `ssh hl` | `ssh hl2` |
| **公网 IP (EIP)** | `54.168.206.59` | `57.182.181.166` |
| **VPC 私有内网 IP** | `10.1.3.164` | `10.1.3.165` |
| **AWS Region / AZ**| `ap-northeast-1a` (Tokyo) | `ap-northeast-1a` (Tokyo) |
| **硬件规格** | 32 vCPUs, 124 GB RAM | `r8i.4xlarge` (16 vCPUs, 128 GB RAM) |
| **数据盘存储** | 4 TB gp3 EBS (12000 IOPS / 600 MB/s) | 4 TB gp3 EBS (12000 IOPS / 600 MB/s) |
| **节点容器名** | `hyperliquid-node-1` | `node-node-1` |
| **专有扩展服务** | **`hypy-websocket-pusher` (端口 8000)**<br>`xlink-data-ingestion-dev` (Go 摄取) | **映射端口 3001** (`--serve-info` / `--serve-evm-rpc`)<br>映射端口 8545 (EVM JSON-RPC) |

### 1.2 端口与网络接入矩阵

| 端口 | 协议 | 所在主机 | 访问路径与权限 | 量化业务定位与优先级 |
| :--- | :--- | :--- | :--- | :--- |
| **`8000`** | **WebSocket** | **`hl1`** | **`ws://10.1.3.164:8000/ws`** (VPC 内网)<br>`ws://54.168.206.59:8000/ws` (公网) | 🌟 **实时成交推流第一优先级 (Priority 1)**：<br>实时广播 `node_fills` 成交数据，同 VPC 延迟 < 0.5ms |
| **`3001`** | **HTTP** | **`hl2`** | **`http://10.1.3.165:3001/info`** (VPC 内网) | 🌟 **状态查询第一优先级 (Priority 1)**：<br>查询持仓、净值与挂单，响应仅 1.2ms，零限频 |
| `8545` / `3001` | HTTP | `hl2` | `http://10.1.3.165:8545` 或 `/evm` | **EVM JSON-RPC**：标准以太坊 Web3 接口，块高与官方毫秒级对齐 |
| `8086` | HTTP | `hl1` / `hl2`| `http://<IP>:8086/metrics` | **Prometheus 指标库**：实时监控最新出块时间戳与 Peer 连接数 |
| `4000-4010`| TCP | `hl1` / `hl2`| `0.0.0.0/0` (公网开放) | **Gossip P2P 共识网络**：与全网验证人和种子节点同步区块与交易 |

---

## 2. 量化接口与接入优先级规范

针对量化套利策略，全链路接口采取 **“内网自建节点优先，官方公网自动兜底”** 的双轨设计原则：

### 2.1 行情与成交 WebSocket 优先级体系

```
[策略启动连接]
      │
      ├──► [首选 / 极速通道 (Priority 1)] ──► ws://10.1.3.164:8000/ws (hl1 内网专线)
      │                                       │ (若网络抖动、掉线或需要全量盘口)
      │                                       ▼
      └──► [兜底 / 全量通道 (Priority 2)] ──► wss://api.hyperliquid.xyz/ws (官方公网)
```

#### 优先使用通道：`ws://10.1.3.164:8000/ws` (`hl1`)
- **运行组件**：`xlink-hypercore-data-hypy-websocket-pusher-1`（基于 `cmd.node_tailer_websocket`）
- **核心机制**：在 `hl1` 本地以毫秒级持续 tail `/mnt/data1/hl/data/node_fills/hourly/`，一旦发现新成交立刻通过 WebSocket 异步广播。
- **协议格式**：
  ```json
  {
    "type": "user_fills",
    "data": [
      [
        0,              // 批次内索引 (int)
        "HYPE",         // 币种名称 (string)
        "82.277",       // 成交价格 (string/float)
        "0.12",         // 成交数量 (string/float)
        1787835169011,  // 撮合成交时间戳 (ms)
        "0x4ac9a0..."   // 交易哈希 (string)
      ]
    ],
    "timestamp": 1787835169015 // 消息构造推送时间戳 (ms)
  }
  ```
- **量化优势**：
  - 同一 AWS VPC 内网访问，网络 RTT 极低（$< 0.5\text{ms}$）；
  - 实测比官方 WebSocket 最快提前 **`83.42 ms`** 捕获成交；
  - 内部网络无连接数与带宽抢占限制。
- **业务注意事项**：
  - 该组件源码第 71 行设定为只推送买入方向成交（`if data["side"] == 'A': continue`）；
  - 若策略需要监听卖单成交、盘口深度（`l2Book`）或资金费率中间价（`allMids`），需保持连接官方 WebSocket 兜底频道。

---

### 2.2 状态查询 Info API 优先级体系

#### 优先使用通道：`http://10.1.3.165:3001/info` (`hl2`)
- **运行组件**：`hl-node run-non-validator --serve-info`
- **核心优势**：
  - 耗时分布：**`p50 = 1.23 ms`**（官方公网 28.13 ms），提速 **23 倍**；
  - **零 Rate Limit 风险**：内网直接打在本地内存状态机，不计入交易所 API 频次配额；
  - **数据 100% 强一致**：实测活跃交易用户持仓、净值与官方分毫不差（误差为 0）。
- **支持范围**：
  - ✅ 支持：`clearinghouseState`、`spotClearinghouseState`、`openOrders`、`exchangeStatus`、`meta`、`spotMeta`、`activeAssetData`。
  - ❌ 不支持（报 422 错误）：`allMids`、`metaAndAssetCtxs`、`fundingHistory`（此类全局汇总接口需走官方）。

---

## 3. 延迟与保真度实测基准 (Empirical Benchmarks)

所有测试均在东京机房同机/同 VPC 运行，利用系统纳秒单调时钟消除跨机时钟漂移：

### 3.1 成交流实测对比 (`ws://10.1.3.164:8000/ws` vs 官方 WebSocket)

在主网真实高峰期持续采样对齐测试结果：

| 指标 | 实测数值 | 说明 |
| :--- | :--- | :--- |
| **测试样本** | 68 笔同质买单成交 (BTC, ETH, SOL, HYPE) | 严格按 `(coin, px, sz, time)` 1:1 对齐 |
| **极值最快 (Min)** | **`-83.42 ms`** | `hl1` 的 WebSocket 比官方 WS **提前 83ms** 到达 |
| **P10 分位数** | **`-15.60 ms`** | 前 10% 优秀样本中，`hl1` 领先约 16ms |
| **中位数 (P50)** | **`+47.92 ms`** | 官方 WS 在综合中位数上占优约 48ms |
| **P90 分位数** | **`+261.83 ms`** | 受本地文件分批刷盘与 Python 内存缓冲抖动影响 |
| **hl1 胜出率** | **`20.6%`** (14/68 笔) | 在约 1/5 的成交中比官方 WebSocket 更快 |

### 3.2 状态查询实测对比 (`hl2:3001` vs 官方 API)

| 测试项 | 本地节点 (`hl2:3001`) | 官方公网 (`api.hyperliquid.xyz`) | 表现差异 |
| :--- | :--- | :--- | :--- |
| **`{"type": "meta"}` 延迟** | **`1.23 ms` (p50)** | `28.13 ms` (p50) | **本地快 23 倍** |
| **EVM 最新区块高度** | Block `44302155` | Block `44302155` | **高度差 = 0** |
| **撮合系统时间戳 (`exchangeStatus`)** | `1787832063991` | `1787832063991` | **误差 0 ms** |
| **活跃大户持仓净值 (`accountValue`)** | `54715.124962` | `54715.124962` | **误差 0.000000** |

---

## 4. 生产级量化双轨客户端代码示例

以下 Python 封装同时实现了 **`ws://10.1.3.164:8000/ws` 优先订阅**与 **`http://10.1.3.165:3001/info` 优先查询（附带滞后熔断守卫）：

```python
import time, json, urllib.request
from typing import Dict, Any, Callable

class HLDualNodeClient:
    """Hyperliquid 双机高可用自建节点量化客户端"""

    def __init__(
        self,
        # hl1: WebSocket 推流节点
        hl1_ws_url: str = "ws://10.1.3.164:8000/ws",
        official_ws_url: str = "wss://api.hyperliquid.xyz/ws",
        # hl2: 状态查询与 RPC 节点
        hl2_info_url: str = "http://10.1.3.165:3001/info",
        hl2_metrics_url: str = "http://10.1.3.165:8086/metrics",
        official_info_url: str = "https://api.hyperliquid.xyz/info",
        max_sync_lag: float = 2.0
    ):
        self.hl1_ws_url = hl1_ws_url
        self.official_ws_url = official_ws_url
        self.hl2_info_url = hl2_info_url
        self.hl2_metrics_url = hl2_metrics_url
        self.official_info_url = official_info_url
        self.max_sync_lag = max_sync_lag
        
        self._last_health_check = 0.0
        self._is_hl2_fresh = False

    def get_preferred_ws_url(self) -> str:
        """优先返回 hl1 内网 WebSocket 接入地址"""
        return self.hl1_ws_url

    def check_hl2_freshness(self) -> bool:
        """检查 hl2 节点状态是否在 2 秒安全同步窗口内"""
        now = time.time()
        if now - self._last_health_check < 1.0:
            return self._is_hl2_fresh

        try:
            req = urllib.request.Request(self.hl2_metrics_url)
            with urllib.request.urlopen(req, timeout=0.5) as resp:
                content = resp.read().decode("utf-8")

            block_time, peers = None, None
            for line in content.splitlines():
                if line.startswith("hl_core_latest_block_time "):
                    block_time = float(line.split()[1])
                elif line.startswith("hl_p2p_non_val_peers_total "):
                    peers = int(float(line.split()[1]))

            self._last_health_check = now
            if block_time and peers is not None:
                lag = now - block_time
                self._is_hl2_fresh = (lag <= self.max_sync_lag and peers >= 3)
            else:
                self._is_hl2_fresh = False
        except Exception:
            self._is_hl2_fresh = False

        return self._is_hl2_fresh

    def query_info(self, payload: Dict[str, Any], timeout: float = 3.0) -> Dict[str, Any]:
        """优先使用 hl2 极速状态查询 (1.2ms)，若滞后或报错无感切至官方公网"""
        use_hl2 = self.check_hl2_freshness()
        target_url = self.hl2_info_url if use_hl2 else self.official_info_url

        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(target_url, data=body, headers={"Content-Type": "application/json"})

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as err:
            # hl2 异常时，自动平滑切至官方公网兜底
            if target_url != self.official_info_url:
                req.full_url = self.official_info_url
                with urllib.request.urlopen(req, timeout=timeout + 2.0) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            raise err
```

---

## 5. 运维监控 Runbook

### 5.1 常用单行检查命令

```bash
# 1. 验证 hl1 的 WebSocket 推流服务运行状态与监听端口
ssh hl 'sudo docker ps | grep pusher'
ssh hl 'sudo ss -tulpn | grep 8000'

# 2. 查看 hl1 的 WebSocket 推送实时日志 (批次耗时、推送条数)
ssh hl 'sudo docker logs xlink-hypercore-data-hypy-websocket-pusher-1 -f --tail 30'

# 3. 检查 hl2 同步滞后时间 (正常应 <= 2s)
ssh hl2 'now=$(date +%s); blk=$(curl -s localhost:8086/metrics | awk "/^hl_core_latest_block_time/{printf \"%d\",\$2}"); echo "Sync Lag: $((now-blk))s"'

# 4. 检查 hl2 的 HTTP Info API 连通性
ssh hl2 'curl -s -X POST http://localhost:3001/info -H "Content-Type: application/json" -d "{\"type\":\"exchangeStatus\"}"'
```

### 5.2 服务故障重启 SOP

- **若 `hl1` 的 WebSocket 服务异常或端口断开**：
  ```bash
  ssh hl 'sudo docker restart xlink-hypercore-data-hypy-websocket-pusher-1'
  ```
- **若 `hl2` 的 Gossip Peer 衰退触发滞后警报 (`Lag > 2s`)**：
  ```bash
  ssh hl2 'cd /mnt/data1/hypeliquid-node/node && sudo docker compose restart node'
  ```
