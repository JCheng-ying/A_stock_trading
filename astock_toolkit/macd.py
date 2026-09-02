"""MACD 确认：给已经进入"回调买点区间"(buy_signal)的信号做二次确认标注。

不影响信号本身是否命中"底部首板"、是否进入观察池/买点区间——那些判断只看价格
（见 bottom_pattern_scanner.py）。MACD 只是在已经筛出来的买点候选里，多标注一个
"有没有均线金叉向上发散的支撑"，方便你自己再筛一道。

术语：
  DIF（快线） = EMA12 - EMA26
  DEA（慢线，也叫信号线） = DIF 的 9 日 EMA
  金叉：DIF 从下方穿过 DEA（即 DIF-DEA 由 <=0 变成 >0）
  向上发散：金叉之后，DIF-DEA 的差值持续走扩，说明多头动能在增强，不是叉完就
  死叉打回去。
"""

from __future__ import annotations

import pandas as pd

from . import config


def compute_macd(close: pd.Series) -> tuple[pd.Series, pd.Series]:
    """返回 (DIF, DEA)，输入按日期升序的收盘价序列。"""
    ema_fast = close.ewm(span=config.MACD_FAST, adjust=False).mean()
    ema_slow = close.ewm(span=config.MACD_SLOW, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=config.MACD_SIGNAL, adjust=False).mean()
    return dif, dea


def check_macd_confirmation(hist: pd.DataFrame) -> dict:
    """对给定历史行情（按日期升序，含 date/close 列）判断 MACD 是否确认。

    返回 {"confirmed": bool, "cross_date": str|None, "detail": str}。
    confirmed=True 表示：近 MACD_CROSS_LOOKBACK_DAYS 个交易日内出现过金叉，且金叉
    之后 DIF-DEA 持续走扩（用最新值 vs 金叉当天的值比较，不要求每天都严格递增）。
    """
    df = hist.dropna(subset=["close"]).reset_index(drop=True)
    close = df["close"]
    dates = df["date"]

    min_len = config.MACD_SLOW + config.MACD_SIGNAL + config.MACD_CROSS_LOOKBACK_DAYS
    if len(close) < min_len:
        return {"confirmed": False, "cross_date": None, "detail": "历史数据不足，无法计算MACD"}

    dif, dea = compute_macd(close)
    diff = (dif - dea).reset_index(drop=True)

    lookback = config.MACD_CROSS_LOOKBACK_DAYS
    start_idx = max(1, len(diff) - lookback)
    cross_idx = None
    for i in range(start_idx, len(diff)):
        if diff.iloc[i - 1] <= 0 and diff.iloc[i] > 0:
            cross_idx = i  # 只保留窗口内最近一次金叉
    if cross_idx is None:
        return {"confirmed": False, "cross_date": None,
                "detail": f"近{lookback}个交易日内未形成金叉"}

    cross_date = str(dates.iloc[cross_idx])
    since_cross = diff.iloc[cross_idx:]
    is_diverging_up = float(since_cross.iloc[-1]) > float(since_cross.iloc[0])
    detail = f"{cross_date}形成金叉，此后DIF-DEA{'持续走扩' if is_diverging_up else '未能持续走扩'}"
    return {"confirmed": is_diverging_up, "cross_date": cross_date, "detail": detail}
