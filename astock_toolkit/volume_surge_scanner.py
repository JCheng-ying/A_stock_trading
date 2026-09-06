"""第二个股票池："地量后放量突破"——跟"底部首板"完全独立，不要求涨停。

条件（同时满足，"当日"以运行扫描那一刻能拿到的最新交易日为准）：
  1. 总市值 < MARKET_CAP_MAX_YI 亿人民币；
  2. 此前 VOLUME_SURGE_LOOKBACK_DAYS 个交易日平均换手率 < VOLUME_SURGE_AVG_TURNOVER_MAX_PCT；
  3. 当日换手率 >= 前30日平均换手率的 VOLUME_SURGE_RATIO 倍；
  4. 当日收盘价 < 前30日平均收盘价的 VOLUME_SURGE_PRICE_MAX_RATIO 倍。

市值过滤依赖东方财富实时快照的"总市值"字段（单位：元），新浪快照没有这个字段，
所以东方财富当天连不上时，这一步市值过滤会跳过（不是报错，是退化成不限制市值，
调用方会看到 market_cap_filter_applied=False，需要自己知悉这一点）。

跟 bottom_pattern_scanner.py 的观察池/回调买点那一套完全独立，写到单独的数据库表
volume_surge_pool 里，不会互相干扰。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from . import config, data_source as ds, db


def _get_history_with_cache(code: str, days: int = config.HISTORY_FETCH_DAYS) -> pd.DataFrame:
    """跟 bottom_pattern_scanner._get_history_with_cache 逻辑一样，独立一份避免循环引用。"""
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


def get_market_cap_filtered_universe(max_cap_yi: float = config.MARKET_CAP_MAX_YI):
    """返回 (universe_df, market_cap_filter_applied)。

    universe_df 列：code, name。当东方财富快照可用时已经按市值 < max_cap_yi 亿过滤过；
    当东方财富不可用（market_cap 全为空）时，market_cap_filter_applied=False，
    universe_df 是完整的选股范围（沪深主板+创业板+科创板，不含北交所/ST），调用方
    应该在界面上提示用户"这次没有做市值过滤"。
    """
    universe = ds.get_a_share_universe(exclude_st=config.UNIVERSE_EXCLUDE_ST)
    if universe.empty:
        return universe, False

    spot = ds.get_spot_snapshot()
    if spot.empty or "market_cap" not in spot.columns or spot["market_cap"].notna().sum() == 0:
        return universe, False

    max_cap_yuan = max_cap_yi * 1e8
    small_cap_codes = set(spot.loc[spot["market_cap"].notna() & (spot["market_cap"] < max_cap_yuan), "code"])
    filtered = universe[universe["code"].isin(small_cap_codes)].reset_index(drop=True)
    return filtered, True


def detect_volume_surge_signal(hist: pd.DataFrame, code: str) -> dict | None:
    """对给定历史行情（按日期升序，最新一行为待检测日）判断是否命中"地量后放量"信号。"""
    df = hist.dropna(subset=["close", "turnover_rate"]).reset_index(drop=True)
    window = config.VOLUME_SURGE_LOOKBACK_DAYS
    if len(df) < window + 1:
        return None

    today = df.iloc[-1]
    pre = df.iloc[:-1].tail(window)
    if len(pre) < window:
        return None

    avg_turnover = float(pre["turnover_rate"].mean())
    avg_price = float(pre["close"].mean())
    today_turnover = today["turnover_rate"]
    today_close = float(today["close"])

    if pd.isna(avg_turnover) or avg_turnover <= 0 or pd.isna(avg_price) or avg_price <= 0:
        return None
    if pd.isna(today_turnover):
        return None

    # 1) 前30日地量：平均换手率够低
    if avg_turnover >= config.VOLUME_SURGE_AVG_TURNOVER_MAX_PCT:
        return None
    # 2) 当日放量：换手率至少是前30日平均的 N 倍
    if float(today_turnover) < avg_turnover * config.VOLUME_SURGE_RATIO:
        return None
    # 3) 当日价格还没大涨：收盘价不超过前30日均价的 M 倍
    if today_close >= avg_price * config.VOLUME_SURGE_PRICE_MAX_RATIO:
        return None

    return {
        "code": code,
        "trigger_date": str(today["date"]),
        "trigger_price": today_close,
        "avg_turnover_30d": round(avg_turnover, 3),
        "today_turnover": round(float(today_turnover), 3),
        "avg_price_30d": round(avg_price, 2),
    }


def scan_volume_surge(codes: list[dict], progress_cb=None) -> list[dict]:
    """批量扫描，codes: [{code, name}, ...]。命中的股票自动写入 volume_surge_pool。"""
    found = []
    total = len(codes)
    for i, item in enumerate(codes):
        code, name = item["code"], item["name"]
        try:
            hist = _get_history_with_cache(code)
            if not hist.empty:
                sig = detect_volume_surge_signal(hist, code)
                if sig:
                    sig["name"] = name
                    db.upsert_volume_surge(
                        code=code,
                        name=name,
                        trigger_date=sig["trigger_date"],
                        trigger_price=sig["trigger_price"],
                        avg_turnover_30d=sig["avg_turnover_30d"],
                        today_turnover=sig["today_turnover"],
                        avg_price_30d=sig["avg_price_30d"],
                        note=(
                            f"地量后放量：前{config.VOLUME_SURGE_LOOKBACK_DAYS}日平均换手率"
                            f"{sig['avg_turnover_30d']:.2f}%，当日换手率{sig['today_turnover']:.2f}%"
                            f"（约{sig['today_turnover']/sig['avg_turnover_30d']:.1f}倍），"
                            f"收盘价{sig['trigger_price']:.2f}未超过前{config.VOLUME_SURGE_LOOKBACK_DAYS}"
                            f"日均价{sig['avg_price_30d']:.2f}的{config.VOLUME_SURGE_PRICE_MAX_RATIO:.1f}倍"
                        ),
                    )
                    found.append(sig)
        except Exception as e:  # noqa: BLE001
            ds._record_error(e, f"scan_volume_surge({code})")
        finally:
            if progress_cb:
                progress_cb(i + 1, total)
    return found
