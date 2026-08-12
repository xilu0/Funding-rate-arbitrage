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

async function fetchData() {
    const statusText = document.getElementById("sync-status");
    statusText.textContent = "更新数据中...";

    const spotFee = document.getElementById("spot-fee-input").value;
    const perpFee = document.getElementById("perp-fee-input").value;
    const maxSpread = document.getElementById("max-spread-input").value;
    const enableAliases = document.getElementById("alias-toggle").checked;

    const url = `/api/funding-rates?spot_fee=${spotFee}&perp_fee=${perpFee}&max_spread=${maxSpread}&enable_aliases=${enableAliases}`;

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
        }
    } catch (err) {
        console.error("Fetch error:", err);
        statusText.textContent = "网络连接错误";
    }
}

function updateStatsOverview(data) {
    if (!data || data.length === 0) return;

    document.getElementById("pair-count-val").textContent = data.length;

    // Top APR
    const topApr = [...data].sort((a, b) => b.apr_pct - a.apr_pct)[0];
    if (topApr) {
        document.getElementById("top-apr-val").textContent = `${topApr.apr_pct >= 0 ? '+' : ''}${topApr.apr_pct.toFixed(2)}%`;
        document.getElementById("top-apr-coin").textContent = `${topApr.coin} (${topApr.spot_symbol})`;
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
        document.getElementById("fast-payback-coin").textContent = `${fastest.coin} (${fastest.spot_symbol})`;
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
        document.getElementById("top-vol-coin").textContent = `${topVol.coin}`;
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
        tbody.innerHTML = `<tr><td colspan="11" class="loading-cell">未找到匹配的套利标的</td></tr>`;
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

        html += `
            <tr>
                <td><span class="rank-badge">${index + 1}</span></td>
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
            </tr>
        `;
    });

    tbody.innerHTML = html;
}

function formatPrice(px) {
    if (!px || px === 0) return "0.00";
    if (px < 0.001) return px.toFixed(6);
    if (px < 1) return px.toFixed(4);
    if (px < 1000) return px.toFixed(2);
    return px.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
}
