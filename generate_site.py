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
  .pct-pos { color: var(--green); }
  .pct-neg { color: var(--red); }
  footer { color: var(--dim); font-size: 12px; margin-top: 48px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>📈 A股底部首板看板</h1>
  <div class="meta" id="meta"></div>

  <div class="stat-row" id="stats"></div>

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

  <footer>纯决策辅助，不构成投资建议，买卖操作请自行判断。数据来源 AKShare，每日更新。</footer>
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

function renderTable(container, rows, columns) {
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
    const tr = el("tr");
    columns.forEach(c => {
      const val = c.render ? c.render(row) : (row[c.key] == null ? "" : row[c.key]);
      if (val instanceof Node) {
        tr.appendChild(el("td", c.cls ? { class: c.cls } : null, [val]));
      } else {
        tr.appendChild(text("td", val, c.cls ? { class: c.cls } : null));
      }
    });
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
  { label: "首板日", key: "trigger_date" },
  { label: "首板价", render: r => r.trigger_price != null ? Number(r.trigger_price).toFixed(2) : "" },
  { label: "状态", render: r => statusBadge(r.status) },
  { label: "MACD确认", render: macdCell },
  { label: "备注", key: "note", cls: "note" },
];
renderTable(document.getElementById("new-table"), newSignals, signalColumns);

const buySignals = DATA.signals.filter(s => s.status === "buy_signal");
renderTable(document.getElementById("buy-table"), buySignals, [
  { key: "code", label: "代码" },
  { key: "name", label: "名称" },
  { key: "board", label: "板块" },
  { label: "首板日", key: "trigger_date" },
  { label: "首板价", render: r => r.trigger_price != null ? Number(r.trigger_price).toFixed(2) : "" },
  { label: "涨停后高点", render: r => r.post_high != null ? Number(r.post_high).toFixed(2) : "" },
  { label: "MACD确认", render: macdCell },
  { label: "MACD说明", key: "macd_note", cls: "note" },
  { label: "备注", key: "note", cls: "note" },
]);

const filterSel = document.getElementById("status-filter");
[["all", "全部"], ["watching", "观察中"], ["buy_signal", "买点区间"], ["expired", "观察期结束"]]
  .forEach(([val, label]) => filterSel.appendChild(el("option", { value: val }, [document.createTextNode(label)])));
function renderAll() {
  const v = filterSel.value;
  const rows = (v === "all" ? DATA.signals : DATA.signals.filter(s => s.status === v))
    .slice().sort((a, b) => (b.trigger_date || "").localeCompare(a.trigger_date || ""));
  renderTable(document.getElementById("all-table"), rows, signalColumns);
}
filterSel.addEventListener("change", renderAll);
renderAll();
</script>
</body>
</html>
"""


def build_data() -> dict:
    last_scan_at = db.get_setting("last_scan_at") or ""
    universe_count = db.get_setting("last_scan_universe_count") or ""
    ranking_json = db.get_setting("last_board_ranking_json")
    board_ranking = json.loads(ranking_json) if ranking_json else []
    signals = db.list_watchlist()
    return {
        "last_scan_at": last_scan_at,
        "universe_count": universe_count,
        "board_ranking": board_ranking,
        "signals": signals,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "macd_lookback_days": config.MACD_CROSS_LOOKBACK_DAYS,
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
