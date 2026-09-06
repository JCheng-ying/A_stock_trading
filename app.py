"""A股"底部首板"每日看板（Streamlit 本地网页，纯只读展示）。

启动方式：
    streamlit run app.py

本页面不发起任何行情请求、不含任何"刷新/扫描/更新"按钮——所有数据更新都由
daily_scan.py 每天运行一次完成，本页面只读取上一次扫描写入数据库的结果。

选股逻辑（只做这一件事）："底部首板"只看两条：
  1. 此前30个交易日内没有涨停过（首板，排除连板/接力）；
  2. 涨停当日收盘价没有超过此前30个交易日的平均收盘价（底部）。
  然后：
  3. 重点关注：第一个涨停板后约5个交易日、回调约5%的股票；
  4. 结合股票所属板块的当日热门度。
"""

from __future__ import annotations

import io
import json

import pandas as pd
import streamlit as st

from astock_toolkit import config, db

st.set_page_config(page_title="A股底部首板看板", layout="wide")
db.init_db()

STATUS_LABEL = {
    "watching": "👀 观察中",
    "buy_signal": "🎯 回调至买点区间",
    "expired": "⌛ 观察期结束（未回调到位）",
}


def _color_pct(val):
    """A股习惯：涨（+）用红色，跌（-）用绿色，跟欧美惯例相反。"""
    try:
        v = float(val)
    except (TypeError, ValueError):
        return ""
    if v > 0:
        return "color: #f85149"
    if v < 0:
        return "color: #3fb950"
    return ""


def style_pct(df: pd.DataFrame, pct_columns: list[str]):
    """给指定的涨跌幅列上色，其余列原样返回，供 st.dataframe() 直接展示。"""
    cols_present = [c for c in pct_columns if c in df.columns]
    if not cols_present:
        return df
    return df.style.map(_color_pct, subset=cols_present)

st.title("📈 A股底部首板看板")

last_scan_at = db.get_setting("last_scan_at")
universe_count = db.get_setting("last_scan_universe_count")

if not last_scan_at:
    st.warning(
        "还没有扫描记录。这个页面只负责展示，扫描本身由单独的脚本每天跑一次：\n\n"
        "```bash\npython daily_scan.py\n```\n\n"
        "第一次运行会拉取全市场历史行情，耗时可能较长，请在终端里运行、等它跑完后回来刷新本页面。"
    )
    st.stop()

scan_date = last_scan_at[:10]
total_signal_count = len(db.list_watchlist())
info_cols = st.columns(4)
info_cols[0].metric("最后扫描时间", last_scan_at)
info_cols[1].metric("扫描范围", universe_count or "?")
info_cols[2].metric("累计信号总数", f"{total_signal_count} 条")
info_cols[3].metric("数据库文件", config.DB_PATH)

st.caption("需要更新今天的数据？在终端运行 `python daily_scan.py`，跑完后刷新本页面即可（本页面没有内置刷新按钮）。")

st.divider()

# ---------------------------------------------------------------------------
# 板块热门度（daily_scan.py 扫描时存的快照，纯展示，不再发请求）
# ---------------------------------------------------------------------------
st.subheader("📊 板块热门度")
ranking_json = db.get_setting("last_board_ranking_json")
if ranking_json:
    try:
        ranking_df = pd.read_json(io.StringIO(ranking_json))
        show_cols = [c for c in ["board", "pct_chg", "group", "score", "leader_name"] if c in ranking_df.columns]
        st.dataframe(style_pct(ranking_df[show_cols], ["pct_chg"]), width="stretch", height=280)
    except (ValueError, json.JSONDecodeError):
        st.caption("板块热度快照解析失败。")
else:
    st.caption("暂无板块热度数据（可能是扫描时板块接口不可用）。")

st.divider()

# ---------------------------------------------------------------------------
# 今日新增：底部首板
# ---------------------------------------------------------------------------
st.subheader(f"🆕 本次扫描新增：底部首板（{scan_date}）")
new_signals = db.list_watchlist_added_on(scan_date)
if new_signals:
    df = pd.DataFrame(new_signals)
    df["状态"] = df["status"].map(STATUS_LABEL).fillna(df["status"])
    cols = ["code", "name", "board", "board_pct_chg", "trigger_date", "trigger_price", "状态", "note"]
    cols = [c for c in cols if c in df.columns]
    st.dataframe(style_pct(df[cols], ["board_pct_chg"]), width="stretch", height=min(400, 60 + 35 * len(df)))
else:
    st.info("本次扫描没有新增的底部首板信号。")

st.divider()

# ---------------------------------------------------------------------------
# 重点关注：涨停后回调至买点区间
# ---------------------------------------------------------------------------
st.subheader(
    f"🎯 重点关注：首板后回调至 {config.PULLBACK_MIN_PCT:.0f}%~{config.PULLBACK_MAX_PCT:.0f}% 买点区间"
)
st.caption(
    f"MACD确认：近{config.MACD_CROSS_LOOKBACK_DAYS}个交易日内出现过金叉，"
    "且金叉后DIF-DEA持续向上发散——只是额外标注，不影响是否进入这个列表。"
)
buy_signals = db.list_watchlist(status="buy_signal")
if buy_signals:
    df = pd.DataFrame(buy_signals)
    df["MACD确认"] = df["macd_confirmed"].map({1: "✅ 确认", 0: "❌ 未确认"}).fillna("－ 未计算")
    cols = ["code", "name", "board", "board_pct_chg", "trigger_date", "trigger_price", "post_high",
            "MACD确认", "macd_note", "note"]
    cols = [c for c in cols if c in df.columns]
    st.dataframe(style_pct(df[cols], ["board_pct_chg"]), width="stretch", height=min(400, 60 + 35 * len(df)))
else:
    st.info("暂无股票处于回调买点区间。")

st.divider()

# ---------------------------------------------------------------------------
# 完整信号记录：每一轮扫描命中的信号都永久保留在这里（同一只股票不同时间的多次首板，
# 是各自独立的信号，不会互相覆盖），仅做本地筛选展示，不发起任何请求。
# ---------------------------------------------------------------------------
st.subheader(f"📋 完整信号记录（累计 {total_signal_count} 条，每轮扫描命中的信号都会永久保留在这里）")
status_filter = st.selectbox("按状态筛选", ["全部", "watching", "buy_signal", "expired"], key="status_filter")
all_signals = db.list_watchlist(status=None if status_filter == "全部" else status_filter)
if all_signals:
    df = pd.DataFrame(all_signals)
    df["状态"] = df["status"].map(STATUS_LABEL).fillna(df["status"])
    df["MACD确认"] = df["macd_confirmed"].map({1: "✅", 0: "❌"}).fillna("－")
    cols = ["code", "name", "board", "board_pct_chg", "trigger_date", "trigger_price",
            "post_high", "状态", "MACD确认", "note", "added_at"]
    cols = [c for c in cols if c in df.columns]
    df = df[cols].sort_values("trigger_date", ascending=False)
    st.dataframe(style_pct(df, ["board_pct_chg"]), width="stretch", height=500)
else:
    st.caption("信号池为空。")

st.divider()

# ---------------------------------------------------------------------------
# 第二个股票池：地量后放量突破（跟上面的底部首板完全独立，由 scan_volume_surge.py
# 单独扫描/更新，不含在 daily_scan.py 里）
# ---------------------------------------------------------------------------
st.header("🔍 股票池二：地量后放量突破")
st.caption(
    f"条件：总市值 < {config.MARKET_CAP_MAX_YI}亿 + 此前{config.VOLUME_SURGE_LOOKBACK_DAYS}日平均换手率 < "
    f"{config.VOLUME_SURGE_AVG_TURNOVER_MAX_PCT:.0f}% + 当日换手率 >= 前{config.VOLUME_SURGE_LOOKBACK_DAYS}日"
    f"平均的{config.VOLUME_SURGE_RATIO:.0f}倍 + 当日收盘价 < 前{config.VOLUME_SURGE_LOOKBACK_DAYS}日均价的"
    f"{config.VOLUME_SURGE_PRICE_MAX_RATIO:.1f}倍。不要求涨停。由 `python scan_volume_surge.py` 单独扫描"
    "（比底部首板慢很多，不用每天跟着一起跑）。"
)
vs_last_scan_at = db.get_setting("last_volume_surge_scan_at")
vs_universe_count = db.get_setting("last_volume_surge_universe_count")
if not vs_last_scan_at:
    st.info("还没跑过这个股票池的扫描。终端里运行 `python scan_volume_surge.py`（建议先加 `--limit 300` 测试）。")
else:
    vs_cols = st.columns(2)
    vs_cols[0].metric("最后扫描时间", vs_last_scan_at)
    vs_cols[1].metric("扫描范围", vs_universe_count or "?")
    vs_signals = db.list_volume_surge()
    if vs_signals:
        vs_df = pd.DataFrame(vs_signals)
        cols = ["code", "name", "trigger_date", "trigger_price", "avg_turnover_30d",
                "today_turnover", "avg_price_30d", "note"]
        cols = [c for c in cols if c in vs_df.columns]
        st.dataframe(vs_df[cols], width="stretch", height=min(450, 60 + 35 * len(vs_df)))
    else:
        st.caption("暂无命中的股票。")
