let rawData = [];
let currentSortField = "hourly_funding";
let currentSortOrder = "desc"; // "asc" or "desc"
let refreshTimer = null;

document.addEventListener("DOMContentLoaded", () => {
    initEventListeners();
    fetchData();
    setupAutoRefresh();
});

function initEventListeners() {
    document.getElementById("btn-refresh").addEventListener("click", fetchData);
    document.getElementById("search-input").addEventListener("input", renderTable);
    
    document.getElementById("exchange-select").addEventListener("change", (e) => {
        const ex = e.target.value;
        if (ex === "bybit") {
            document.getElementById("spot-fee-input").value = "0.10";
            document.getElementById("perp-fee-input").value = "0.055";
        } else if (ex === "hyperliquid") {
            document.getElementById("spot-fee-input").value = "0.07";
            document.getElementById("perp-fee-input").value = "0.035";
        }
        updateFeeSummaryBadge();
        fetchData();
    });

    document.getElementById("spot-fee-input").addEventListener("change", () => {
        updateFeeSummaryBadge();
        fetchData();
    });
    document.getElementById("perp-fee-input").addEventListener("change", () => {
        updateFeeSummaryBadge();
        fetchData();
    });
    document.getElementById("max-spread-input").addEventListener("change", fetchData);
    document.getElementById("alias-toggle").addEventListener("change", fetchData);
    document.getElementById("payback-mode").addEventListener("change", renderTable);
    
    document.getElementById("refresh-rate").addEventListener("change", setupAutoRefresh);

    // Header sorting click handlers
    document.querySelectorAll("th.sortable").forEach(th => {
        th.addEventListener("click", () => {
            const field = th.getAttribute("data-sort");
            if (field === currentSortField) {
                currentSortOrder = (currentSortOrder === "asc") ? "desc" : "asc";
            } else {
                currentSortField = field;
                currentSortOrder = (field === "payback_hrs") ? "asc" : "desc";
            }
            updateSortHeaderUI();
            renderTable();
        });
    });
}

function updateFeeSummaryBadge() {
    const spotFee = parseFloat(document.getElementById("spot-fee-input").value) || 0.07;
    const perpFee = parseFloat(document.getElementById("perp-fee-input").value) || 0.035;
    const entryFee = spotFee + perpFee;
    const roundtripFee = entryFee * 2.0;

    const badge = document.getElementById("fee-summary-badge");
    badge.textContent = `当前单边开仓费率: ${entryFee.toFixed(3)}% | 双边完整费率: ${roundtripFee.toFixed(3)}%`;
}

function setupAutoRefresh() {
    if (refreshTimer) {
        clearInterval(refreshTimer);
        refreshTimer = null;
    }
    const intervalSec = parseInt(document.getElementById("refresh-rate").value);
    if (intervalSec > 0) {
        refreshTimer = setInterval(fetchData, intervalSec * 1000);
    }
}

function formatPrice(val) {
    if (val === null || val === undefined || isNaN(val)) return "--";
    const num = Number(val);
    if (num === 0) return "0.00";
    const absNum = Math.abs(num);
    if (absNum >= 1000) {
        return num.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    } else if (absNum >= 1) {
        return num.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 });
    } else if (absNum >= 0.0001) {
        return num.toFixed(6);
    } else {
        return num.toFixed(8);
    }
}

async function fetchData() {
    const statusText = document.getElementById("sync-status");
    statusText.textContent = "更新数据中...";

    const exchange = document.getElementById("exchange-select").value;
    const spotFee = document.getElementById("spot-fee-input").value;
    const perpFee = document.getElementById("perp-fee-input").value;
    const maxSpread = document.getElementById("max-spread-input").value;
    const enableAliases = document.getElementById("alias-toggle").checked;

    const url = `/api/funding-rates?exchange=${exchange}&spot_fee=${spotFee}&perp_fee=${perpFee}&max_spread=${maxSpread}&enable_aliases=${enableAliases}`;

    try {
        const response = await fetch(url);
        const json = await response.json();

        if (json.status === "success") {
            rawData = json.data;
            updateStatsOverview(rawData);
            renderTable();
            
            const now = new Date();
            document.getElementById("last-update-time").textContent = now.toTimeString().split(" ")[0];
            statusText.textContent = "实时同步中";
        } else {
            statusText.textContent = "数据获取失败";
            const tbody = document.getElementById("table-body");
            if (tbody && (!rawData || rawData.length === 0)) {
                tbody.innerHTML = `<tr><td colspan="13" class="loading-cell">数据获取失败: ${json.message || '未知错误'}</td></tr>`;
            }
        }
    } catch (err) {
        console.error("Fetch error:", err);
        statusText.textContent = "网络连接错误";
        const tbody = document.getElementById("table-body");
        if (tbody && (!rawData || rawData.length === 0)) {
            tbody.innerHTML = `<tr><td colspan="13" class="loading-cell">网络请求失败或前端解析错误: ${err.message}</td></tr>`;
        }
    }
}

function updateStatsOverview(data) {
    if (!data || data.length === 0) {
        document.getElementById("pair-count-val").textContent = "0";
        document.getElementById("top-apr-val").textContent = "--%";
        document.getElementById("top-apr-coin").textContent = "--";
        document.getElementById("fast-payback-val").textContent = "--";
        document.getElementById("fast-payback-coin").textContent = "--";
        document.getElementById("top-vol-val").textContent = "$0";
        document.getElementById("top-vol-coin").textContent = "--";
        return;
    }

    document.getElementById("pair-count-val").textContent = data.length;

    // Top APR
    const topApr = [...data].sort((a, b) => b.apr_pct - a.apr_pct)[0];
    if (topApr) {
        document.getElementById("top-apr-val").textContent = `${topApr.apr_pct >= 0 ? '+' : ''}${topApr.apr_pct.toFixed(2)}%`;
        document.getElementById("top-apr-coin").textContent = `${topApr.coin} (${topApr.exchange})`;
    }

    // Fastest Payback
    const paybackMode = document.getElementById("payback-mode").value;
    const key = paybackMode === "entry" ? "entry_payback_hrs" : "roundtrip_payback_hrs";
    const validPaybacks = data.filter(d => d[key] !== null && d[key] > 0);
    if (validPaybacks.length > 0) {
        validPaybacks.sort((a, b) => a[key] - b[key]);
        const fastest = validPaybacks[0];
        const strKey = paybackMode === "entry" ? "entry_payback_str" : "roundtrip_payback_str";
        document.getElementById("fast-payback-val").textContent = fastest[strKey];
        document.getElementById("fast-payback-coin").textContent = `${fastest.coin} (${fastest.exchange})`;
    } else {
        document.getElementById("fast-payback-val").textContent = "N/A";
        document.getElementById("fast-payback-coin").textContent = "无正向资费";
    }

    // Highest Volume
    const topVol = [...data].sort((a, b) => b.perp_24h_volume - a.perp_24h_volume)[0];
    if (topVol) {
        const formattedVol = topVol.perp_24h_volume > 1e6 
            ? `$${(topVol.perp_24h_volume / 1e6).toFixed(2)}M`
            : `$${(topVol.perp_24h_volume / 1e3).toFixed(1)}K`;
        document.getElementById("top-vol-val").textContent = formattedVol;
        document.getElementById("top-vol-coin").textContent = `${topVol.coin} (${topVol.exchange})`;
    }
}

function updateSortHeaderUI() {
    document.querySelectorAll("th.sortable").forEach(th => {
        const field = th.getAttribute("data-sort");
        let label = th.textContent.replace(" ▲", "").replace(" ▼", "");
        if (field === currentSortField) {
            th.textContent = `${label} ${currentSortOrder === "asc" ? "▲" : "▼"}`;
            th.classList.add("active-sort");
        } else {
            th.textContent = label;
            th.classList.remove("active-sort");
        }
    });
}

function renderTable() {
    const tbody = document.getElementById("table-body");
    const searchQuery = document.getElementById("search-input").value.trim().toLowerCase();
    const paybackMode = document.getElementById("payback-mode").value;

    // Filter
    let filtered = rawData.filter(item => {
        if (!searchQuery) return true;
        return item.coin.toLowerCase().includes(searchQuery) || 
               item.spot_symbol.toLowerCase().includes(searchQuery) ||
               (item.exchange && item.exchange.toLowerCase().includes(searchQuery)) ||
               item.spot_pair.toLowerCase().includes(searchQuery);
    });

    // Sort
    const sortKey = (currentSortField === "payback_hrs") 
        ? (paybackMode === "entry" ? "entry_payback_hrs" : "roundtrip_payback_hrs")
        : currentSortField;

    filtered.sort((a, b) => {
        let valA = a[sortKey];
        let valB = b[sortKey];

        if (valA === null || valA === undefined) valA = currentSortOrder === "asc" ? Infinity : -Infinity;
        if (valB === null || valB === undefined) valB = currentSortOrder === "asc" ? Infinity : -Infinity;

        if (typeof valA === "string") {
            return currentSortOrder === "asc" ? valA.localeCompare(valB) : valB.localeCompare(valA);
        } else {
            return currentSortOrder === "asc" ? valA - valB : valB - valA;
        }
    });

    if (filtered.length === 0) {
        tbody.innerHTML = `<tr><td colspan="12" class="loading-cell">未找到匹配的套利标的</td></tr>`;
        return;
    }

    let html = "";
    filtered.forEach((item, index) => {
        const hrRate = item.hourly_funding_pct;
        const hrClass = hrRate > 0 ? "rate-positive" : (hrRate < 0 ? "rate-negative" : "");
        const hrSign = hrRate > 0 ? "+" : "";

        const paybackHrsKey = paybackMode === "entry" ? "entry_payback_hrs" : "roundtrip_payback_hrs";
        const paybackStrKey = paybackMode === "entry" ? "entry_payback_str" : "roundtrip_payback_str";
        const paybackHrs = item[paybackHrsKey];
        const paybackStr = item[paybackStrKey];

        let paybackClass = "payback-slow";
        if (paybackHrs !== null && paybackHrs <= 24) {
            paybackClass = "payback-fast";
        } else if (paybackHrs !== null && paybackHrs <= 168) {
            paybackClass = "payback-medium";
        }

        const apyStr = (item.apy_pct > 10000) ? ">9999%" : `${item.apy_pct >= 0 ? '+' : ''}${item.apy_pct.toFixed(2)}%`;
        const aprStr = `${item.apr_pct >= 0 ? '+' : ''}${item.apr_pct.toFixed(2)}%`;
        const spreadStr = `${item.spread_pct >= 0 ? '+' : ''}${item.spread_pct.toFixed(2)}%`;

        const exchClass = item.exchange === "Hyperliquid" ? "exchange-hl" : "exchange-bybit";
        const exchShort = item.exchange === "Hyperliquid" ? "HL" : "Bybit";

        html += `
            <tr>
                <td><span class="rank-badge">${index + 1}</span></td>
                <td><span class="exchange-badge ${exchClass}">${exchShort}</span></td>
                <td><div class="coin-cell"><span>${item.coin}</span></div></td>
                <td><span style="color: var(--text-secondary);">${item.spot_symbol}</span> <span style="font-size:11px; opacity:0.6;">(${item.spot_pair})</span></td>
                <td><span class="badge-rate ${hrClass}">${hrSign}${hrRate.toFixed(4)}% /h</span></td>
                <td style="font-weight:700; color: ${item.apr_pct > 0 ? '#10b981' : '#f8fafc'};">${aprStr}</td>
                <td>${apyStr}</td>
                <td>$${formatPrice(item.spot_price)}</td>
                <td>$${formatPrice(item.perp_price)}</td>
                <td style="color: ${item.spread_pct >= 0 ? '#06b6d4' : '#ef4444'};">${spreadStr}</td>
                <td><span class="${paybackClass}">${paybackStr}</span></td>
                <td>$${Math.round(item.perp_24h_volume).toLocaleString()}</td>
                <td>
                    <button class="btn-action" onclick="openHistoryModal('${item.coin}')">📊 历史</button>
                    <button class="btn-action btn-depth" onclick="openDepthModal('${item.exchange}', '${item.coin}', '${item.spot_symbol}')">⚖️ 容量</button>
                    <button class="btn-action btn-build" onclick="openBuildModal('${item.coin}', '${item.spot_symbol}')">🚀 建仓</button>
                </td>
            </tr>
        `;
    });

    tbody.innerHTML = html;
}

// ----------------------------------------------------
// Depth & Capital Capacity Logic
// ----------------------------------------------------
let currentDepthExchange = "";
let currentDepthSymbol = "";
let currentDepthSpotSymbol = "";

function initDepthModalListeners() {
    const modal = document.getElementById("depth-modal");
    const closeBtn = document.getElementById("depth-modal-close-btn");
    const calcBtn = document.getElementById("btn-calc-custom");
    const input = document.getElementById("custom-capital-input");

    if (closeBtn) closeBtn.addEventListener("click", closeDepthModal);
    if (modal) {
        modal.addEventListener("click", (e) => {
            if (e.target === modal) closeDepthModal();
        });
    }

    if (calcBtn) {
        calcBtn.addEventListener("click", () => {
            const val = parseFloat(input.value);
            if (val && val > 0 && currentDepthSymbol) {
                fetchAndRenderDepthCapacity(currentDepthExchange, currentDepthSymbol, currentDepthSpotSymbol, val);
            }
        });
    }
}

document.addEventListener("DOMContentLoaded", initDepthModalListeners);

function openDepthModal(exchange, symbol, spotSymbol) {
    currentDepthExchange = exchange;
    currentDepthSymbol = symbol;
    currentDepthSpotSymbol = spotSymbol || symbol;

    const modal = document.getElementById("depth-modal");
    document.getElementById("depth-modal-title").textContent = `⚖️ Delta 中性建仓容量评估 - ${symbol}`;
    document.getElementById("depth-modal-subtitle").textContent = `${exchange} (Spot: ${currentDepthSpotSymbol} | Perp: ${symbol})`;

    modal.classList.remove("hidden");
    const customVal = parseFloat(document.getElementById("custom-capital-input").value) || 10000;
    fetchAndRenderDepthCapacity(exchange, symbol, currentDepthSpotSymbol, customVal);
}

function closeDepthModal() {
    const modal = document.getElementById("depth-modal");
    if (modal) modal.classList.add("hidden");
}

async function fetchAndRenderDepthCapacity(exchange, symbol, spotSymbol, customUsd) {
    const tbody = document.getElementById("depth-table-body");
    tbody.innerHTML = `<tr><td colspan="7" class="loading-cell">正在拉取 ${symbol} L2 盘口深度并测算容量...</td></tr>`;

    try {
        const exchParam = exchange.toLowerCase().includes("bybit") ? "bybit" : "hyperliquid";
        let url = `/api/depth-capacity?exchange=${exchParam}&symbol=${symbol}&spot_symbol=${spotSymbol || ''}`;
        if (customUsd) url += `&target_usd=${customUsd}`;

        const response = await fetch(url);
        const json = await response.json();

        if (json.status === "success" && json.data) {
            renderDepthCapacityUI(json.data);
        } else {
            tbody.innerHTML = `<tr><td colspan="7" class="loading-cell">无法获取该标的的盘口深度数据</td></tr>`;
        }
    } catch (err) {
        console.error("Fetch depth capacity error:", err);
        tbody.innerHTML = `<tr><td colspan="7" class="loading-cell">网络请求失败</td></tr>`;
    }
}

function renderDepthCapacityUI(data) {
    const caps = data.max_capacities || {};
    
    // Render C_max cards
    document.getElementById("cap-01-val").textContent = caps.max_cap_01_pct_usd ? `$${caps.max_cap_01_pct_usd.toLocaleString()}` : "$0";
    document.getElementById("cap-02-val").textContent = caps.max_cap_02_pct_usd ? `$${caps.max_cap_02_pct_usd.toLocaleString()}` : "$0";
    document.getElementById("cap-05-val").textContent = caps.max_cap_05_pct_usd ? `$${caps.max_cap_05_pct_usd.toLocaleString()}` : "$0";
    document.getElementById("cap-24h-val").textContent = caps.max_cap_24h_payback_usd ? `$${caps.max_cap_24h_payback_usd.toLocaleString()}` : "$0";

    // Render Custom Sim Banner
    const simBanner = document.getElementById("custom-sim-result-banner");
    const customSim = data.custom_simulation;
    if (customSim) {
        simBanner.classList.remove("hidden");
        if (customSim.exceeds_depth) {
            simBanner.className = "sim-banner banner-red";
            simBanner.innerHTML = `⚠️ <b>建仓 $${customSim.target_usd.toLocaleString()}</b>: 超出当前 L2 盘口深度上限！`;
        } else {
            const isGood = customSim.combined_slippage_pct <= 0.30;
            simBanner.className = `sim-banner ${isGood ? 'banner-green' : 'banner-yellow'}`;
            simBanner.innerHTML = `
                ⚡ <b>建仓 $${customSim.target_usd.toLocaleString()} 测算结果</b>: 
                现货滑点: <b>+${customSim.spot_slippage_pct.toFixed(4)}%</b> | 
                永续滑点: <b>-${customSim.perp_slippage_pct.toFixed(4)}%</b> | 
                综合滑点: <b>+${customSim.combined_slippage_pct.toFixed(4)}%</b> | 
                总开仓成本: <b>${customSim.total_cost_pct.toFixed(4)}%</b> | 
                预计回本时间: <b>${customSim.payback_str}</b> 
                <span class="banner-status-badge">${customSim.status}</span>
            `;
        }
    } else {
        simBanner.classList.add("hidden");
    }

    // Render Table
    const tbody = document.getElementById("depth-table-body");
    const sims = data.simulations || [];
    if (sims.length === 0) {
        tbody.innerHTML = `<tr><td colspan="7" class="loading-cell">无盘口测算结果</td></tr>`;
        return;
    }

    let html = "";
    sims.forEach(sim => {
        const isCustom = data.custom_target_usd && sim.target_usd === data.custom_target_usd;
        const rowClass = isCustom ? "highlight-row" : "";

        if (sim.exceeds_depth) {
            html += `
                <tr class="${rowClass}">
                    <td style="font-weight:700; color:var(--accent-cyan);">$${sim.target_usd.toLocaleString()}</td>
                    <td>-</td>
                    <td>-</td>
                    <td>-</td>
                    <td>-</td>
                    <td><span class="payback-slow">超出深度</span></td>
                    <td><span class="badge-rate rate-negative">超出盘口深度</span></td>
                </tr>
            `;
        } else {
            const totSlip = sim.combined_slippage_pct;
            const slipClass = totSlip <= 0.20 ? "rate-positive" : (totSlip <= 0.50 ? "" : "rate-negative");
            const statusBadgeClass = (sim.status && sim.status.includes("推荐")) ? "rate-positive" : "";

            html += `
                <tr class="${rowClass}">
                    <td style="font-weight:700; color:var(--accent-cyan);">$${sim.target_usd.toLocaleString()} ${isCustom ? '⭐' : ''}</td>
                    <td>$${formatPrice(sim.spot_vwap)} <span style="font-size:11px; opacity:0.7;">(+${sim.spot_slippage_pct.toFixed(4)}%)</span></td>
                    <td>$${formatPrice(sim.perp_vwap)} <span style="font-size:11px; opacity:0.7;">(-${sim.perp_slippage_pct.toFixed(4)}%)</span></td>
                    <td><span class="badge-rate ${slipClass}">+${totSlip.toFixed(4)}%</span></td>
                    <td style="font-weight:600;">${sim.total_cost_pct.toFixed(4)}%</td>
                    <td style="font-weight:600; color:#38bdf8;">${sim.payback_str}</td>
                    <td><span class="status-pill ${totSlip <= 0.20 ? 'pill-green' : (totSlip <= 0.50 ? 'pill-yellow' : 'pill-red')}">${sim.status}</span></td>
                </tr>
            `;
        }
    });

    tbody.innerHTML = html;
}


// ----------------------------------------------------
// History Modal & Analysis Logic
// ----------------------------------------------------
let currentHistorySymbol = "";
let currentHistoryDays = 30;

function initModalListeners() {
    const modal = document.getElementById("history-modal");
    const closeBtn = document.getElementById("modal-close-btn");

    if (closeBtn) {
        closeBtn.addEventListener("click", closeHistoryModal);
    }

    if (modal) {
        modal.addEventListener("click", (e) => {
            if (e.target === modal) closeHistoryModal();
        });
    }

    document.querySelectorAll(".range-btn").forEach(btn => {
        btn.addEventListener("click", (e) => {
            document.querySelectorAll(".range-btn").forEach(b => b.classList.remove("active"));
            e.target.classList.add("active");
            currentHistoryDays = parseInt(e.target.getAttribute("data-days"));
            if (currentHistorySymbol) {
                fetchAndRenderHistory(currentHistorySymbol, currentHistoryDays);
            }
        });
    });
}

// Attach modal listener on load
document.addEventListener("DOMContentLoaded", initModalListeners);

function openHistoryModal(symbol) {
    currentHistorySymbol = symbol;
    const modal = document.getElementById("history-modal");
    document.getElementById("modal-title").textContent = `📊 ${symbol} 历史资金费率分析`;
    document.getElementById("modal-subtitle").textContent = `Bybit Linear Perpetual Contract (${symbol})`;
    
    modal.classList.remove("hidden");
    fetchAndRenderHistory(symbol, currentHistoryDays);
}

function closeHistoryModal() {
    const modal = document.getElementById("history-modal");
    if (modal) modal.classList.add("hidden");
}

async function fetchAndRenderHistory(symbol, days) {
    const tbody = document.getElementById("modal-table-body");
    tbody.innerHTML = `<tr><td colspan="5" class="loading-cell">正在获取 ${symbol} 历史资费明细...</td></tr>`;

    try {
        const response = await fetch(`/api/bybit/funding-history?symbol=${symbol}&days=${days}`);
        const json = await response.json();

        if (json.status === "success" && json.data) {
            const data = json.data;
            renderModalStats(data);
            drawHistoryChart(data.records);
            renderModalTable(data.records);
        } else {
            tbody.innerHTML = `<tr><td colspan="5" class="loading-cell">无该标的的历史数据或解析失败</td></tr>`;
        }
    } catch (err) {
        console.error("Fetch history error:", err);
        tbody.innerHTML = `<tr><td colspan="5" class="loading-cell">网络请求失败</td></tr>`;
    }
}

function renderModalStats(data) {
    const stats = data.stats || {};
    
    const apr = stats.apr_simple_pct || 0;
    const apy = stats.apy_compound_pct || 0;
    document.getElementById("mstat-apr").textContent = `${apr >= 0 ? '+' : ''}${apr.toFixed(2)}%`;
    document.getElementById("mstat-apr").style.color = apr >= 0 ? "#10b981" : "#ef4444";
    document.getElementById("mstat-apy").textContent = `复利 APY: ${apy > 10000 ? '>9999%' : (apy >= 0 ? '+' : '') + apy.toFixed(2) + '%'}`;

    const cum = stats.cumulative_funding_pct || 0;
    document.getElementById("mstat-cum").textContent = `${cum >= 0 ? '+' : ''}${cum.toFixed(4)}%`;
    document.getElementById("mstat-cum").style.color = cum >= 0 ? "#10b981" : "#ef4444";
    document.getElementById("mstat-periods").textContent = `总结算次数: ${data.total_periods || 0} 次 (${data.funding_interval_hr || 8}h周期)`;

    const posPct = stats.pos_pct || 0;
    document.getElementById("mstat-pos-pct").textContent = `${posPct.toFixed(1)}%`;
    document.getElementById("mstat-pos-count").textContent = `正资费: ${stats.pos_count || 0} | 负资费: ${stats.neg_count || 0}`;

    const maxRate = stats.max_rate_pct || 0;
    const minRate = stats.min_rate_pct || 0;
    document.getElementById("mstat-max").textContent = `${maxRate >= 0 ? '+' : ''}${maxRate.toFixed(4)}%`;
    document.getElementById("mstat-min").textContent = `最低: ${minRate >= 0 ? '+' : ''}${minRate.toFixed(4)}%`;

    const vol = stats.std_dev_period_pct || 0;
    document.getElementById("mstat-vol").textContent = `${vol.toFixed(4)}%`;
}

function drawHistoryChart(records) {
    const canvas = document.getElementById("history-chart-canvas");
    if (!canvas) return;

    const container = canvas.parentElement;
    canvas.width = container.clientWidth || 750;
    canvas.height = 180;

    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    if (!records || records.length === 0) return;

    const padding = { top: 20, bottom: 25, left: 60, right: 20 };
    const chartW = canvas.width - padding.left - padding.right;
    const chartH = canvas.height - padding.top - padding.bottom;

    const rates = records.map(r => r.period_funding_pct);
    let maxVal = Math.max(...rates, 0.001);
    let minVal = Math.min(...rates, -0.001);
    const maxAbs = Math.max(Math.abs(maxVal), Math.abs(minVal)) * 1.2;

    const zeroY = padding.top + chartH / 2;

    // Draw zero line
    ctx.strokeStyle = "rgba(255, 255, 255, 0.15)";
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(padding.left, zeroY);
    ctx.lineTo(padding.left + chartW, zeroY);
    ctx.stroke();
    ctx.setLineDash([]);

    // Y Axis labels
    ctx.fillStyle = "rgba(255, 255, 255, 0.4)";
    ctx.font = "10px JetBrains Mono, monospace";
    ctx.textAlign = "right";
    ctx.fillText("0.00%", padding.left - 6, zeroY + 3);
    ctx.fillText(`+${maxAbs.toFixed(3)}%`, padding.left - 6, padding.top + 10);
    ctx.fillText(`-${maxAbs.toFixed(3)}%`, padding.left - 6, padding.top + chartH - 2);

    // Bars
    const stepX = chartW / records.length;
    const barW = Math.max(2, stepX - 2);

    records.forEach((r, i) => {
        const val = r.period_funding_pct;
        const x = padding.left + i * stepX + 1;
        const barH = (Math.abs(val) / maxAbs) * (chartH / 2);

        ctx.fillStyle = val >= 0 ? "#10b981" : "#ef4444";
        if (val >= 0) {
            ctx.fillRect(x, zeroY - barH, barW, barH);
        } else {
            ctx.fillRect(x, zeroY, barW, barH);
        }
    });
}

function renderModalTable(records) {
    const tbody = document.getElementById("modal-table-body");
    if (!records || records.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5" class="loading-cell">无历史明细数据</td></tr>`;
        return;
    }

    // Show newest first in detail table
    const reversed = [...records].reverse();
    let html = "";
    reversed.forEach((rec, idx) => {
        const pRate = rec.period_funding_pct;
        const pClass = pRate > 0 ? "rate-positive" : (pRate < 0 ? "rate-negative" : "");
        const pSign = pRate > 0 ? "+" : "";

        const hRate = rec.hourly_funding_pct;
        const hClass = hRate > 0 ? "rate-positive" : (hRate < 0 ? "rate-negative" : "");
        const hSign = hRate > 0 ? "+" : "";

        const apr = rec.apr_pct;

        html += `
            <tr>
                <td><span class="rank-badge">${idx + 1}</span></td>
                <td>${rec.datetime_utc}</td>
                <td><span class="badge-rate ${pClass}">${pSign}${pRate.toFixed(4)}%</span></td>
                <td><span class="badge-rate ${hClass}">${hSign}${hRate.toFixed(4)}% /h</span></td>
                <td style="font-weight:700; color: ${apr > 0 ? '#10b981' : '#f8fafc'};">${apr >= 0 ? '+' : ''}${apr.toFixed(2)}%</td>
            </tr>
        `;
    });

    tbody.innerHTML = html;
}

// ----------------------------------------------------
// Build Arbitrage & Risk Guard Logic
// ----------------------------------------------------
let currentBuildSymbol = "";
let currentBuildSpotSymbol = "";

function initBuildModalListeners() {
    const modal = document.getElementById("build-modal");
    const closeBtn = document.getElementById("build-modal-close-btn");
    const tryRunBtn = document.getElementById("btn-run-tryrun");
    const modeRadios = document.querySelectorAll("input[name='sizing-mode']");

    if (closeBtn) closeBtn.addEventListener("click", closeBuildModal);
    if (modal) {
        modal.addEventListener("click", (e) => {
            if (e.target === modal) closeBuildModal();
        });
    }

    modeRadios.forEach(radio => {
        radio.addEventListener("change", (e) => {
            const isUsd = e.target.value === "usd";
            document.getElementById("build-input-label").textContent = isUsd ? "目标建仓金额 (USD):" : "目标建仓 Token 数量:";
            document.getElementById("build-amount-input").placeholder = isUsd ? "例如 10000" : "例如 1000000";
        });
    });

    if (tryRunBtn) {
        tryRunBtn.addEventListener("click", () => {
            fetchAndRenderBuildTryRun();
        });
    }
}

document.addEventListener("DOMContentLoaded", initBuildModalListeners);

function openBuildModal(symbol, spotSymbol) {
    currentBuildSymbol = symbol;
    currentBuildSpotSymbol = spotSymbol || symbol;

    const modal = document.getElementById("build-modal");
    document.getElementById("build-modal-title").textContent = `🚀 Bybit Delta 中性套利建仓演练与风控 - ${symbol}`;
    document.getElementById("build-modal-subtitle").textContent = `Spot: ${currentBuildSpotSymbol} | Perp: ${symbol}`;

    modal.classList.remove("hidden");
    fetchAndRenderBuildTryRun();
}

function closeBuildModal() {
    const modal = document.getElementById("build-modal");
    if (modal) modal.classList.add("hidden");
}

async function fetchAndRenderBuildTryRun() {
    const tbody = document.getElementById("build-table-body");
    const banner = document.getElementById("build-risk-banner");
    tbody.innerHTML = `<tr><td colspan="8" class="loading-cell">正在进行 Try-Run 模拟演练并分析风控卡口...</td></tr>`;

    const isUsd = document.querySelector("input[name='sizing-mode']:checked").value === "usd";
    const amountVal = parseFloat(document.getElementById("build-amount-input").value) || 10000;
    const force = document.getElementById("chk-force-override").checked;

    let url = `/api/bybit/build-arbitrage?symbol=${currentBuildSymbol}&dry_run=true&force=${force}`;
    if (isUsd) {
        url += `&amount_usd=${amountVal}`;
    } else {
        url += `&amount_qty=${amountVal}`;
    }

    try {
        const response = await fetch(url);
        const json = await response.json();

        if (json.status === "success" && json.try_run_plan) {
            const plan = json.try_run_plan;
            const risk = plan.risk_guard || {};

            // Render Risk Banner
            if (risk.decision === "PASSED") {
                banner.className = "sim-banner banner-green";
                banner.innerHTML = `✅ <b>风控检查全部通过 (PASSED)</b>: 1h资费 +${plan.hourly_funding_pct.toFixed(4)}%/h, 综合滑点 +${(plan.combined_slippage_pct||0).toFixed(4)}%, 预计回本时间 ${plan.payback_str}`;
            } else if (risk.decision === "FORCED_OVERRIDE") {
                const warningsStr = (risk.warnings || []).join("<br>");
                banner.className = "sim-banner banner-yellow";
                banner.innerHTML = `⚠️ <b>强制建仓覆写 (FORCED OVERRIDE)</b>: 风控拦截已被 --force 绕过！<br>${warningsStr}`;
            } else {
                const warningsStr = (risk.warnings || []).join("<br>");
                banner.className = "sim-banner banner-red";
                banner.innerHTML = `🛑 <b>建仓已被风控系统拦截 (BLOCKED)</b>:<br>${warningsStr}<br><span style="font-size:11px; opacity:0.8;">提示: 调整资金金额/参数，或勾选“强制建仓覆写”按钮跳过风控拦截。</span>`;
            }

            // Render Orders Table
            const orders = plan.orders_plan || [];
            let html = "";
            orders.forEach(order => {
                const slip = order.slippage_pct;
                const slipStr = slip !== null ? `+${slip.toFixed(4)}%` : "-";
                html += `
                    <tr>
                        <td style="font-weight:700; color:var(--accent-cyan);">${order.leg}</td>
                        <td><span class="exchange-badge exchange-hl">${order.category.toUpperCase()}</span></td>
                        <td style="font-weight:600;">${order.symbol}</td>
                        <td style="font-weight:700; color:${order.side === 'Buy' ? '#10b981' : '#ef4444'};">${order.side}</td>
                        <td>${order.order_type}</td>
                        <td style="font-family:var(--font-mono); font-weight:600;">${order.quantity_str}</td>
                        <td style="font-family:var(--font-mono);">$${formatPrice(order.expected_vwap)}</td>
                        <td><span class="badge-rate ${slip <= 0.2 ? 'rate-positive' : ''}">${slipStr}</span></td>
                    </tr>
                `;
            });
            tbody.innerHTML = html;
        } else {
            tbody.innerHTML = `<tr><td colspan="8" class="loading-cell">无法生成 Try-Run 演练计划</td></tr>`;
        }
    } catch (err) {
        console.error("Fetch build try-run error:", err);
        tbody.innerHTML = `<tr><td colspan="8" class="loading-cell">网络请求失败</td></tr>`;
    }
}



