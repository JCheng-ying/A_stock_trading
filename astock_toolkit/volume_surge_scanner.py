"""第二个股票池："地量后放量突破"——跟"底部首板"完全独立，不要求涨停。

条件（同时满足，"当日"以运行扫描那一刻能拿到的最新交易日为准）：
  1. 总市值 < MARKET_CAP_MAX_YI 亿人民币；
  2. 此前 VOLUME_SURGE_LOOKBACK_DAYS 个交易日平均换手率 < VOLUME_SURGE_AVG_TURNOVER_MAX_PCT；
  3. 当日换手率 >= 前30日平均换手率的 VOLUME_SURGE_RATIO 倍；
  4. 当日收盘价 < 前30日平均收盘价的 VOLUME_SURGE_PRICE_MAX_RATIO 倍。

市值过滤依赖东方财富实时快照的"总市值"字段（单位：元），新浪快照没有这个字段。
东方财富能连上时，除了当次使用，还会把这份市值快照缓存进本地数据库（settings表）；
东方财富连不上时，自动退回用上一次成功缓存的市值快照做过滤（哪怕缓存已经有几天旧了，
市值排名在200亿这个量级附近，短期内变动不会大到影响筛选结果）。只有"从来没有成功
缓存过"这一种情况，才会真的完全跳过市值过滤、退化成扫全市场。

跟 bottom_pattern_scanner.py 的观察池/回调买点那一套完全独立，写到单独的数据库表
volume_surge_pool 里，不会互相干扰。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pandas as pd

from . import concurrency, config, data_source as ds, db


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


_MARKET_CAP_CACHE_KEY = "market_cap_snapshot_json"
_MARKET_CAP_CACHE_AT_KEY = "market_cap_snapshot_at"


def get_market_cap_filtered_universe(max_cap_yi: float = config.MARKET_CAP_MAX_YI):
    """返回 (universe_df, filter_applied, source)。

    universe_df 列：code, name，已经按市值 < max_cap_yi 亿过滤过（除非 filter_applied
    为 False）。source 说明市值数据的来源：
      - "live"：本次东方财富快照拉取成功，用的是最新数据（顺带已经写回缓存）；
      - 一个时间字符串（如 "2026-09-05 23:59:21"）：东方财富这次连不上，用的是上次
        成功缓存的市值快照，这个时间就是那次缓存的时间；
      - None：从来没有成功缓存过市值数据，filter_applied=False，universe_df 是完整
        选股范围（沪深主板+创业板+科创板，不含北交所/ST），调用方应提示用户这次没
        做市值过滤。
    """
    universe = ds.get_a_share_universe(exclude_st=config.UNIVERSE_EXCLUDE_ST)
    if universe.empty:
        return universe, False, None

    max_cap_yuan = max_cap_yi * 1e8
    spot = ds.get_spot_snapshot()
    if not spot.empty and "market_cap" in spot.columns and spot["market_cap"].notna().sum() > 0:
        cap_df = spot.dropna(subset=["market_cap"])
        cap_map = dict(zip(cap_df["code"], cap_df["market_cap"]))
        db.set_setting(_MARKET_CAP_CACHE_KEY, json.dumps(cap_map))
        db.set_setting(_MARKET_CAP_CACHE_AT_KEY, db.now_str())
        small_cap_codes = {c for c, mc in cap_map.items() if mc < max_cap_yuan}
        filtered = universe[universe["code"].isin(small_cap_codes)].reset_index(drop=True)
        return filtered, True, "live"

    cached_json = db.get_setting(_MARKET_CAP_CACHE_KEY)
    cached_at = db.get_setting(_MARKET_CAP_CACHE_AT_KEY)
    if cached_json:
        cap_map = json.loads(cached_json)
        small_cap_codes = {c for c, mc in cap_map.items() if mc < max_cap_yuan}
        filtered = universe[universe["code"].isin(small_cap_codes)].reset_index(drop=True)
        return filtered, True, cached_at

    return universe, False, None


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


def _scan_one_volume_surge(item: dict) -> dict | None:
    """必须是模块顶层函数（不能是闭包）——多进程要靠 pickle 把它传给子进程。"""
    code, name = item["code"], item["name"]
    try:
        hist = _get_history_with_cache(code)
        if hist.empty:
            return None
        sig = detect_volume_surge_signal(hist, code)
        if not sig:
            return None
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
        return sig
    except Exception as e:  # noqa: BLE001
        ds._record_error(e, f"scan_volume_surge({code})")
        return None


def scan_volume_surge(codes: list[dict], progress_cb=None, max_workers: int = 8) -> list[dict]:
    """批量扫描，codes: [{code, name}, ...]。命中的股票自动写入 volume_surge_pool。

    max_workers>1 时用多进程并发拉历史行情（瓶颈在网络I/O等待，不是CPU；用多进程而
    不是多线程是因为新浪历史行情降级路径内部用的V8引擎不是线程安全的，见
    concurrency.py 顶部注释）；max_workers=1 退化成顺序执行。
    """
    return concurrency.run_concurrent(codes, _scan_one_volume_surge, max_workers=max_workers, progress_cb=progress_cb)
