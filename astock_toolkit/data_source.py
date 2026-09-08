"""AKShare 数据源封装层。

设计原则：
- 优先尝试东方财富（em）接口：字段最全（换手率、板块成分股等）。
- 东方财富在部分网络环境（尤其境外/云端IP）会被限制访问而连接失败，此时自动
  降级到新浪 / 同花顺接口，保证核心功能（实时报价、历史K线、行业板块强度排名）
  仍可用；仅"按板块拉取成分股列表"这一项在降级模式下不可用，会返回 None，
  调用方需接受该信号不标注所属板块，而不是整条信号作废。
- 所有函数失败时不抛异常导致页面崩溃，而是返回空 DataFrame / None，并把最后一次
  错误信息记录在 LAST_ERROR，供调用方判断"是否真的失败"（区别于降级但仍拿到数据）。
"""

from __future__ import annotations

import time

import akshare as ak
import pandas as pd
import requests

# akshare 内部几乎都是直接用 requests 发请求，很多函数的 timeout 参数默认是 None——
# 也就是完全不设超时。正常情况下连不上会很快报 ConnectionError/RemoteDisconnected，
# 但偶尔会遇到那种"连上了但一直不回应"的连接，这种情况下请求会永久挂起，把整个扫描
# 卡死在某一只股票上（实测遇到过，daily_scan.py 卡住不动）。
# 在这里统一给 requests.Session.request 打个补丁：调用方没显式传 timeout（包括传了
# timeout=None 的情况，比如 akshare 很多函数的默认值）时，强制补上一个兜底超时。
_DEFAULT_HTTP_TIMEOUT = 12  # 秒

_orig_requests_session_request = requests.Session.request


def _patched_requests_session_request(self, method, url, **kwargs):
    if kwargs.get("timeout") is None:
        kwargs["timeout"] = _DEFAULT_HTTP_TIMEOUT
    return _orig_requests_session_request(self, method, url, **kwargs)


requests.Session.request = _patched_requests_session_request

LAST_ERROR: str | None = None

# 全市场快照 / 板块排名在东方财富不可用时会降级为新浪全市场分页拉取，耗时可达
# 30~60秒。同一次页面交互里经常会被调用好几次（例如"板块排名"和"生成候选"各触发
# 一次），因此做一个很短的进程内 TTL 缓存，避免同一分钟内重复拉取整张全市场快照。
_CACHE: dict[str, tuple[float, object]] = {}
_CACHE_TTL_SECONDS = 45.0


def _cached(key: str, fn):
    hit = _CACHE.get(key)
    now = time.time()
    if hit and (now - hit[0]) < _CACHE_TTL_SECONDS:
        return hit[1]
    value = fn()
    _CACHE[key] = (now, value)
    return value


def _record_error(e: Exception, where: str):
    global LAST_ERROR
    LAST_ERROR = f"[{where}] {type(e).__name__}: {e}"


def _retry(fn, tries=2, delay=1.0):
    last_exc = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last_exc = e
            if i < tries - 1:
                time.sleep(delay)
    if last_exc:
        raise last_exc


def to_market_prefixed_symbol(code: str) -> str:
    """6位代码 -> 带市场前缀的新浪/同花顺格式代码，如 600519 -> sh600519。"""
    code = str(code).strip().zfill(6)
    if code.startswith(("60", "68", "9")):
        return f"sh{code}"
    if code.startswith(("00", "30", "20")):
        return f"sz{code}"
    if code.startswith(("8", "4", "92")):
        return f"bj{code}"
    return f"sh{code}"


def is_st(name: str) -> bool:
    return "ST" in (name or "").upper()


def limit_up_threshold_for(code: str) -> float:
    """按代码判断涨停幅度基准：创业板/科创板/北交所 20%，主板 10%（含ST 5%另处理）。"""
    code = str(code)
    if code.startswith(("300", "301", "688", "689")):
        return 19.5
    if code.startswith(("8", "4", "92")):
        return 29.0
    return 9.5


# ---------------------------------------------------------------------------
# 1. 全市场股票代码
# ---------------------------------------------------------------------------

def get_all_codes() -> pd.DataFrame:
    """返回全市场 A 股代码/名称（含北交所/ST），列：code, name。"""
    try:
        df = _retry(lambda: ak.stock_info_a_code_name())
        df = df.rename(columns={"code": "code", "name": "name"})
        return df[["code", "name"]]
    except Exception as e:  # noqa: BLE001
        _record_error(e, "get_all_codes")
        return pd.DataFrame(columns=["code", "name"])


# 沪深主板 / 创业板 / 科创板代码前缀；不含北交所（8/4/92 开头）
_UNIVERSE_PREFIXES = (
    "600", "601", "603", "605",  # 沪市主板
    "000", "001", "002", "003",  # 深市主板（含原中小板）
    "300", "301",                 # 创业板
    "688", "689",                 # 科创板
)


def get_a_share_universe(exclude_st: bool = True) -> pd.DataFrame:
    """返回选股范围内的股票：沪深主板 + 创业板 + 科创板，不含北交所。

    列：code, name。exclude_st=True 时同时剔除名称含"ST"的股票（默认排除，见
    config.UNIVERSE_EXCLUDE_ST）。
    """
    df = get_all_codes()
    if df.empty:
        return df
    mask = df["code"].astype(str).str.startswith(_UNIVERSE_PREFIXES)
    df = df[mask].copy()
    if exclude_st:
        df = df[~df["name"].apply(is_st)]
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# 1b. 涨停股池——快速拿到"近N个交易日涨停过的股票"候选名单，不用全市场逐只扫描。
#     仅东方财富独有接口；官方文档明确注明"不含ST股票及科创板股票"。
# ---------------------------------------------------------------------------

def get_limit_up_pool(date: str) -> pd.DataFrame:
    """返回某交易日的涨停股池（东方财富），已过滤到选股范围内（沪深主板+创业板+科创板，
    不含北交所）。date 格式 YYYYMMDD。

    统一列：code, name, pct_chg, price, turnover_rate, board_count(连板数), industry(所属行业)。
    非交易日 / 当天数据尚未生成 / 接口不可用时返回空 DataFrame（不算错误，调用方应跳过）。
    注意：官方接口本身不含 ST 股票和科创板股票，但**会包含北交所股票**（比如"92"开头的
    新编码）——这里额外用 _UNIVERSE_PREFIXES 过滤掉，不能假设接口已经排除了北交所。
    """
    try:
        df = _retry(lambda: ak.stock_zt_pool_em(date=date), tries=1)
        if df is None or df.empty:
            return pd.DataFrame(columns=["code", "name", "pct_chg", "price", "turnover_rate",
                                          "board_count", "industry"])
        out = pd.DataFrame({
            "code": df["代码"],
            "name": df["名称"],
            "pct_chg": df["涨跌幅"],
            "price": df["最新价"],
            "turnover_rate": df["换手率"],
            "board_count": df["连板数"],
            "industry": df["所属行业"],
        })
        out = out[out["code"].astype(str).str.startswith(_UNIVERSE_PREFIXES)].reset_index(drop=True)
        return out
    except Exception as e:  # noqa: BLE001
        _record_error(e, f"get_limit_up_pool({date})")
        return pd.DataFrame(columns=["code", "name", "pct_chg", "price", "turnover_rate",
                                      "board_count", "industry"])


def get_recent_limit_up_candidates(trading_days: int = 7, max_calendar_days_back: int = 16) -> pd.DataFrame:
    """近 trading_days 个交易日涨停股池的并集（同一只股票在窗口内每涨停一次占一行）。

    列：code, name, trigger_date(YYYY-MM-DD), board_count, industry, pct_chg。
    按日历日往前找交易日（非交易日该接口返回空，自动跳过），最多回溯
    max_calendar_days_back 个日历日，凑够 trading_days 个有数据的交易日为止。
    """
    from datetime import datetime, timedelta

    rows = []
    seen_trading_days = 0
    d = datetime.now()
    for _ in range(max_calendar_days_back):
        date_str = d.strftime("%Y%m%d")
        pool = get_limit_up_pool(date_str)
        if not pool.empty:
            pool = pool.copy()
            pool["trigger_date"] = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
            rows.append(pool)
            seen_trading_days += 1
            if seen_trading_days >= trading_days:
                break
        d -= timedelta(days=1)

    if not rows:
        return pd.DataFrame(columns=["code", "name", "trigger_date", "board_count", "industry", "pct_chg"])
    return pd.concat(rows, ignore_index=True)


# ---------------------------------------------------------------------------
# 2. 实时快照（全市场）
# ---------------------------------------------------------------------------

def get_spot_snapshot() -> pd.DataFrame:
    """返回全市场实时快照（进程内短期缓存，见 _CACHE_TTL_SECONDS，避免同一分钟内重复拉取
    整张全市场快照）。统一列：code, name, price, pct_chg, open, prev_close, high, low,
    volume, amount, turnover_rate, market_cap(总市值，可能为空), pb_ratio(市净率，可能为空，
    用于反推每股净资产=price/pb_ratio)。优先东方财富（含换手率、总市值、市净率），
    失败降级新浪（无换手率、无总市值、无市净率，且分页拉取较慢）。
    """
    return _cached("spot_snapshot", _get_spot_snapshot_uncached)


def _get_spot_snapshot_uncached() -> pd.DataFrame:
    try:
        df = _retry(lambda: ak.stock_zh_a_spot_em())
        out = pd.DataFrame({
            "code": df["代码"],
            "name": df["名称"],
            "price": df["最新价"],
            "pct_chg": df["涨跌幅"],
            "open": df.get("今开"),
            "prev_close": df.get("昨收"),
            "high": df.get("最高"),
            "low": df.get("最低"),
            "volume": df.get("成交量"),
            "amount": df.get("成交额"),
            "turnover_rate": df.get("换手率"),
            "market_cap": df.get("总市值"),
            "pb_ratio": df.get("市净率"),
        })
        return out
    except Exception as e:  # noqa: BLE001
        _record_error(e, "get_spot_snapshot(em)")

    try:
        df = _retry(lambda: ak.stock_zh_a_spot())
        out = pd.DataFrame({
            "code": df["代码"].str.replace(r"^(sh|sz|bj)", "", regex=True),
            "name": df["名称"],
            "price": df["最新价"],
            "pct_chg": df["涨跌幅"],
            "open": df.get("今开"),
            "prev_close": df.get("昨收"),
            "high": df.get("最高"),
            "low": df.get("最低"),
            "volume": df.get("成交量"),
            "amount": df.get("成交额"),
            "turnover_rate": None,
            "market_cap": None,
            "pb_ratio": None,
        })
        return out
    except Exception as e:  # noqa: BLE001
        _record_error(e, "get_spot_snapshot(sina)")
        return pd.DataFrame(columns=["code", "name", "price", "pct_chg", "open", "prev_close",
                                      "high", "low", "volume", "amount", "turnover_rate",
                                      "market_cap", "pb_ratio"])


def get_book_value_per_share(code: str) -> dict | None:
    """查单只股票财报披露的真实"每股净资产"（不是股价/市净率反推的近似值）。

    用的是 `stock_financial_abstract` 这个按财报科目查询的接口，跟实时快照走的
    是不同的数据源/host，实测在东方财富主快照接口连不上的环境下这个接口仍然可用。
    是按单只股票查的（不是批量快照），所以只适合"扫描候选名单里这几百只"这种规模，
    不适合每天对全市场做。财报按季度披露，不会天天变，查到后应该缓存起来长期复用
    （见 db.get_book_value_cached / check_book_value.py），不需要每天重新查。

    返回 {"book_value_per_share": float, "report_period": "20260630"} 或 None
    （查不到 / 该科目缺失）。
    """
    try:
        df = _retry(lambda: ak.stock_financial_abstract(code))
        if df is None or df.empty:
            return None
        row = df[df["指标"] == "每股净资产"]
        if row.empty:
            return None
        # 第0、1列是"选项"/"指标"，第2列往后按报告期倒序排列，取最新一期。
        date_cols = [c for c in df.columns if c not in ("选项", "指标")]
        if not date_cols:
            return None
        latest_col = date_cols[0]
        val = row[latest_col].values[0]
        if pd.isna(val):
            return None
        return {"book_value_per_share": float(val), "report_period": str(latest_col)}
    except Exception as e:  # noqa: BLE001
        _record_error(e, f"get_book_value_per_share({code})")
        return None


# ---------------------------------------------------------------------------
# 3. 行业板块实时强度排名
# ---------------------------------------------------------------------------

def get_industry_board_ranking() -> pd.DataFrame:
    """返回行业板块实时涨跌幅排名（进程内短期缓存），统一列：board, pct_chg, leader_name,
    leader_pct_chg。优先东方财富，失败降级同花顺板块概况接口。
    """
    return _cached("industry_board_ranking", _get_industry_board_ranking_uncached)


def _get_industry_board_ranking_uncached() -> pd.DataFrame:
    try:
        df = _retry(lambda: ak.stock_board_industry_name_em())
        out = pd.DataFrame({
            "board": df["板块名称"],
            "pct_chg": df["涨跌幅"],
            "leader_name": df.get("领涨股票"),
            "leader_pct_chg": None,
        }).sort_values("pct_chg", ascending=False).reset_index(drop=True)
        return out
    except Exception as e:  # noqa: BLE001
        _record_error(e, "get_industry_board_ranking(em)")

    try:
        df = _retry(lambda: ak.stock_board_industry_summary_ths())
        out = pd.DataFrame({
            "board": df["板块"],
            "pct_chg": pd.to_numeric(df["涨跌幅"], errors="coerce"),
            "leader_name": df.get("领涨股"),
            "leader_pct_chg": pd.to_numeric(df.get("领涨股-涨跌幅"), errors="coerce"),
        }).sort_values("pct_chg", ascending=False).reset_index(drop=True)
        return out
    except Exception as e:  # noqa: BLE001
        _record_error(e, "get_industry_board_ranking(ths)")
        return pd.DataFrame(columns=["board", "pct_chg", "leader_name", "leader_pct_chg"])


# 板块成分股只有东方财富一个数据源，没有备用降级。如果连续失败好几次，大概率是
# 整个东方财富接口在当前网络下都不通（daily_scan.py 里会连续对20个板块调用这个函数），
# 与其让每一个都重试再失败、白白浪费几十秒，不如在同一次进程里先记下来，后面直接跳过。
_board_cons_consecutive_failures = 0
_BOARD_CONS_GIVE_UP_AFTER = 3


def get_board_constituents(board_name: str) -> pd.DataFrame | None:
    """返回某行业板块的成分股实时行情，统一列：code, name, pct_chg。
    仅东方财富接口支持，若不可用返回 None（调用方应接受"这条信号不标注板块"）。
    """
    global _board_cons_consecutive_failures
    if _board_cons_consecutive_failures >= _BOARD_CONS_GIVE_UP_AFTER:
        _record_error(
            RuntimeError(f"已连续失败{_board_cons_consecutive_failures}次，本次运行内不再尝试"),
            f"get_board_constituents({board_name})/skipped",
        )
        return None
    try:
        df = _retry(lambda: ak.stock_board_industry_cons_em(symbol=board_name), tries=1)
        out = pd.DataFrame({
            "code": df["代码"],
            "name": df["名称"],
            "pct_chg": df["涨跌幅"],
            "turnover_rate": df.get("换手率"),
        })
        _board_cons_consecutive_failures = 0
        return out
    except Exception as e:  # noqa: BLE001
        _board_cons_consecutive_failures += 1
        _record_error(e, f"get_board_constituents({board_name})")
        return None


# ---------------------------------------------------------------------------
# 4. 个股历史日线（含换手率）—— 用于低位地量涨停战法扫描
# ---------------------------------------------------------------------------

_hist_em_consecutive_failures = 0
_HIST_EM_GIVE_UP_AFTER = 5  # 连续失败这么多次后，本次进程内不再尝试东方财富历史接口


def get_daily_history(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """返回统一列：date, open, high, low, close, volume, turnover_rate, pct_chg（升序）。
    优先东方财富（前复权），失败降级新浪（不复权，同样含换手率）。

    daily_scan.py 经常在一次运行里对成百上千只股票依次调用这个函数——如果东方财富在
    当前网络下整体不通，没必要每一只都重试再等它失败，连续失败几次后就直接跳过东方财富、
    只走新浪，省下大量浪费在必败请求上的时间。
    """
    global _hist_em_consecutive_failures
    if _hist_em_consecutive_failures < _HIST_EM_GIVE_UP_AFTER:
        try:
            df = _retry(lambda: ak.stock_zh_a_hist(
                symbol=code, period="daily", start_date=start_date, end_date=end_date, adjust="qfq"
            ), tries=1)
            if df is None or df.empty:
                raise ValueError("empty result")
            _hist_em_consecutive_failures = 0
            out = pd.DataFrame({
                "date": df["日期"].astype(str),
                "open": df["开盘"],
                "high": df["最高"],
                "low": df["最低"],
                "close": df["收盘"],
                "volume": df["成交量"],
                "turnover_rate": df["换手率"],
                "pct_chg": df["涨跌幅"],
            })
            return out.sort_values("date").reset_index(drop=True)
        except Exception as e:  # noqa: BLE001
            _hist_em_consecutive_failures += 1
            _record_error(e, f"get_daily_history(em,{code})")
    else:
        _record_error(
            RuntimeError(f"东方财富历史接口已连续失败{_hist_em_consecutive_failures}次，本次运行内不再尝试"),
            f"get_daily_history(em,{code})/skipped",
        )

    try:
        sina_symbol = to_market_prefixed_symbol(code)
        df = _retry(lambda: ak.stock_zh_a_daily(symbol=sina_symbol, start_date=start_date, end_date=end_date))
        if df is None or df.empty:
            raise ValueError("empty result")
        df = df.sort_values("date").reset_index(drop=True)
        close = pd.to_numeric(df["close"], errors="coerce")
        pct_chg = close.pct_change() * 100
        out = pd.DataFrame({
            "date": df["date"].astype(str),
            "open": df["open"],
            "high": df["high"],
            "low": df["low"],
            "close": df["close"],
            "volume": df["volume"],
            "turnover_rate": pd.to_numeric(df["turnover"], errors="coerce") * 100,
            "pct_chg": pct_chg,
        })
        return out
    except Exception as e:  # noqa: BLE001
        _record_error(e, f"get_daily_history(sina,{code})")
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume",
                                      "turnover_rate", "pct_chg"])


# ---------------------------------------------------------------------------
# 5. 数据源自检（在"设置"页展示，帮助判断当前网络下哪些数据源可用）
# ---------------------------------------------------------------------------

def selftest() -> list[dict]:
    """依次探测各数据源的可用性，返回 [{name, ok, detail}, ...]。用于设置页排查网络问题。"""
    results = []

    spot = get_spot_snapshot()
    has_turnover = spot["turnover_rate"].notna().any() if not spot.empty else False
    results.append({
        "name": "全市场实时快照 (spot)",
        "ok": not spot.empty,
        "detail": f"{len(spot)} 只，{'含换手率(东方财富)' if has_turnover else '无换手率(降级为新浪)' if not spot.empty else '获取失败'}",
    })

    ranking = get_industry_board_ranking()
    results.append({
        "name": "行业板块强度排名",
        "ok": not ranking.empty,
        "detail": f"{len(ranking)} 个板块" if not ranking.empty else "获取失败",
    })

    cons = get_board_constituents("有色金属") if not ranking.empty else None
    results.append({
        "name": "板块成分股 (东方财富专属)",
        "ok": cons is not None and not cons.empty,
        "detail": (f"{len(cons)} 只成分股" if cons is not None and not cons.empty
                   else "不可用，daily_scan.py 会跳过板块热门度标注（信号本身仍照常产出）"),
    })

    hist = get_daily_history("600519", "20250101", pd.Timestamp.now().strftime("%Y%m%d"))
    results.append({
        "name": "个股历史日线（含换手率）",
        "ok": not hist.empty,
        "detail": f"{len(hist)} 条（600519 贵州茅台测试）" if not hist.empty else "获取失败",
    })

    return results
