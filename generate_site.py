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
  .chart-tabs { display: flex; gap: 8px; margin-bottom: 10px; }
  .chart-tab {
    background: var(--bg); color: var(--dim); border: 1px solid var(--border);
    border-radius: 6px; padding: 4px 12px; font-size: 12.5px; cursor: pointer;
  }
  .chart-tab.active { color: var(--accent); border-color: var(--accent); }
  .quote-panel {
    display: grid; grid-template-columns: 1fr 1.3fr; gap: 12px; margin-bottom: 14px;
    background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px;
    font-size: 12.5px;
  }
  .quote-book table { width: 100%; border-collapse: collapse; font-size: 12px; }
  .quote-book td { padding: 1px 4px; white-space: nowrap; }
  .quote-book td.lbl { color: var(--dim); }
  .quote-book td.vol { color: var(--dim); text-align: right; }
  .quote-stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 3px 10px; align-content: start; }
  .quote-stats .qs { display: flex; justify-content: space-between; gap: 6px; white-space: nowrap; }
  .quote-stats .qs .lbl { color: var(--dim); }
  .fund-panel {
    display: grid; grid-template-columns: 1fr 1.2fr; gap: 12px; margin-bottom: 14px;
    background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px;
    font-size: 12.5px;
  }
  .fund-stats { display: grid; grid-template-columns: repeat(2, 1fr); gap: 3px 10px; align-content: start; }
  .fund-stats .qs { display: flex; justify-content: space-between; gap: 6px; white-space: nowrap; }
  .fund-stats .qs .lbl { color: var(--dim); }
  .fund-trend-title { font-size: 11px; color: var(--dim); margin-bottom: 4px; }
  @media (max-width: 560px) {
    .quote-panel { grid-template-columns: 1fr; }
    .quote-stats { grid-template-columns: repeat(2, 1fr); }
    .fund-panel { grid-template-columns: 1fr; }
  }
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
    <div id="quote-panel"></div>
    <div id="fundamentals-panel"></div>
    <div class="chart-tabs">
      <button class="chart-tab active" id="tab-daily">日K线</button>
      <button class="chart-tab" id="tab-intraday">今日分时</button>
    </div>
    <div id="chart-container"></div>
    <div class="chart-legend" id="chart-legend-daily">
      <span><span class="dot" style="background:var(--red)"></span>收盘价高于开盘价（阳线）</span>
      <span><span class="dot" style="background:var(--green)"></span>收盘价低于开盘价（阴线）</span>
      <span><span class="dot" style="background:var(--accent)"></span>信号触发日</span>
    </div>
    <div class="chart-legend" id="chart-legend-intraday" style="display:none">
      <span><span class="dot" style="background:var(--red)"></span>现价高于昨收</span>
      <span><span class="dot" style="background:var(--green)"></span>现价低于昨收</span>
      <span><span class="dot" style="background:var(--dim)"></span>昨收参考线</span>
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
  rows.forEach((row, index) => {
    const tr = el("tr", onRowClick ? { class: "clickable", title: "点击查看K线图（弹窗里滚轮可切换上/下一只）" } : null);
    columns.forEach(c => {
      const val = c.render ? c.render(row) : (row[c.key] == null ? "" : row[c.key]);
      if (val instanceof Node) {
        tr.appendChild(el("td", c.cls ? { class: c.cls } : null, [val]));
      } else {
        tr.appendChild(text("td", val, c.cls ? { class: c.cls } : null));
      }
    });
    if (onRowClick) tr.addEventListener("click", () => onRowClick(row, rows, index));
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
// 五档买卖盘 + 实时行情统计面板：完全复用 fetchLiveQuote 已经拿到的那份数据
// （日K线"实时快照"本来就在取这个接口），不会额外发请求。涨停/跌停价是按公开
// 的涨跌幅限制规则客户端算的（主板/中小板±10%，创业板300xxx/科创板688xxx
// ±20%），四舍五入到分——跟真实撮合的取整规则可能有个别分钱的误差，仅供参考。
// ---------------------------------------------------------------------------
function limitPctForCode(code) {
  return /^(300|301|688|689)/.test(code) ? 0.20 : 0.10;
}
function fmtNum(v, digits) {
  return (v == null || isNaN(v)) ? "－" : v.toFixed(digits == null ? 2 : digits);
}
function pctSpan(v, digits) {
  if (v == null || isNaN(v)) return text("span", "－");
  const span = el("span", { class: v >= 0 ? "pct-pos" : "pct-neg" });
  span.textContent = (v >= 0 ? "+" : "") + v.toFixed(digits == null ? 2 : digits) + "%";
  return span;
}
function renderQuotePanel(container, quote, code) {
  if (!quote) { container.innerHTML = ""; return; }
  container.innerHTML = "";
  const panel = el("div", { class: "quote-panel" });

  // 左边：五档买卖盘（卖五在最上面，买五在最下面，跟真实交易软件的排法一致）；
  // 价格按A股习惯上色：高于昨收红、低于昨收绿。
  const bookDiv = el("div", { class: "quote-book" });
  const bookTable = el("table");
  const bookBody = el("tbody");
  function priceCell(price) {
    if (price == null || isNaN(price)) return text("td", "－");
    const cls = quote.prevClose ? (price >= quote.prevClose ? "pct-pos" : "pct-neg") : "";
    return el("td", cls ? { class: cls } : null, [document.createTextNode(price.toFixed(2))]);
  }
  quote.asks.slice().reverse().forEach((lv, i) => {
    const rank = 5 - i;
    const tr = el("tr");
    tr.appendChild(text("td", "卖" + rank, { class: "lbl" }));
    tr.appendChild(priceCell(lv.price));
    tr.appendChild(text("td", lv.vol ? String(lv.vol) : "－", { class: "vol" }));
    bookBody.appendChild(tr);
  });
  quote.bids.forEach((lv, i) => {
    const tr = el("tr");
    tr.appendChild(text("td", "买" + (i + 1), { class: "lbl" }));
    tr.appendChild(priceCell(lv.price));
    tr.appendChild(text("td", lv.vol ? String(lv.vol) : "－", { class: "vol" }));
    bookBody.appendChild(tr);
  });
  bookTable.appendChild(bookBody);
  bookDiv.appendChild(bookTable);
  panel.appendChild(bookDiv);

  // 右边：统计信息网格
  const limitPct = limitPctForCode(code);
  const limitUp = quote.prevClose ? Math.round(quote.prevClose * (1 + limitPct) * 100) / 100 : null;
  const limitDown = quote.prevClose ? Math.round(quote.prevClose * (1 - limitPct) * 100) / 100 : null;
  const weiBi = (quote.totalBidVol + quote.totalAskVol) > 0
    ? (quote.totalBidVol - quote.totalAskVol) / (quote.totalBidVol + quote.totalAskVol) * 100 : null;
  const weiCha = quote.totalBidVol - quote.totalAskVol;
  const statsDiv = el("div", { class: "quote-stats" });
  function stat(label, valueNode) {
    const row = el("div", { class: "qs" });
    row.appendChild(text("span", label, { class: "lbl" }));
    row.appendChild(valueNode instanceof Node ? valueNode : text("span", valueNode));
    statsDiv.appendChild(row);
  }
  stat("现价", el("span", { class: quote.prevClose && quote.close >= quote.prevClose ? "pct-pos" : "pct-neg" }, [document.createTextNode(fmtNum(quote.close))]));
  stat("涨幅", pctSpan(quote.pctChg));
  stat("今开", text("span", fmtNum(quote.open)));
  stat("昨收", text("span", fmtNum(quote.prevClose)));
  stat("最高", text("span", fmtNum(quote.high)));
  stat("最低", text("span", fmtNum(quote.low)));
  stat("涨停", text("span", fmtNum(limitUp)));
  stat("跌停", text("span", fmtNum(limitDown)));
  stat("换手", text("span", fmtNum(quote.turnoverRate) + "%"));
  stat("总手", text("span", quote.volume ? (quote.volume / 100).toFixed(0) : "－"));
  stat("金额", text("span", quote.amountWan ? (quote.amountWan / 10000).toFixed(2) + "亿" : "－"));
  stat("委比", text("span", weiBi == null ? "－" : weiBi.toFixed(1) + "%"));
  stat("委差", text("span", weiCha == null ? "－" : String(weiCha)));
  stat("外盘", text("span", quote.waiPan ? String(quote.waiPan) : "－"));
  stat("内盘", text("span", quote.neiPan ? String(quote.neiPan) : "－"));
  stat("市盈率", text("span", fmtNum(quote.pe, 2)));
  stat("市净率", text("span", fmtNum(quote.pb, 2)));
  stat("总市值", text("span", quote.totalCapYi ? quote.totalCapYi.toFixed(2) + "亿" : "－"));
  stat("流通值", text("span", quote.floatCapYi ? quote.floatCapYi.toFixed(2) + "亿" : "－"));
  stat("总股本", text("span", quote.totalShares ? (quote.totalShares / 1e8).toFixed(2) + "亿" : "－"));
  stat("流通股", text("span", quote.floatShares ? (quote.floatShares / 1e8).toFixed(2) + "亿" : "－"));
  panel.appendChild(statsDiv);

  container.appendChild(panel);
}

// ---------------------------------------------------------------------------
// 基本面面板：净资产/净利润/毛利率/净利率/ROE/负债率 + 净利润趋势图。数据来自
// generate_site.py 打包进 DATA.fundamentals 的本地缓存（check_book_value.py
// 查财报时存下来的真实值，按季度更新，不是每次生成网页现查，更不会在浏览器里
// 发请求）。跟"日K线/今日分时"两个tab无关，一直显示。
// ---------------------------------------------------------------------------
function drawProfitTrendSVG(trend) {
  const W = 340, H = 130, padL = 34, padR = 8, padT = 10, padB = 20;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const values = trend.map((t) => t.net_profit / 1e8); // 换算成"亿元"
  const maxV = Math.max(...values, 0);
  const minV = Math.min(...values, 0);
  const range = (maxV - minV) || 1;
  const y = (v) => padT + plotH - ((v - minV) / range) * plotH;
  const zeroY = y(0);
  const n = values.length;
  const barW = Math.max((plotW / n) * 0.55, 3);
  const x = (i) => padL + ((i + 0.5) / n) * plotW;

  let svg = `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block">`;
  svg += `<line x1="${padL}" y1="${zeroY}" x2="${W - padR}" y2="${zeroY}" stroke="var(--border)" stroke-width="1"/>`;
  svg += `<text x="4" y="${y(maxV) + 4}" font-size="9" fill="var(--dim)">${maxV.toFixed(0)}</text>`;
  svg += `<text x="4" y="${y(minV) + 4}" font-size="9" fill="var(--dim)">${minV.toFixed(0)}</text>`;
  values.forEach((v, i) => {
    const cx = x(i);
    const barTop = y(Math.max(v, 0));
    const barBot = y(Math.min(v, 0));
    const color = v >= 0 ? "var(--red)" : "var(--green)"; // A股：盈利红、亏损绿
    svg += `<rect x="${cx - barW / 2}" y="${barTop}" width="${barW}" height="${Math.max(barBot - barTop, 1)}" fill="${color}"/>`;
    const p = trend[i].period;
    const label = p.slice(2, 4) + "." + p.slice(4, 6);
    svg += `<text x="${cx}" y="${H - 6}" font-size="9" fill="var(--dim)" text-anchor="middle">${label}</text>`;
  });
  svg += `</svg>`;
  return svg;
}

function renderFundamentalsPanel(container, fund) {
  container.innerHTML = "";
  if (!fund) return;
  const panel = el("div", { class: "fund-panel" });

  const statsDiv = el("div", { class: "fund-stats" });
  function stat(label, value) {
    const row = el("div", { class: "qs" });
    row.appendChild(text("span", label, { class: "lbl" }));
    row.appendChild(text("span", value));
    statsDiv.appendChild(row);
  }
  stat("每股净资产", fund.book_value_per_share != null ? fund.book_value_per_share.toFixed(2) + "元" : "－");
  stat("净利润", fund.net_profit != null ? (fund.net_profit / 1e8).toFixed(2) + "亿" : "－");
  stat("毛利率", fund.gross_margin != null ? fund.gross_margin.toFixed(2) + "%" : "－");
  stat("净利率", fund.net_margin != null ? fund.net_margin.toFixed(2) + "%" : "－");
  stat("ROE", fund.roe != null ? fund.roe.toFixed(2) + "%" : "－");
  stat("负债率", fund.debt_ratio != null ? fund.debt_ratio.toFixed(2) + "%" : "－");
  panel.appendChild(statsDiv);

  const trendDiv = el("div");
  const hasTrend = fund.profit_trend && fund.profit_trend.length > 0;
  trendDiv.appendChild(text("div", "净利润趋势" + (hasTrend ? "（近" + fund.profit_trend.length + "期，亿元）" : ""), { class: "fund-trend-title" }));
  const trendHost = el("div");
  if (hasTrend) {
    trendHost.innerHTML = drawProfitTrendSVG(fund.profit_trend);
  } else {
    trendHost.appendChild(el("div", { class: "empty" }, [document.createTextNode("暂无趋势数据。")]));
  }
  trendDiv.appendChild(trendHost);
  panel.appendChild(trendDiv);

  container.appendChild(panel);
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
// 实时行情：弹窗打开的那一刻，除了先显示打包进网页的历史缓存K线，还会额外去
// 腾讯的实时行情接口（qt.gtimg.cn）取一次最新价，把"今天"这根K线更新成那一刻的
// 快照——只在打开时取一次，不会持续每隔几秒自动刷新（这是有意的设计：这里要的
// 是"点开时对着当时盘面拍一张细致快照"，不是一个持续跳动的实时行情器）。这是
// 这个网站唯一会发起的外部网络请求（其余都是纯静态、打包好的数据）。
//
// 踩过的坑：一开始用的是新浪 hq.sinajs.cn，本地用 curl 带上
// `Referer: https://finance.sina.com.cn` 测试是通的，但真放到网页上全都失败——
// 因为新浪这个接口按 Referer 做了防盗链，只认自家域名发起的请求，而浏览器发起的
// 跨域请求，Referer 只会是网页自己的真实地址（比如这个GitHub Pages的域名），
// JS 没办法伪造成新浪自己的域名（这是浏览器强制的，改不了）。实测直接用 curl
// 模拟"从 GitHub Pages 域名请求"，新浪返回 403 Forbidden，而本地测试用假 Referer
// 蒙混过去看着是通的，这是本地测试没测对场景，不是网页代码本身的bug。
//
// 换成腾讯的接口后不认 Referer，而且直接带了 `access-control-allow-origin: *`
// （真正开了 CORS），所以这里改用标准的 fetch 而不是 <script> 标签硬凑。取不到
// （接口不可用、网络问题、超时、被浏览器扩展拦截）就什么都不做，只显示历史缓存
// 数据，不影响其它功能。
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
  const finish = (result) => { if (seq === _liveQuoteSeq) cb(result); };
  const controller = ("AbortController" in window) ? new AbortController() : null;
  const timer = setTimeout(() => { if (controller) controller.abort(); }, 5000);
  fetch("https://qt.gtimg.cn/q=" + prefixed + "&_=" + Date.now(),
        { cache: "no-store", signal: controller ? controller.signal : undefined })
    .then(r => r.text())
    .then(raw => {
      clearTimeout(timer);
      const marker = 'v_' + prefixed + '="';
      const idx = raw.indexOf(marker);
      if (idx === -1) { finish(null); return; }
      // 股票名称那个字段是GBK编码，fetch按UTF-8解码会花掉，但我们不需要那个字段，
      // 后面用到的都是纯数字字段，"~"分隔符本身是ASCII，不受编码影响，不用管它。
      const parts = raw.slice(idx + marker.length).split("~");
      if (parts.length < 47) { finish(null); return; }
      const close = parseFloat(parts[3]);
      const ts = parts[30] || "";
      if (!(close > 0) || ts.length < 14) { finish(null); return; }
      const prevClose = parseFloat(parts[4]);
      // 买一~买五 / 卖一~卖五：价格和"手数"两两一组，下标9开始。
      const book = (startIdx) => {
        const levels = [];
        for (let i = 0; i < 5; i++) {
          const p = parseFloat(parts[startIdx + i * 2]);
          const v = parseFloat(parts[startIdx + i * 2 + 1]);
          levels.push({ price: p, vol: v });
        }
        return levels;
      };
      const bids = book(9);
      const asks = book(19);
      const totalBidVol = bids.reduce((s, l) => s + (l.vol || 0), 0);
      const totalAskVol = asks.reduce((s, l) => s + (l.vol || 0), 0);
      finish({
        open: parseFloat(parts[5]), high: parseFloat(parts[33]), low: parseFloat(parts[34]),
        close, volume: parseFloat(parts[6]) * 100, // 腾讯这里是"手"，*100换算成"股"跟历史缓存对齐
        date: ts.slice(0, 4) + "-" + ts.slice(4, 6) + "-" + ts.slice(6, 8),
        time: ts.slice(8, 10) + ":" + ts.slice(10, 12) + ":" + ts.slice(12, 14),
        prevClose,
        pctChg: parseFloat(parts[32]),
        turnoverRate: parseFloat(parts[38]),
        pe: parseFloat(parts[39]),
        pb: parseFloat(parts[46]),
        floatCapYi: parseFloat(parts[44]),
        totalCapYi: parseFloat(parts[45]),
        amountWan: parseFloat(parts[37]),
        floatShares: parseFloat(parts[72]),
        totalShares: parseFloat(parts[73]),
        waiPan: parseFloat(parts[7]), // 外盘：累计"以卖一价及以上成交"的量，主动买盘
        neiPan: parseFloat(parts[8]), // 内盘：累计"以买一价及以下成交"的量，主动卖盘
        bids, asks, totalBidVol, totalAskVol, // 这两个是当前5档挂单量，只用来算委比/委差，不是外盘/内盘
      });
    })
    .catch(() => { clearTimeout(timer); finish(null); });
}

// ---------------------------------------------------------------------------
// 今日分时：跟"日K线+实时快照"是两回事——日K线那根"今天"的蜡烛只是一个整体的
// OHLC快照，分时图给的是今天从开盘到现在，价格一分钟一分钟怎么走的完整曲线。
// 用的是腾讯的分时行情接口（web.ifzq.gtimg.cn，跟K线实时快照的 qt.gtimg.cn 是
// 同一家，同样支持标准CORS），返回的是干净的JSON（不是tilde分隔的老格式），
// 每一条是"HHMM 价格 累计成交量(股) 累计成交额"。上午9:30-11:30、下午13:00-15:00
// 中间有个午休，按分钟序号压缩掉这段空档（跟真实的分时图软件一个做法），不是
// 按时钟时间等比例画，不然中间会空出一大段。
// ---------------------------------------------------------------------------
const INTRADAY_TOTAL_MIN = 242; // 上午121分钟(9:30~11:30) + 下午121分钟(13:00~15:00)
function intradayMinuteIndex(hhmm) {
  const t = parseInt(hhmm.slice(0, 2), 10) * 60 + parseInt(hhmm.slice(2, 4), 10);
  const morningStart = 9 * 60 + 30, morningEnd = 11 * 60 + 30, afternoonStart = 13 * 60;
  return t <= morningEnd ? t - morningStart : (morningEnd - morningStart) + (t - afternoonStart);
}
let _intradaySeq = 0;
function fetchIntradayData(code, cb) {
  const seq = ++_intradaySeq;
  const finish = (result) => { if (seq === _intradaySeq) cb(result); };
  const prefixed = marketPrefix(code) + code;
  const controller = ("AbortController" in window) ? new AbortController() : null;
  const timer = setTimeout(() => { if (controller) controller.abort(); }, 6000);
  fetch("https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=" + prefixed + "&_=" + Date.now(),
        { cache: "no-store", signal: controller ? controller.signal : undefined })
    .then(r => r.json())
    .then(json => {
      clearTimeout(timer);
      const entry = json && json.data && json.data[prefixed];
      const rawPoints = entry && entry.data && entry.data.data;
      const qt = entry && entry.qt && entry.qt[prefixed];
      if (!rawPoints || !rawPoints.length || !qt) { finish(null); return; }
      const prevClose = parseFloat(qt[4]);
      if (!(prevClose > 0)) { finish(null); return; }
      let prevCumVol = 0;
      const points = rawPoints.map((line) => {
        const bits = line.split(" ");
        const cumVol = parseFloat(bits[2]);
        const vol = Math.max(cumVol - prevCumVol, 0);
        prevCumVol = cumVol;
        return { time: bits[0], price: parseFloat(bits[1]), vol };
      });
      finish({ points, prevClose });
    })
    .catch(() => { clearTimeout(timer); finish(null); });
}

function drawIntradaySVG(points, prevClose) {
  const W = 780, H = 340, padL = 54, padR = 12, padT = 12, padB = 26, volH = 60;
  const plotH = H - padT - padB - volH - 8;
  const plotW = W - padL - padR;
  const n = INTRADAY_TOTAL_MIN;
  const maxDelta = Math.max(...points.map(p => Math.abs(p.price - prevClose)), prevClose * 0.005);
  const minP = prevClose - maxDelta, maxP = prevClose + maxDelta;
  const priceRange = (maxP - minP) || 1;
  const maxVol = Math.max(...points.map(p => p.vol), 1);
  const x = (i) => padL + (i / (n - 1)) * plotW;
  const y = (p) => padT + plotH - ((p - minP) / priceRange) * plotH;
  const volY0 = padT + plotH + 8;
  const isUp = points.length > 0 && points[points.length - 1].price >= prevClose;
  const color = isUp ? "var(--red)" : "var(--green)"; // A股：现价高于昨收=红，低于=绿

  let svg = `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block;background:var(--panel)">`;
  [maxP, prevClose, minP].forEach((p) => {
    const yy = y(p);
    svg += `<line x1="${padL}" y1="${yy}" x2="${W - padR}" y2="${yy}" stroke="var(--border)" stroke-width="1"` +
      (p === prevClose ? ' stroke-dasharray="3,3"' : "") + `/>`;
    svg += `<text x="4" y="${yy + 4}" font-size="10" fill="var(--dim)">${p.toFixed(2)}</text>`;
  });
  if (points.length) {
    const linePts = points.map((p) => x(intradayMinuteIndex(p.time)) + "," + y(p.price)).join(" ");
    svg += `<polyline points="${linePts}" fill="none" stroke="${color}" stroke-width="1.3"/>`;
    points.forEach((p) => {
      const cx = x(intradayMinuteIndex(p.time));
      const vh = p.vol > 0 ? Math.max((p.vol / maxVol) * volH, 1) : 0;
      svg += `<rect x="${cx - 0.8}" y="${volY0 + volH - vh}" width="1.6" height="${vh}" fill="${color}" opacity="0.55"/>`;
    });
  }
  [[0, "09:30"], [120, "11:30"], [121, "13:00"], [241, "15:00"]].forEach(([i, label]) => {
    svg += `<text x="${x(i)}" y="${H - 6}" font-size="10" fill="var(--dim)" text-anchor="middle">${label}</text>`;
  });
  svg += `</svg>`;
  return svg;
}

// 弹窗里滚轮切换上/下一只：_chartRows/_chartIndex 记录"当前是从哪张表格、第几行
// 点进来的"，滚轮就在这张表格的行范围内前后移动，到头绕回另一端。_chartActiveTab
// 记住切换股票时应该停留在哪个tab（比如正在看"今日分时"就切下一只也接着看分时，
// 不会每切一只都跳回日K线）。
let _chartRows = [];
let _chartIndex = -1;
let _chartActiveTab = "daily";
let _wheelNavLock = false;

function navigateChart(delta) {
  if (_chartRows.length < 2 || _wheelNavLock) return;
  _wheelNavLock = true;
  setTimeout(() => { _wheelNavLock = false; }, 220);
  let idx = _chartIndex + delta;
  if (idx < 0) idx = _chartRows.length - 1;
  if (idx >= _chartRows.length) idx = 0;
  _chartIndex = idx;
  const row = _chartRows[idx];
  openChart(row.code, row.name, row.trigger_date);
}

function openChart(code, name, triggerDate) {
  const cached = (DATA.price_history || {})[code] || [];
  const posNote = _chartRows.length > 1
    ? "　（第" + (_chartIndex + 1) + "/" + _chartRows.length + "只，滚轮切换上/下一只）"
    : "";
  document.getElementById("chart-title").textContent = code + "  " + (name || "") + posNote;
  renderFundamentalsPanel(document.getElementById("fundamentals-panel"), (DATA.fundamentals || {})[code]);
  const subEl = document.getElementById("chart-sub");
  const container = document.getElementById("chart-container");
  const tabDaily = document.getElementById("tab-daily");
  const tabIntraday = document.getElementById("tab-intraday");
  const legendDaily = document.getElementById("chart-legend-daily");
  const legendIntraday = document.getElementById("chart-legend-intraday");
  let intradayData = null; // 缓存这次弹窗里已经取到的分时数据，来回切tab不用重新发请求

  function renderDaily(rows, liveNote) {
    const base = triggerDate
      ? "信号触发日：" + triggerDate + "（图中蓝色虚线标注）　·　最近" + rows.length + "个交易日"
      : "最近" + rows.length + "个交易日";
    subEl.textContent = base + (liveNote ? "　·　" + liveNote : "");
    container.innerHTML = rows.length
      ? drawCandlestickSVG(rows, triggerDate)
      : '<div class="empty">暂无K线数据（可能是本地历史行情缓存还没有这只股票）。</div>';
  }

  function showDailyTab() {
    _chartActiveTab = "daily";
    tabDaily.classList.add("active");
    tabIntraday.classList.remove("active");
    legendDaily.style.display = "";
    legendIntraday.style.display = "none";
    renderDaily(cached, "🔄 正在获取实时行情…");
    renderQuotePanel(document.getElementById("quote-panel"), null, code);
    // 只在打开的这一刻取一次快照，不设定时器、不自动刷新。
    fetchLiveQuote(code, (live) => {
      if (!live || !(live.close > 0)) {
        renderDaily(cached, cached.length ? "实时行情获取失败，以下是最近一次扫描缓存的数据" : "");
        return;
      }
      const liveBar = [live.date, live.open, live.high, live.low, live.close, live.volume];
      let merged = cached;
      if (cached.length && cached[cached.length - 1][0] === live.date) {
        merged = cached.slice(0, -1).concat([liveBar]); // 今天已经有一根缓存的，用实时数据覆盖
      } else if (!cached.length || live.date >= cached[cached.length - 1][0]) {
        merged = cached.concat([liveBar]); // 缓存里还没有今天，追加一根
      }
      renderDaily(merged, "🔴 实时快照 " + live.date + " " + (live.time || ""));
      renderQuotePanel(document.getElementById("quote-panel"), live, code);
    });
  }

  function showIntradayTab() {
    _chartActiveTab = "intraday";
    tabDaily.classList.remove("active");
    tabIntraday.classList.add("active");
    legendDaily.style.display = "none";
    legendIntraday.style.display = "";
    renderQuotePanel(document.getElementById("quote-panel"), null, code); // 分时tab不显示盘口面板
    if (intradayData) {
      subEl.textContent = "今日分时　·　🔴 实时（" + intradayData.points.length + "个数据点）";
      container.innerHTML = drawIntradaySVG(intradayData.points, intradayData.prevClose);
      return;
    }
    subEl.textContent = "🔄 正在获取今日分时数据…";
    container.innerHTML = "";
    fetchIntradayData(code, (data) => {
      if (!data || !data.points.length) {
        subEl.textContent = "今日分时数据获取失败（可能还没开盘，或接口暂时不可用）。";
        container.innerHTML = '<div class="empty">暂无分时数据。</div>';
        return;
      }
      intradayData = data;
      subEl.textContent = "今日分时　·　🔴 实时（" + data.points.length + "个数据点）";
      container.innerHTML = drawIntradaySVG(data.points, data.prevClose);
    });
  }

  tabDaily.onclick = showDailyTab;
  tabIntraday.onclick = showIntradayTab;
  if (_chartActiveTab === "intraday") {
    showIntradayTab();
  } else {
    showDailyTab();
  }
  document.getElementById("chart-modal").classList.add("open");
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
function withChartClick(row, rows, index) {
  _chartRows = rows || [];
  _chartIndex = index != null ? index : -1;
  openChart(row.code, row.name, row.trigger_date);
}
document.getElementById("chart-modal").addEventListener("wheel", (e) => {
  if (!document.getElementById("chart-modal").classList.contains("open")) return;
  e.preventDefault();
  navigateChart(e.deltaY > 0 ? 1 : -1);
}, { passive: false });

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


def build_fundamentals(codes: set[str]) -> dict:
    """给K线弹窗的基本面面板打包数据：每股净资产/净利润/毛利率/净利率/ROE/负债率/
    净利润趋势，来自 book_value_cache（check_book_value.py 查财报缓存的，不是
    每次生成网页时现查——这些数据本来就是"按季度更新，不用每天查"的东西）。
    """
    cache = db.get_all_book_value_cache()
    fundamentals = {}
    for code in codes:
        row = cache.get(code)
        if not row:
            continue
        entry = {"book_value_per_share": row.get("book_value_per_share")}
        if row.get("extra_json"):
            try:
                entry.update(json.loads(row["extra_json"]))
            except (TypeError, ValueError):
                pass
        if any(v is not None for v in entry.values()):
            fundamentals[code] = entry
    return fundamentals


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
        "fundamentals": build_fundamentals(codes),
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
