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
    
    // Knowledge Base Modal
    const guideBtn = document.getElementById("btn-knowledge-guide");
    const guideModal = document.getElementById("knowledge-modal");
    const guideCloseBtn = document.getElementById("knowledge-modal-close-btn");

    if (guideBtn) guideBtn.addEventListener("click", openKnowledgeModal);
    if (guideCloseBtn) guideCloseBtn.addEventListener("click", closeKnowledgeModal);
    if (guideModal) {
        guideModal.addEventListener("click", (e) => {
            if (e.target === guideModal) closeKnowledgeModal();
        });
    }

    // Guide Tab Switching
    document.querySelectorAll(".guide-tab-btn").forEach(btn => {
        btn.addEventListener("click", (e) => {
            document.querySelectorAll(".guide-tab-btn").forEach(b => b.classList.remove("active"));
            document.querySelectorAll(".guide-tab-pane").forEach(p => p.classList.remove("active"));
            
            const targetTab = e.currentTarget.getAttribute("data-tab");
            e.currentTarget.classList.add("active");
            const pane = document.getElementById(targetTab);
            if (pane) pane.classList.add("active");
        });
    });

    const originSelect = document.getElementById("origin-type-select");
    if (originSelect) {
        originSelect.addEventListener("change", renderTable);
        originSelect.addEventListener("input", renderTable);
    }

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

function openKnowledgeModal() {
    const modal = document.getElementById("knowledge-modal");
    if (modal) modal.classList.remove("hidden");
}

function closeKnowledgeModal() {
    const modal = document.getElementById("knowledge-modal");
    if (modal) modal.classList.add("hidden");
}

function getFilterKeyForOrigin(originType, hasEvm, multiplier) {
    if (originType === "OFFICIAL_CANONICAL" || originType === "NATIVE_HYPE") return "hl_official";
    if (originType === "UNIT_BRIDGED") return "hl_unit";
    if (originType === "HYBRIDGE_BRIDGED") return "hl_hybridge";
    if (originType === "HIP1_PERMISSIONLESS") return "hl_hip1";
    if (originType === "BYBIT_OFFICIAL_LINEAR") return "bybit_linear";
    if (originType === "BYBIT_MULTIPLIER" || (multiplier && multiplier > 1.0)) return "multiplier";
    if (hasEvm) return "evm";
    return "all";
}

function filterByOrigin(originKey) {
    const originSelect = document.getElementById("origin-type-select");
    if (originSelect) {
        originSelect.value = originKey;
        renderTable();
    }
}

function renderTable() {
    const tbody = document.getElementById("table-body");
    const searchQuery = document.getElementById("search-input").value.trim().toLowerCase();
    const paybackMode = document.getElementById("payback-mode").value;
    const originSelectEl = document.getElementById("origin-type-select");
    const originFilter = originSelectEl ? originSelectEl.value : "all";

    // Filter
    let filtered = rawData.filter(item => {
        // Search filter
        if (searchQuery) {
            const matchesSearch = item.coin.toLowerCase().includes(searchQuery) || 
                   item.spot_symbol.toLowerCase().includes(searchQuery) ||
                   (item.exchange && item.exchange.toLowerCase().includes(searchQuery)) ||
                   item.spot_pair.toLowerCase().includes(searchQuery) ||
                   (item.raw_spot_pair && item.raw_spot_pair.toLowerCase().includes(searchQuery)) ||
                   (item.raw_pair_id && item.raw_pair_id.toLowerCase().includes(searchQuery)) ||
                   (item.origin_badge && item.origin_badge.toLowerCase().includes(searchQuery)) ||
                   (item.origin_type && item.origin_type.toLowerCase().includes(searchQuery));
            if (!matchesSearch) return false;
        }

        // Origin Type Filter
        if (originFilter && originFilter !== "all") {
            const otype = item.origin_type || "";
            const hasEvm = !!item.has_evm;
            const mult = parseFloat(item.multiplier) || 1.0;

            if (originFilter === "hl_official" || originFilter === "official") {
                if (otype !== "OFFICIAL_CANONICAL" && otype !== "NATIVE_HYPE") return false;
            } else if (originFilter === "hl_unit" || originFilter === "unit") {
                if (otype !== "UNIT_BRIDGED") return false;
            } else if (originFilter === "hl_hybridge" || originFilter === "hybridge") {
                if (otype !== "HYBRIDGE_BRIDGED") return false;
            } else if (originFilter === "hl_hip1" || originFilter === "hip1") {
                if (otype !== "HIP1_PERMISSIONLESS") return false;
            } else if (originFilter === "bybit_linear") {
                if (otype !== "BYBIT_OFFICIAL_LINEAR") return false;
            } else if (originFilter === "all_official") {
                if (otype !== "OFFICIAL_CANONICAL" && otype !== "NATIVE_HYPE" && otype !== "BYBIT_OFFICIAL_LINEAR") return false;
            } else if (originFilter === "evm") {
                if (!hasEvm) return false;
            } else if (originFilter === "multiplier") {
                if (mult <= 1.0 && otype !== "BYBIT_MULTIPLIER") return false;
            }
        }

        return true;
    });

    // Update the 4 stats overview cards for the active filtered view!
    updateStatsOverview(filtered);

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
        tbody.innerHTML = `<tr><td colspan="14" class="loading-cell">未找到匹配的套利标的 (当前筛选无数据)</td></tr>`;
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

        let badgeClass = "badge-canonical";
        if (item.origin_type === "UNIT_BRIDGED") badgeClass = "badge-unit";
        else if (item.origin_type === "HYBRIDGE_BRIDGED") badgeClass = "badge-bridged";
        else if (item.origin_type === "HIP1_PERMISSIONLESS") badgeClass = "badge-hip1";
        else if (item.origin_type === "BYBIT_MULTIPLIER" || (item.multiplier && item.multiplier > 1.0)) badgeClass = "badge-multiplier";

        const originBadgeText = item.origin_badge || (item.exchange === "Hyperliquid" ? "🏛️ 官方" : "🏛️ 官方正向");
        const evmTagHtml = item.has_evm ? `<span class="badge-evm-tag">EVM</span>` : "";
        const clickFilterKey = getFilterKeyForOrigin(item.origin_type, item.has_evm, item.multiplier);

        const originTooltipHtml = `
            <div class="tooltip-wrapper">
                <span class="badge-origin ${badgeClass}" onclick="filterByOrigin('${clickFilterKey}')" title="点击过滤此类型标的">${originBadgeText}${evmTagHtml}</span>
                <div class="origin-tooltip">
                    <div class="tooltip-title">
                        <span>${originBadgeText}</span>
                        <span>${item.exchange}</span>
                    </div>
                    <div class="tooltip-desc">${item.origin_desc || 'Hyperliquid / Bybit 撮合标的'}</div>
                    <div class="tooltip-meta-row">
                        <span class="tooltip-meta-label">底层交易对:</span>
                        <span class="tooltip-meta-val">${item.raw_pair_id || item.raw_spot_pair || item.spot_pair}</span>
                    </div>
                    ${item.evm_address ? `<div class="tooltip-meta-row"><span class="tooltip-meta-label">EVM 合约:</span><span class="tooltip-meta-val">${item.evm_address.slice(0, 8)}...${item.evm_address.slice(-6)}</span></div>` : ''}
                    <div class="tooltip-meta-row">
                        <span class="tooltip-meta-label">质押属性:</span>
                        <span class="tooltip-meta-val" style="color:${(item.origin_type === 'OFFICIAL_CANONICAL' || item.origin_type === 'NATIVE_HYPE') ? '#34d399' : '#94a3b8'};">${item.collateral_status || '全款现货对冲'}</span>
                    </div>
                    <div class="tooltip-risk">💡 ${item.risk_note || '注意核对现货与永续基差'}</div>
                </div>
            </div>
        `;

        html += `
            <tr>
                <td><span class="rank-badge">${index + 1}</span></td>
                <td><span class="exchange-badge ${exchClass}">${exchShort}</span></td>
                <td><div class="coin-cell"><span style="font-weight:700;">${item.coin}</span></div></td>
                <td>${originTooltipHtml}</td>
                <td><span style="color: var(--text-primary); font-weight:600;">${item.spot_symbol}</span> <span style="font-size:11px; opacity:0.75; font-family:var(--font-mono);">(${item.raw_spot_pair || item.spot_pair})</span></td>
                <td><span class="badge-rate ${hrClass}">${hrSign}${hrRate.toFixed(4)}% /h</span></td>
                <td style="font-weight:700; color: ${item.apr_pct > 0 ? '#10b981' : '#f8fafc'};">${aprStr}</td>
                <td>${apyStr}</td>
                <td>$${formatPrice(item.spot_price)}</td>
                <td>$${formatPrice(item.perp_price)}</td>
                <td style="color: ${item.spread_pct >= 0 ? '#06b6d4' : '#ef4444'}; font-weight:600;">${spreadStr}</td>
                <td><span class="${paybackClass}">${paybackStr}</span></td>
                <td>$${Math.round(item.perp_24h_volume).toLocaleString()}</td>
                <td>
                    <button class="btn-action" onclick="openHistoryModal('${item.exchange}', '${item.coin}')">📊 历史</button>
                    <button class="btn-action btn-depth" onclick="openDepthModal('${item.exchange}', '${item.coin}', '${item.raw_spot_pair || item.spot_symbol}')">⚖️ 容量</button>
                    <button class="btn-action btn-build" onclick="openBuildModal('${item.exchange}', '${item.coin}', '${item.spot_symbol}', '${item.raw_spot_pair || ''}')">🚀 建仓</button>
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
let currentHistoryExchange = "Bybit";
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
                fetchAndRenderHistory(currentHistoryExchange, currentHistorySymbol, currentHistoryDays);
            }
        });
    });
}

// Attach modal listener on load
document.addEventListener("DOMContentLoaded", initModalListeners);

function openHistoryModal(exchange, symbol) {
    currentHistoryExchange = exchange || "Bybit";
    currentHistorySymbol = symbol;
    const modal = document.getElementById("history-modal");
    document.getElementById("modal-title").textContent = `📊 ${symbol} 历史资金费率分析`;
    const isHl = currentHistoryExchange.toLowerCase().includes("hyperliquid");
    const exchDesc = isHl ? "Hyperliquid Perpetual Contract" : "Bybit Linear Perpetual Contract";
    document.getElementById("modal-subtitle").textContent = `${exchDesc} (${symbol})`;
    
    modal.classList.remove("hidden");
    fetchAndRenderHistory(currentHistoryExchange, currentHistorySymbol, currentHistoryDays);
}

function closeHistoryModal() {
    const modal = document.getElementById("history-modal");
    if (modal) modal.classList.add("hidden");
}

async function fetchAndRenderHistory(exchange, symbol, days) {
    const tbody = document.getElementById("modal-table-body");
    tbody.innerHTML = `<tr><td colspan="5" class="loading-cell">正在获取 ${symbol} (${exchange || ''}) 历史资费明细...</td></tr>`;

    try {
        const exchParam = (exchange || "").toLowerCase().includes("hyperliquid") ? "hyperliquid" : "bybit";
        const response = await fetch(`/api/funding-history?exchange=${exchParam}&symbol=${encodeURIComponent(symbol)}&days=${days}`);
        const json = await response.json();

        if (json.status === "success" && json.data && json.data.total_periods > 0) {
            const data = json.data;
            renderModalStats(data);
            drawHistoryChart(data.records);
            renderModalTable(data.records);
        } else if (json.status === "success" && json.data && json.data.total_periods === 0) {
            renderModalStats(json.data);
            drawHistoryChart([]);
            tbody.innerHTML = `<tr><td colspan="5" class="loading-cell">暂无该标的在选定时间范围内的历史资费数据</td></tr>`;
        } else {
            const errMsg = json.message || "无该标的的历史数据或解析失败";
            tbody.innerHTML = `<tr><td colspan="5" class="loading-cell">${errMsg}</td></tr>`;
        }
    } catch (err) {
        console.error("Fetch history error:", err);
        tbody.innerHTML = `<tr><td colspan="5" class="loading-cell">网络请求失败: ${err.message}</td></tr>`;
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
    let maxVal = Math.max(...rates, 0.0001);
    let minVal = Math.min(...rates, -0.0001);
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

    // Bars - adaptive bar width
    const stepX = chartW / records.length;
    const barW = Math.max(1, Math.min(12, stepX * 0.85));

    records.forEach((r, i) => {
        const val = r.period_funding_pct;
        const x = padding.left + i * stepX;
        const barH = (Math.abs(val) / maxAbs) * (chartH / 2);

        ctx.fillStyle = val >= 0 ? "#10b981" : "#ef4444";
        if (val >= 0) {
            ctx.fillRect(x, zeroY - barH, barW, Math.max(1, barH));
        } else {
            ctx.fillRect(x, zeroY, barW, Math.max(1, barH));
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
let currentBuildExchange = "bybit";
let currentBuildSymbol = "";
let currentBuildSpotSymbol = "";
let currentBuildRawSpotPair = "";

function initBuildModalListeners() {
    const modal = document.getElementById("build-modal");
    const closeBtn = document.getElementById("build-modal-close-btn");
    const tryRunBtn = document.getElementById("btn-run-tryrun");
    const modeRadios = document.querySelectorAll("input[name='sizing-mode']");
    const execRadios = document.querySelectorAll("input[name='execution-mode']");

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

    execRadios.forEach(radio => {
        radio.addEventListener("change", () => {
            fetchAndRenderBuildTryRun();
        });
    });

    if (tryRunBtn) {
        tryRunBtn.addEventListener("click", () => {
            fetchAndRenderBuildTryRun();
        });
    }
}

document.addEventListener("DOMContentLoaded", initBuildModalListeners);

function openDepthModal(exchange, symbol, spotSymbol) {
    currentDepthExchange = exchange;
    currentDepthSymbol = symbol;
    currentDepthSpotSymbol = spotSymbol || symbol;

    const matchedItem = rawData.find(d => d.coin === symbol && d.exchange.toLowerCase().includes(exchange.toLowerCase()));
    const originBadgeText = matchedItem ? ` [${matchedItem.origin_badge || ''}]` : "";

    const modal = document.getElementById("depth-modal");
    document.getElementById("depth-modal-title").textContent = `⚖️ Delta 中性建仓容量评估 - ${symbol}`;
    document.getElementById("depth-modal-subtitle").textContent = `${exchange} (Spot: ${currentDepthSpotSymbol} | Perp: ${symbol})${originBadgeText}`;

    modal.classList.remove("hidden");
    const customVal = parseFloat(document.getElementById("custom-capital-input").value) || 10000;
    fetchAndRenderDepthCapacity(exchange, symbol, currentDepthSpotSymbol, customVal);
}

function closeDepthModal() {
    const modal = document.getElementById("depth-modal");
    if (modal) modal.classList.add("hidden");
}

function openBuildModal(exchange, symbol, spotSymbol, rawSpotPair) {
    currentBuildExchange = exchange || "bybit";
    currentBuildSymbol = symbol;
    currentBuildSpotSymbol = spotSymbol || symbol;
    currentBuildRawSpotPair = rawSpotPair || "";

    const exchName = currentBuildExchange.toLowerCase().includes("hyperliquid") ? "Hyperliquid" : "Bybit";
    const matchedItem = rawData.find(d => d.coin === symbol && d.exchange.toLowerCase().includes(exchange.toLowerCase()));
    const originBadgeText = matchedItem ? ` [${matchedItem.origin_badge || ''}]` : "";
    const collateralText = (matchedItem && matchedItem.collateral_status) ? ` | 质押属性: ${matchedItem.collateral_status}` : "";

    const modal = document.getElementById("build-modal");
    document.getElementById("build-modal-title").textContent = `🚀 ${exchName} Delta 中性套利建仓演练与风控 - ${symbol}`;
    document.getElementById("build-modal-subtitle").textContent = `Exchange: ${exchName} | Spot: ${currentBuildSpotSymbol} | Perp: ${symbol}${originBadgeText}${collateralText}`;

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
    const workflowBanner = document.getElementById("build-workflow-banner");
    tbody.innerHTML = `<tr><td colspan="9" class="loading-cell">正在进行 Try-Run 模拟演练并分析风控卡口...</td></tr>`;

    const isUsd = document.querySelector("input[name='sizing-mode']:checked").value === "usd";
    const execModeRadio = document.querySelector("input[name='execution-mode']:checked");
    const execMode = execModeRadio ? execModeRadio.value : "maker_taker";
    const amountVal = parseFloat(document.getElementById("build-amount-input").value) || 10000;
    const force = document.getElementById("chk-force-override").checked;

    const exchParam = currentBuildExchange.toLowerCase().includes("hyperliquid") ? "hyperliquid" : "bybit";
    let url = `/api/build-arbitrage?exchange=${exchParam}&symbol=${currentBuildSymbol}&spot_symbol=${currentBuildSpotSymbol}&raw_spot_pair=${currentBuildRawSpotPair}&dry_run=true&force=${force}&execution_mode=${execMode}`;
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

            // Render Workflow & Savings Banner
            if (workflowBanner) {
                workflowBanner.classList.remove("hidden");
                const savingsHtml = (plan.fee_savings_pct && plan.fee_savings_pct > 0)
                    ? `<span class="savings-tag">💰 节省手续费: +${plan.fee_savings_pct.toFixed(3)}% (约 $${(plan.fee_savings_usd || 0).toFixed(2)})</span>`
                    : `<span class="savings-tag" style="background:rgba(255,255,255,0.08); color:#94a3b8; border-color:transparent;">基准全 Taker 摩擦</span>`;

                workflowBanner.innerHTML = `
                    <div class="workflow-header">
                        <span>⚙️ 策略架构: <b>${execMode === 'maker_taker' ? 'Maker-Taker 触发式对冲' : 'Taker-Taker 全市价快速开仓'}</b></span>
                        ${savingsHtml}
                    </div>
                    <div style="font-size:12px; color:var(--text-secondary); line-height:1.4;">
                        ${plan.workflow_desc || ''}
                    </div>
                `;
            }

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
                const feeStr = order.fee_pct !== undefined ? `${order.fee_pct.toFixed(3)}%` : "-";
                const roleBadge = order.role ? `<br><span style="font-size:10px; color:#94a3b8; font-weight:normal;">${order.role}</span>` : "";
                const tifStr = order.time_in_force ? ` <span style="font-size:10px; opacity:0.75;">(${order.time_in_force})</span>` : "";

                html += `
                    <tr>
                        <td style="font-weight:700; color:var(--accent-cyan);">${order.leg}${roleBadge}</td>
                        <td><span class="exchange-badge exchange-hl">${order.category.toUpperCase()}</span></td>
                        <td style="font-weight:600;">${order.symbol}</td>
                        <td style="font-weight:700; color:${order.side === 'Buy' ? '#10b981' : '#ef4444'};">${order.side}</td>
                        <td><b>${order.order_type}</b>${tifStr}</td>
                        <td style="font-family:var(--font-mono); font-weight:600;">${order.quantity_str}</td>
                        <td style="font-family:var(--font-mono);">$${formatPrice(order.target_price || order.expected_vwap)}</td>
                        <td><span class="badge-rate ${slip <= 0.2 ? 'rate-positive' : ''}">${slipStr}</span></td>
                        <td style="font-family:var(--font-mono); color:#38bdf8;">${feeStr}</td>
                    </tr>
                `;
            });
            tbody.innerHTML = html;
        } else {
            tbody.innerHTML = `<tr><td colspan="9" class="loading-cell">无法生成 Try-Run 演练计划</td></tr>`;
        }
    } catch (err) {
        console.error("Fetch build try-run error:", err);
        tbody.innerHTML = `<tr><td colspan="9" class="loading-cell">网络请求失败</td></tr>`;
    }
}



