"""核心战法：底部首板 -> 观察池 -> 回调买点。

"底部首板"的定义（按最新说法，只有两条）：
  1. 此前30个交易日内没有涨停过（首板，排除连板/接力）。
  2. 涨停当日收盘价，没有超过此前30个交易日的平均收盘价（判断是否处于底部；
     不看地量、不看均线是否走平钝化）。
  3. 重点关注：第一个涨停板后的5个交易日左右，回调5%左右的股票。
  4. 结合股票所属板块的热门度（由 daily_scan.py 在扫描时一并抓取）。

范围：沪深主板 + 创业板 + 科创板，不含北交所（见 data_source.get_a_share_universe）。

本模块提供：
  1. detect_bottom_reversal_signal —— 对单只股票最新一根K线做信号识别
  2. scan_universe_for_new_setups  —— 批量扫描一批股票，命中的自动写入观察池
  3. refresh_pullback_signals      —— 检查观察池里的股票是否已到回调买点，或已过期

本模块不含任何交互式按钮/人工触发逻辑——统一由 daily_scan.py 每天调用一次，
app.py 只负责只读展示最近一次扫描的结果。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from . import config, data_source as ds, db


def _get_history_with_cache(code: str, days: int = config.HISTORY_FETCH_DAYS) -> pd.DataFrame:
    """带本地缓存的历史行情获取：已缓存过的股票只增量拉取最新部分。"""
    last_cached = db.get_last_cached_date(code)
    end_date = datetime.now().strftime("%Y%m%d")
    cached_rows = db.get_cached_history(code) if last_cached else []

    if last_cached and len(cached_rows) >= days:
        fetch_start = last_cached.replace("-", "")
    else:
        fetch_start = (datetime.now() - timedelta(days=int(days * 1.6))).strftime("%Y%m%d")

    new_df = ds.get_daily_history(code, fetch_start, end_date)
    if not new_df.empty:
        rows = new_df.dropna(subset=["date"]).to_dict("records")
        db.save_history_rows(code, rows)

    all_rows = db.get_cached_history(code)
    if not all_rows:
        return pd.DataFrame()
    hist = pd.DataFrame(all_rows).sort_values("date").reset_index(drop=True)
    return hist.tail(days + 10).reset_index(drop=True)


def detect_bottom_reversal_signal(hist: pd.DataFrame, code: str) -> dict | None:
    """对给定历史行情（按日期升序，最新一行为待检测日）判断是否命中"底部首板"信号。

    命中条件（同时满足）：
      1. 最新一日涨停（按板块阈值：主板9.5%/创业科创19.5%，留误差空间）
      2. 是"第一个涨停板"：此前 FIRST_LIMIT_UP_LOOKBACK_DAYS 个交易日内没有出现过涨停
         （避免选到连板/接力，只要底部区域启动的首板）
      3. 涨停当日收盘价，没有超过此前 MA_PRICE_WINDOW 个交易日的平均收盘价（用这一条
         判断"底部"，不看地量、不看均线是否走平——按最新的定义简化掉了）
    """
    df = hist.dropna(subset=["close"]).reset_index(drop=True)
    n = len(df)
    lookback_needed = max(config.FIRST_LIMIT_UP_LOOKBACK_DAYS, config.MA_PRICE_WINDOW)
    if n < lookback_needed + 1:
        return None

    today = df.iloc[-1]
    limit_up_pct = ds.limit_up_threshold_for(code)
    if pd.isna(today["pct_chg"]) or today["pct_chg"] < limit_up_pct:
        return None

    pre = df.iloc[:-1]
    if len(pre) < lookback_needed:
        return None

    # 1) 首板：此前一段时间内没有涨停过，排除连板/接力
    first_board_window = pre.tail(config.FIRST_LIMIT_UP_LOOKBACK_DAYS)
    prior_pct = first_board_window["pct_chg"].dropna()
    if (prior_pct >= limit_up_pct).any():
        return None

    # 2) 涨停当日收盘价不超过此前30个交易日的平均收盘价（用涨停之前的价格算均价，
    #    不把当天这根涨停自己算进去，否则均价会被当天的涨幅拉高，条件失去意义）
    price_window = pre.tail(config.MA_PRICE_WINDOW)["close"].dropna()
    if len(price_window) < config.MA_PRICE_WINDOW:
        return None
    avg_price = float(price_window.mean())
    trigger_close = float(today["close"])
    if trigger_close > avg_price:
        return None

    return {
        "code": code,
        "trigger_date": str(today["date"]),
        "trigger_price": trigger_close,
        "avg_price_30d": round(avg_price, 2),
    }


def scan_universe_for_new_setups(codes: list[dict], progress_cb=None) -> list[dict]:
    """批量扫描，codes: [{code, name}, ...]。命中的股票自动 upsert 进观察池。

    注意：全市场（约5000只）逐只拉历史行情较慢（受限于数据源限速），建议先用较小的
    候选池（如沪深300/中证500/自选池）做日常扫描，全市场扫描适合收盘后跑一次。
    """
    found = []
    total = len(codes)
    for i, item in enumerate(codes):
        code, name = item["code"], item["name"]
        try:
            hist = _get_history_with_cache(code)
            if not hist.empty:
                sig = detect_bottom_reversal_signal(hist, code)
                if sig:
                    sig["name"] = name
                    db.upsert_watchlist(
                        code=code,
                        name=name,
                        source="bottom_reversal",
                        trigger_date=sig["trigger_date"],
                        trigger_price=sig["trigger_price"],
                        post_high=sig["trigger_price"],
                        status="watching",
                        note=(
                            f"底部首板：此前{config.FIRST_LIMIT_UP_LOOKBACK_DAYS}个交易日无涨停，"
                            f"涨停价{sig['trigger_price']:.2f}未超过此前"
                            f"{config.MA_PRICE_WINDOW}日均价{sig['avg_price_30d']:.2f}"
                        ),
                    )
                    found.append(sig)
        except Exception as e:  # noqa: BLE001
            ds._record_error(e, f"scan_universe({code})")
        finally:
            if progress_cb:
                progress_cb(i + 1, total)
    return found


def scan_recent_limit_up_pool(trading_days: int = 7, progress_cb=None) -> list[dict]:
    """快速方案（默认用这个）：先用 data_source.get_recent_limit_up_candidates 拿到
    "近N个交易日涨停过的股票"这个很小的候选名单（东方财富涨停股池接口直接给，不用
    自己逐只拉历史去判断涨没涨停），再只对这一小撮候选股做完整的首板/地量/钝化/
    相对低位历史校验——比 scan_universe_for_new_setups 全市场扫描快得多。

    注意：涨停股池接口本身不含 ST 和科创板股票（东方财富的限制，非本工具限制）。
    如果某只股票在窗口内涨停了不止一次（连板），会对它在窗口内的每个涨停日分别校验，
    命中即为真正的"首板日"（更早的涨停会被 detect_bottom_reversal_signal 里的
    "首板"校验自然排除，不需要在这里手工去重）。
    """
    pool = ds.get_recent_limit_up_candidates(trading_days=trading_days)
    if pool.empty:
        return []

    found = []
    grouped = list(pool.groupby("code"))
    total = len(grouped)
    for i, (code, group) in enumerate(grouped):
        name = group["name"].iloc[0]
        try:
            hist = _get_history_with_cache(code)
            if not hist.empty:
                for _, row in group.sort_values("trigger_date").iterrows():
                    truncated = hist[hist["date"] <= row["trigger_date"]]
                    if truncated.empty:
                        continue
                    sig = detect_bottom_reversal_signal(truncated, code)
                    if sig:
                        sig["name"] = name
                        db.upsert_watchlist(
                            code=code,
                            name=name,
                            source="bottom_reversal",
                            trigger_date=sig["trigger_date"],
                            trigger_price=sig["trigger_price"],
                            post_high=sig["trigger_price"],
                            status="watching",
                            note=(
                                f"底部首板：此前{config.FIRST_LIMIT_UP_LOOKBACK_DAYS}个交易日无涨停，"
                                f"涨停价{sig['trigger_price']:.2f}未超过此前"
                                f"{config.MA_PRICE_WINDOW}日均价{sig['avg_price_30d']:.2f}"
                            ),
                        )
                        industry = row.get("industry")
                        if industry:
                            db.update_watchlist_board(code, sig["trigger_date"], industry, None)
                        found.append(sig)
                        break  # 该代码在窗口内命中一次首板即可，不用再测它后续的连板日
        except Exception as e:  # noqa: BLE001
            ds._record_error(e, f"scan_recent_limit_up_pool({code})")
        finally:
            if progress_cb:
                progress_cb(i + 1, total)
    return found


def refresh_pullback_signals() -> list[dict]:
    """检查观察池中 status='watching' 的股票：

    - 若已从涨停后高点回调 PULLBACK_MIN_PCT~PULLBACK_MAX_PCT，标记为 'buy_signal'（买点区间）
    - 若超过 PULLBACK_WINDOW_DAYS 个交易日仍未回调到位，标记为 'expired'（观察期结束）
    - 否则更新 post_high 后继续保持 'watching'
    """
    watching = db.list_watchlist(status="watching")
    updates = []
    for item in watching:
        code = item["code"]
        hist = _get_history_with_cache(code)
        if hist.empty:
            continue
        trigger_date = item["trigger_date"]
        post = hist[hist["date"] > trigger_date]
        if post.empty:
            continue

        post_high = max(float(item["post_high"] or item["trigger_price"]), float(post["close"].max()))
        latest = post.iloc[-1]
        days_since = len(post)
        pullback_pct = (post_high - float(latest["close"])) / post_high * 100 if post_high else 0.0

        if config.PULLBACK_MIN_PCT <= pullback_pct <= config.PULLBACK_MAX_PCT:
            note = (f"{latest['date']} 较涨停后高点{post_high:.2f}回调{pullback_pct:.1f}%，"
                    f"进入{config.PULLBACK_MIN_PCT:.0f}%-{config.PULLBACK_MAX_PCT:.0f}%买点区间")
            db.update_watchlist_status(code, trigger_date, "buy_signal", post_high=post_high, note=note)
            updates.append({**item, "status": "buy_signal", "pullback_pct": round(pullback_pct, 1),
                             "post_high": post_high, "note": note})
        elif days_since > config.PULLBACK_WINDOW_DAYS:
            note = (f"涨停后已{days_since}个交易日，未出现"
                    f"{config.PULLBACK_MIN_PCT:.0f}%-{config.PULLBACK_MAX_PCT:.0f}%回调，观察期结束")
            db.update_watchlist_status(code, trigger_date, "expired", post_high=post_high, note=note)
            updates.append({**item, "status": "expired", "pullback_pct": round(pullback_pct, 1),
                             "post_high": post_high, "note": note})
        else:
            db.update_watchlist_status(code, trigger_date, "watching", post_high=post_high)
    return updates
