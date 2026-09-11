#!/usr/bin/env python3
"""生成一个自包含的静态网页（index.html），把 daily_scan.py 扫描到的数据渲染成
朋友能直接打开看的网站——不需要服务器、不需要 Streamlit，纯静态 HTML+CSS+JS，
数据直接以 JSON 形式打包进页面里，配合 GitHub Pages 用。

用法：
    python daily_scan.py       # 先扫描，更新本地数据库
    python generate_site.py    # 再根据数据库最新内容生成 index.html
    git add -A && git commit -m "更新每日数据" && git push   # 推到 GitHub，Pages 自动更新

每天的完整流程见 README.md 的"每天怎么发布"一节。
"""

from __future__ import annotations

import json
from datetime import datetime

from astock_toolkit import config, db

OUT_PATH = "index.html"

STATUS_LABEL = {
    "watching": "观察中",
    "buy_signal": "回调至买点区间",
    "expired": "观察期结束",
}

TEMPLATE = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>A股底部首板看板</title>
<style>
  :root {
    --bg: #0d1117; --panel: #161b22; --border: #30363d;
    --text: #e6edf3; --dim: #8b949e; --accent: #58a6ff;
    --green: #3fb950; --yellow: #d29922; --red: #f85149;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font-family: -apple-system, "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
    line-height: 1.55;
  }
  .wrap { max-width: 1100px; margin: 0 auto; padding: 32px 20px 80px; }
  h1 { font-size: 26px; margin: 0 0 6px; }
  h2 { font-size: 18px; margin: 36px 0 12px; border-left: 4px solid var(--accent); padding-left: 10px; }
  .meta { color: var(--dim); font-size: 13px; margin-bottom: 24px; }
  .stat-row { display: flex; flex-wrap: wrap; gap: 12px; margin: 16px 0 28px; }
  .stat {
    background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
    padding: 12px 18px; min-width: 140px;
  }
  .stat .label { font-size: 12px; color: var(--dim); }
  .stat .value { font-size: 20px; font-weight: 600; margin-top: 2px; }
  table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
  th, td {
    text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border);
    white-space: nowrap;
  }
  th { color: var(--dim); font-weight: 500; position: sticky; top: 0; background: var(--panel); }
  td.note, th.note { white-space: normal; min-width: 260px; }
  tr:hover td { background: #1c2129; }
  .table-scroll {
    overflow-x: auto; border: 1px solid var(--border); border-radius: 8px;
    background: var(--panel);
  }
  .empty { color: var(--dim); padding: 16px; font-size: 13.5px; }
  .badge {
    display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px;
  }
  .badge.watching { background: #1f6feb33; color: var(--accent); }
  .badge.buy_signal { background: #3fb95033; color: var(--green); }
  .badge.expired { background: #8b949e33; color: var(--dim); }
  select {
    background: var(--panel); color: var(--text); border: 1px solid var(--border);
    border-radius: 6px; padding: 6px 10px; font-size: 13px; margin-bottom: 10px;
  }
  .btn {
    background: var(--panel); color: var(--accent); border: 1px solid var(--border);
    border-radius: 6px; padding: 6px 14px; font-size: 13px; cursor: pointer;
    margin: 6px 0 16px;
  }
  .btn:hover { background: #1c2129; }
  /* A股习惯：涨（+）用红色，跌（-）用绿色，跟欧美惯例相反 */
  .pct-pos { color: var(--red); }
  .pct-neg { color: var(--green); }
  footer { color: var(--dim); font-size: 12px; margin-top: 48px; }
  tr.clickable { cursor: pointer; }
  .modal-overlay {
    display: none; position: fixed; inset: 0; background: #000000aa;
    z-index: 100; align-items: center; justify-content: center; padding: 20px;
  }
  .modal-overlay.open { display: flex; }
  .modal-box {
    background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 20px; max-width: 820px; width: 100%; max-height: 90vh; overflow-y: auto;
  }
  .modal-box h3 { margin: 0 0 4px; font-size: 17px; }
  .modal-box .modal-sub { color: var(--dim); font-size: 12.5px; margin-bottom: 14px; }
  .modal-close {
    float: right; background: none; border: none; color: var(--dim); font-size: 20px;
    cursor: pointer; line-height: 1;
  }
  .modal-close:hover { color: var(--text); }
  .chart-legend { display: flex; gap: 16px; font-size: 12px; color: var(--dim); margin-top: 8px; }
  .chart-legend span.dot { display: inline-block; width: 9px; height: 9px; border-radius: 2px; margin-right: 4px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>📈 A股底部首板看板</h1>
  <div class="meta" id="meta"></div>

  <div class="stat-row" id="stats"></div>

  <button class="btn" id="download-pool1">📥 下载股票池一数据（CSV）</button>

  <h2>📊 板块热门度</h2>
  <div id="board-table"></div>

  <h2 id="new-heading">🆕 本次扫描新增</h2>
  <div id="new-table"></div>

  <h2>🎯 重点关注：回调至买点区间</h2>
  <div class="meta" id="macd-meta"></div>
  <div id="buy-table"></div>

  <h2>📋 完整信号记录</h2>
  <select id="status-filter"></select>
  <div id="all-table"></div>

  <h1 style="margin-top:56px">🔍 股票池二：地量后放量突破</h1>
  <div class="meta" id="vs-meta"></div>
  <button class="btn" id="download-pool2">📥 下载股票池二数据（CSV）</button>
  <div id="vs-table"></div>

  <footer>纯决策辅助，不构成投资建议，买卖操作请自行判断。数据来源 AKShare，每日更新。</footer>
</div>

<div class="modal-overlay" id="chart-modal">
  <div class="modal-box">
    <button class="modal-close" id="chart-modal-close">✕</button>
    <h3 id="chart-title"></h3>
    <div class="modal-sub" id="chart-sub"></div>
    <div id="chart-container"></div>
    <div class="chart-legend">
      <span><span class="dot" style="background:var(--red)"></span>收盘价高于开盘价（阳线）</span>
      <span><span class="dot" style="background:var(--green)"></span>收盘价低于开盘价（阴线）</span>
      <span><span class="dot" style="background:var(--accent)"></span>信号触发日</span>
    </div>
  </div>
</div>

<script>
const DATA = __DATA_JSON__;
const STATUS_LABEL = { watching: "观察中", buy_signal: "回调至买点区间", expired: "观察期结束" };

function el(tag, attrs, children) {
  const e = document.createElement(tag);
  if (attrs) for (const k in attrs) {
    if (k === "class") e.className = attrs[k];
    else if (k === "html") e.innerHTML = attrs[k];
    else e.setAttribute(k, attrs[k]);
  }
  (children || []).forEach(c => e.appendChild(c));
  return e;
}
function text(tag, s, attrs) { return el(tag, attrs, [document.createTextNode(s == null ? "" : s)]); }

function renderTable(container, rows, columns, onRowClick) {
  container.innerHTML = "";
  if (!rows || rows.length === 0) {
    container.appendChild(el("div", { class: "empty" }, [document.createTextNode("暂无数据。")]));
    return;
  }
  const scroll = el("div", { class: "table-scroll" });
  const table = el("table");
  const thead = el("thead");
  const headRow = el("tr");
  columns.forEach(c => headRow.appendChild(text("th", c.label, c.cls ? { class: c.cls } : null)));
  thead.appendChild(headRow);
  table.appendChild(thead);
  const tbody = el("tbody");
  rows.forEach(row => {
    const tr = el("tr", onRowClick ? { class: "clickable", title: "点击查看K线图" } : null);
    columns.forEach(c => {
      const val = c.render ? c.render(row) : (row[c.key] == null ? "" : row[c.key]);
      if (val instanceof Node) {
        tr.appendChild(el("td", c.cls ? { class: c.cls } : null, [val]));
      } else {
        tr.appendChild(text("td", val, c.cls ? { class: c.cls } : null));
      }
    });
    if (onRowClick) tr.addEventListener("click", () => onRowClick(row));
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  scroll.appendChild(table);
  container.appendChild(scroll);
}

function statusBadge(status) {
  const span = el("span", { class: "badge " + status });
  span.textContent = STATUS_LABEL[status] || status;
  return span;
}

function pctCell(v) {
  if (v == null || v === "") return "";
  const n = Number(v);
  const span = el("span", { class: n >= 0 ? "pct-pos" : "pct-neg" });
  span.textContent = (n >= 0 ? "+" : "") + n.toFixed(2) + "%";
  return span;
}

// ---------------------------------------------------------------------------
// K线图：点一行弹出浮层，用纯SVG手画蜡烛图（不依赖任何外部图表库），数据是
// generate_site.py 打包进 DATA.price_history 的本地历史行情缓存（扫描时早就
// 拉取过了，这里只是重新展示，不会触发任何新的网络请求）。
// ---------------------------------------------------------------------------
function drawCandlestickSVG(rows, triggerDate) {
  // rows: [[date, open, high, low, close, volume], ...] 按日期升序
  const W = 780, H = 340, padL = 54, padR = 12, padT = 12, padB = 26;
  const volH = 60; // 底部成交量条区域高度
  const plotH = H - padT - padB - volH - 8;
  const plotW = W - padL - padR;
  const n = rows.length;
  const highs = rows.map(r => r[2]);
  const lows = rows.map(r => r[3]);
  const vols = rows.map(r => r[5] || 0);
  const maxP = Math.max(...highs);
  const minP = Math.min(...lows);
  const priceRange = (maxP - minP) || 1;
  const maxVol = Math.max(...vols) || 1;
  const barW = plotW / n;
  const bodyW = Math.max(barW * 0.62, 1);
  const x = i => padL + i * barW + barW / 2;
  const y = p => padT + plotH - ((p - minP) / priceRange) * plotH;
  const volY0 = padT + plotH + 8;
  const yVol = v => volY0 + volH - (v / maxVol) * volH;

  let svg = `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block;background:var(--panel)">`;

  // 价格网格线 + 左侧刻度（高/中/低三条）
  [maxP, (maxP + minP) / 2, minP].forEach(p => {
    const yy = y(p);
    svg += `<line x1="${padL}" y1="${yy}" x2="${W - padR}" y2="${yy}" stroke="var(--border)" stroke-width="1"/>`;
    svg += `<text x="4" y="${yy + 4}" font-size="10" fill="var(--dim)">${p.toFixed(2)}</text>`;
  });

  rows.forEach((row, i) => {
    const [date, o, h, l, c] = row;
    const vol = row[5] || 0;
    const isUp = c >= o; // A股：阳线（涨）红色，阴线（跌）绿色
    const color = isUp ? "var(--red)" : "var(--green)";
    const cx = x(i);
    svg += `<line x1="${cx}" y1="${y(h)}" x2="${cx}" y2="${y(l)}" stroke="${color}" stroke-width="1"/>`;
    const bodyTop = y(Math.max(o, c));
    const bodyBot = y(Math.min(o, c));
    const bodyH = Math.max(bodyBot - bodyTop, 1);
    svg += `<rect x="${cx - bodyW / 2}" y="${bodyTop}" width="${bodyW}" height="${bodyH}" fill="${color}"/>`;
    // 成交量条
    const vh = Math.max((vol / maxVol) * volH, 1);
    svg += `<rect x="${cx - bodyW / 2}" y="${yVol(vol)}" width="${bodyW}" height="${vh}" fill="${color}" opacity="0.55"/>`;
    if (date === triggerDate) {
      svg += `<line x1="${cx}" y1="${padT}" x2="${cx}" y2="${volY0 + volH}" stroke="var(--accent)" stroke-width="1.2" stroke-dasharray="3,3"/>`;
    }
  });

  // 底部日期刻度：首/中/末三个
  [0, Math.floor((n - 1) / 2), n - 1].forEach(i => {
    if (i < 0 || i >= n) return;
    svg += `<text x="${x(i)}" y="${H - 6}" font-size="10" fill="var(--dim)" text-anchor="middle">${rows[i][0].slice(5)}</text>`;
  });

  svg += `</svg>`;
  return svg;
}

// ---------------------------------------------------------------------------
// 实时行情：弹窗打开后，除了先显示打包进网页的历史缓存K线，还会额外去新浪的
// 实时行情接口（hq.sinajs.cn）取一下最新价，把"今天"这根K线更新成实时的——
// 这是这个网站唯一会发起的外部网络请求（其余都是纯静态、打包好的数据）。走的是
// <script src> 标签加载（不是 fetch），因为这个接口没有开 CORS，用 fetch 直接
// 会被浏览器拦掉；用 <script> 标签加载不受 CORS 限制，这也是很多网页"实时行情"
// 小组件的老办法。取不到（比如接口不可用、网络问题、被浏览器扩展拦截）就什么都
// 不做，只显示历史缓存数据，不影响其它功能。
// ---------------------------------------------------------------------------
function marketPrefix(code) {
  if (/^(60|68|9)/.test(code)) return "sh";
  if (/^(00|30|20)/.test(code)) return "sz";
  if (/^(8|4|92)/.test(code)) return "bj";
  return "sh";
}
let _liveQuoteSeq = 0;
function fetchLiveQuote(code, cb) {
  const seq = ++_liveQuoteSeq;
  const prefixed = marketPrefix(code) + code;
  const varName = "hq_str_" + prefixed;
  const scriptId = "live-quote-script";
  const old = document.getElementById(scriptId);
  if (old) old.remove();
  let done = false;
  const finish = (result) => {
    if (done || seq !== _liveQuoteSeq) return; // 已经被新的一次点击取代，丢弃这次结果
    done = true;
    cb(result);
  };
  const timer = setTimeout(() => finish(null), 5000);
  const script = document.createElement("script");
  script.id = scriptId;
  script.src = "https://hq.sinajs.cn/list=" + prefixed + "&_=" + Date.now();
  script.onload = () => {
    clearTimeout(timer);
    const raw = window[varName];
    if (!raw) { finish(null); return; }
    const parts = raw.split(",");
    if (parts.length < 32 || !parts[3]) { finish(null); return; }
    finish({
      open: parseFloat(parts[1]), high: parseFloat(parts[4]), low: parseFloat(parts[5]),
      close: parseFloat(parts[3]), volume: parseFloat(parts[8]), // 新浪这里也是"股"，跟历史缓存单位一致，不用换算
      date: parts[30], time: parts[31],
    });
  };
  script.onerror = () => { clearTimeout(timer); finish(null); };
  document.head.appendChild(script);
}

function openChart(code, name, triggerDate) {
  const cached = (DATA.price_history || {})[code] || [];
  document.getElementById("chart-title").textContent = code + "  " + (name || "");
  const subEl = document.getElementById("chart-sub");
  const container = document.getElementById("chart-container");

  function render(rows, liveNote) {
    const base = triggerDate
      ? "信号触发日：" + triggerDate + "（图中蓝色虚线标注）　·　最近" + rows.length + "个交易日"
      : "最近" + rows.length + "个交易日";
    subEl.textContent = base + (liveNote ? "　·　" + liveNote : "");
    container.innerHTML = rows.length
      ? drawCandlestickSVG(rows, triggerDate)
      : '<div class="empty">暂无K线数据（可能是本地历史行情缓存还没有这只股票）。</div>';
  }

  render(cached, "🔄 正在获取实时行情…");
  document.getElementById("chart-modal").classList.add("open");

  fetchLiveQuote(code, (live) => {
    if (!live || !(live.close > 0)) {
      render(cached, cached.length ? "实时行情获取失败，以下是最近一次扫描缓存的数据" : "");
      return;
    }
    const liveBar = [live.date, live.open, live.high, live.low, live.close, live.volume];
    let merged = cached;
    if (cached.length && cached[cached.length - 1][0] === live.date) {
      merged = cached.slice(0, -1).concat([liveBar]); // 今天已经有一根缓存的，用实时数据覆盖
    } else if (!cached.length || live.date >= cached[cached.length - 1][0]) {
      merged = cached.concat([liveBar]); // 缓存里还没有今天，追加一根
    }
    render(merged, "🔴 实时 " + live.date + " " + (live.time || ""));
  });
}
function closeChart() {
  document.getElementById("chart-modal").classList.remove("open");
}
document.getElementById("chart-modal-close").addEventListener("click", closeChart);
document.getElementById("chart-modal").addEventListener("click", (e) => {
  if (e.target.id === "chart-modal") closeChart();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeChart();
});
function withChartClick(row) { openChart(row.code, row.name, row.trigger_date); }

document.getElementById("meta").textContent =
  "最后扫描时间：" + (DATA.last_scan_at || "无") + "　·　扫描范围：" + (DATA.universe_count || "无");

const stats = document.getElementById("stats");
[
  ["累计信号总数", DATA.signals.length + " 条"],
  ["观察中", DATA.signals.filter(s => s.status === "watching").length + " 只"],
  ["买点区间", DATA.signals.filter(s => s.status === "buy_signal").length + " 只"],
].forEach(([label, value]) => {
  stats.appendChild(el("div", { class: "stat" }, [
    el("div", { class: "label" }, [document.createTextNode(label)]),
    el("div", { class: "value" }, [document.createTextNode(value)]),
  ]));
});

renderTable(document.getElementById("board-table"), DATA.board_ranking, [
  { key: "board", label: "板块" },
  { label: "涨跌幅", render: r => pctCell(r.pct_chg) },
  { key: "group", label: "关注分组" },
  { key: "leader_name", label: "领涨股" },
]);

document.getElementById("macd-meta").textContent =
  "MACD确认：近" + DATA.macd_lookback_days + "个交易日内出现过金叉，且金叉后DIF-DEA持续向上发散——只是额外标注，不影响是否进入这个列表。";

function macdCell(row) {
  if (row.macd_confirmed === 1 || row.macd_confirmed === true) return "✅ 确认";
  if (row.macd_confirmed === 0 || row.macd_confirmed === false) return "❌ 未确认";
  return "－";
}

const scanDate = (DATA.last_scan_at || "").slice(0, 10);
document.getElementById("new-heading").textContent = "🆕 本次扫描新增（" + scanDate + "）";
const newSignals = DATA.signals.filter(s => (s.added_at || "").slice(0, 10) === scanDate);
const signalColumns = [
  { key: "code", label: "代码" },
  { key: "name", label: "名称" },
  { key: "board", label: "板块" },
  { label: "板块涨跌幅", render: r => pctCell(r.board_pct_chg) },
  { label: "首板日", key: "trigger_date" },
  { label: "首板价", render: r => r.trigger_price != null ? Number(r.trigger_price).toFixed(2) : "" },
  { label: "状态", render: r => statusBadge(r.status) },
  { label: "MACD确认", render: macdCell },
  { label: "备注", key: "note", cls: "note" },
];
renderTable(document.getElementById("new-table"), newSignals, signalColumns, withChartClick);

const buySignals = DATA.signals.filter(s => s.status === "buy_signal");
renderTable(document.getElementById("buy-table"), buySignals, [
  { key: "code", label: "代码" },
  { key: "name", label: "名称" },
  { key: "board", label: "板块" },
  { label: "板块涨跌幅", render: r => pctCell(r.board_pct_chg) },
  { label: "首板日", key: "trigger_date" },
  { label: "首板价", render: r => r.trigger_price != null ? Number(r.trigger_price).toFixed(2) : "" },
  { label: "涨停后高点", render: r => r.post_high != null ? Number(r.post_high).toFixed(2) : "" },
  { label: "MACD确认", render: macdCell },
  { label: "MACD说明", key: "macd_note", cls: "note" },
  { label: "备注", key: "note", cls: "note" },
], withChartClick);

const filterSel = document.getElementById("status-filter");
[["all", "全部"], ["watching", "观察中"], ["buy_signal", "买点区间"], ["expired", "观察期结束"]]
  .forEach(([val, label]) => filterSel.appendChild(el("option", { value: val }, [document.createTextNode(label)])));
function renderAll() {
  const v = filterSel.value;
  const rows = (v === "all" ? DATA.signals : DATA.signals.filter(s => s.status === v))
    .slice().sort((a, b) => (b.trigger_date || "").localeCompare(a.trigger_date || ""));
  renderTable(document.getElementById("all-table"), rows, signalColumns, withChartClick);
}
filterSel.addEventListener("change", renderAll);
renderAll();

document.getElementById("vs-meta").textContent = DATA.vs_last_scan_at
  ? "最后扫描时间：" + DATA.vs_last_scan_at + "　·　扫描范围：" + (DATA.vs_universe_count || "无")
  : "还没跑过这个股票池的扫描（python scan_volume_surge.py）。";
renderTable(document.getElementById("vs-table"), DATA.volume_surge_signals, [
  { key: "code", label: "代码" },
  { key: "name", label: "名称" },
  { key: "trigger_date", label: "触发日" },
  { label: "触发价", render: r => r.trigger_price != null ? Number(r.trigger_price).toFixed(2) : "" },
  { label: "前30日均换手", render: r => r.avg_turnover_30d != null ? Number(r.avg_turnover_30d).toFixed(2) + "%" : "" },
  { label: "当日换手", render: r => r.today_turnover != null ? Number(r.today_turnover).toFixed(2) + "%" : "" },
  { label: "前30日均价", render: r => r.avg_price_30d != null ? Number(r.avg_price_30d).toFixed(2) : "" },
  { label: "备注", key: "note", cls: "note" },
], withChartClick);

// ---------------------------------------------------------------------------
// 下载按钮：把当前页面打包的数据导出成 CSV，纯前端生成（Blob + 临时<a>），不需要
// 服务器。导出的是这次网页打包的全部数据（不受"完整信号记录"筛选框影响），文件名
// 按扫描日期命名。Excel 打开中文不乱码需要开头加 UTF-8 BOM。
// ---------------------------------------------------------------------------
function csvEscape(v) {
  if (v == null) return "";
  const s = String(v);
  return /[",\\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}
function toCSV(rows, columns) {
  const header = columns.map(c => csvEscape(c.label)).join(",");
  const lines = rows.map(row => columns.map(c => csvEscape(c.value(row))).join(","));
  return "﻿" + [header].concat(lines).join("\\r\\n");
}
function downloadCSV(rows, columns, filename) {
  const blob = new Blob([toCSV(rows, columns)], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = el("a", { href: url, download: filename });
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

const pool1CsvColumns = [
  { label: "代码", value: r => r.code },
  { label: "名称", value: r => r.name },
  { label: "板块", value: r => r.board },
  { label: "板块涨跌幅(%)", value: r => r.board_pct_chg },
  { label: "首板日", value: r => r.trigger_date },
  { label: "首板价", value: r => r.trigger_price },
  { label: "涨停后高点", value: r => r.post_high },
  { label: "状态", value: r => STATUS_LABEL[r.status] || r.status },
  { label: "MACD确认", value: r => (r.macd_confirmed === 1 || r.macd_confirmed === true) ? "是"
      : ((r.macd_confirmed === 0 || r.macd_confirmed === false) ? "否" : "") },
  { label: "MACD说明", value: r => r.macd_note },
  { label: "备注", value: r => r.note },
  { label: "新增时间", value: r => r.added_at },
  { label: "更新时间", value: r => r.updated_at },
];
const pool2CsvColumns = [
  { label: "代码", value: r => r.code },
  { label: "名称", value: r => r.name },
  { label: "触发日", value: r => r.trigger_date },
  { label: "触发价", value: r => r.trigger_price },
  { label: "前30日均换手(%)", value: r => r.avg_turnover_30d },
  { label: "当日换手(%)", value: r => r.today_turnover },
  { label: "前30日均价", value: r => r.avg_price_30d },
  { label: "备注", value: r => r.note },
  { label: "添加时间", value: r => r.added_at },
];

document.getElementById("download-pool1").addEventListener("click", () => {
  const d = scanDate || "无日期";
  downloadCSV(DATA.signals, pool1CsvColumns, "astock_底部首板_" + d + ".csv");
});
document.getElementById("download-pool2").addEventListener("click", () => {
  const d = (DATA.vs_last_scan_at || "").slice(0, 10) || "无日期";
  downloadCSV(DATA.volume_surge_signals, pool2CsvColumns, "astock_地量放量_" + d + ".csv");
});
</script>
</body>
</html>
"""


PRICE_HISTORY_DAYS = 120  # 给K线图用，取每只股票最近N个交易日的本地缓存行情


def build_price_history(codes: set[str]) -> dict:
    """给点进去看K线图这个功能打包数据。数据来自 price_cache 本地缓存——扫描时
    （_get_history_with_cache）早就拉取过了，这里只是重新读出来打包进网页，不会
    再发起任何新的网络请求，跟扫描速度完全无关。

    压缩成 [date, open, high, low, close, volume] 数组而不是带字段名的对象，
    省掉重复的key名字，网页体积能小不少。
    """
    history = {}
    for code in codes:
        rows = db.get_cached_history(code)
        if not rows:
            continue
        rows = rows[-PRICE_HISTORY_DAYS:]
        history[code] = [
            [r["date"], r["open"], r["high"], r["low"], r["close"], r["volume"]]
            for r in rows
        ]
    return history


def build_data() -> dict:
    last_scan_at = db.get_setting("last_scan_at") or ""
    universe_count = db.get_setting("last_scan_universe_count") or ""
    ranking_json = db.get_setting("last_board_ranking_json")
    board_ranking = json.loads(ranking_json) if ranking_json else []
    signals = db.list_watchlist()
    volume_surge_signals = db.list_volume_surge()
    codes = {r["code"] for r in signals} | {r["code"] for r in volume_surge_signals}
    return {
        "last_scan_at": last_scan_at,
        "universe_count": universe_count,
        "board_ranking": board_ranking,
        "signals": signals,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "macd_lookback_days": config.MACD_CROSS_LOOKBACK_DAYS,
        "vs_last_scan_at": db.get_setting("last_volume_surge_scan_at") or "",
        "vs_universe_count": db.get_setting("last_volume_surge_universe_count") or "",
        "volume_surge_signals": volume_surge_signals,
        "price_history": build_price_history(codes),
    }


def main():
    db.init_db()
    data = build_data()
    html = TEMPLATE.replace("__DATA_JSON__", json.dumps(data, ensure_ascii=False))
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"已生成 {OUT_PATH}（{len(data['signals'])} 条信号，最后扫描时间 {data['last_scan_at'] or '无'}）")


if __name__ == "__main__":
    main()
