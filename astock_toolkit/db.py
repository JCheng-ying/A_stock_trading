"""本地 SQLite 存储：底部首板信号池 / 历史行情缓存 / 扫描运行时设置。

全部本地文件，不上传任何数据。数据库文件路径见 config.DB_PATH。

本工具是"每天跑一次 daily_scan.py -> app.py 只读展示"的模式，数据库里不存
持仓、不存人工规则体检结果——那些都不是这个工具要做的事。

watchlist 表的主键是 (code, trigger_date) 而不是单独的 code：同一只股票完全可能
在不同时间分别符合一次"底部首板"（比如这次信号过期或已经买入很久之后，又跌回底部
再走出一次新的首板），每一次都是独立的信号，都要完整保留、不能互相覆盖——这也是
"每一轮扫描出来的信号都要完整记录"这个要求在数据层面的实现方式。
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from . import config

WATCHLIST_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS watchlist (
    code TEXT,
    name TEXT,
    source TEXT,            -- 目前固定为 'bottom_reversal'（底部首板信号）
    trigger_date TEXT,      -- 首板涨停日
    trigger_price REAL,     -- 首板涨停日收盘价
    post_high REAL,         -- 首板后至今的最高收盘价（用于计算回调幅度）
    status TEXT,            -- 'watching'(观察中) | 'buy_signal'(回调至买点区间) | 'expired'(过期)
    note TEXT,
    board TEXT,             -- 所属热门板块名称（拿不到板块成分股数据时为空）
    board_pct_chg REAL,     -- 该板块当日涨跌幅（打分参考）
    macd_confirmed INTEGER, -- 仅在 status='buy_signal' 时才会计算：1=近30日金叉且向上发散，0=没有，NULL=还没算过
    macd_note TEXT,         -- MACD 判断的文字说明（金叉日期等）
    added_at TEXT,          -- 信号首次被扫描到并写入的时间（用于区分"哪一轮扫描新增的"）
    updated_at TEXT,
    PRIMARY KEY (code, trigger_date)
);
"""

VOLUME_SURGE_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS volume_surge_pool (
    code TEXT,
    name TEXT,
    trigger_date TEXT,        -- 命中"地量后放量"条件的那个交易日
    trigger_price REAL,       -- 当日收盘价
    avg_turnover_30d REAL,    -- 此前30日平均换手率(%)
    today_turnover REAL,      -- 当日换手率(%)
    avg_price_30d REAL,       -- 此前30日平均收盘价
    note TEXT,
    added_at TEXT,
    PRIMARY KEY (code, trigger_date)
);
"""

SCHEMA = WATCHLIST_CREATE_SQL + VOLUME_SURGE_CREATE_SQL + """
CREATE TABLE IF NOT EXISTS price_cache (
    code TEXT,
    date TEXT,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    turnover_rate REAL,
    pct_chg REAL,
    PRIMARY KEY (code, date)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def _ensure_dir():
    d = os.path.dirname(config.DB_PATH)
    if d:
        os.makedirs(d, exist_ok=True)


@contextmanager
def get_conn():
    """每次调用开一个新连接（不跨线程共享 sqlite3.Connection 对象，这样天然线程安全）。
    scan_volume_surge.py / daily_scan.py 用线程池并发拉取历史行情时，多个线程会同时
    写 price_cache——timeout=30 让某个线程写入时如果暂时锁住了，其它线程等一等而不是
    立刻报 "database is locked"；WAL 模式进一步减少读写互相阻塞。
    """
    _ensure_dir()
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def _migrate(conn):
    """兼容旧版数据库文件。每一步都是幂等的，重复运行不会出错。"""
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(watchlist)")}
    for col, coltype in (("board", "TEXT"), ("board_pct_chg", "REAL"), ("updated_at", "TEXT"),
                         ("macd_confirmed", "INTEGER"), ("macd_note", "TEXT")):
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE watchlist ADD COLUMN {col} {coltype}")
    for old_table in ("holdings", "journal"):
        conn.execute(f"DROP TABLE IF EXISTS {old_table}")

    # 旧版本 watchlist 主键只有 code：同一只股票第二次出现信号时会直接覆盖第一次的记录，
    # 历史信号会丢失。这里检测到还是老的单列主键时，迁移成 (code, trigger_date) 复合主键，
    # 把已有数据原样搬过去（老数据本身没有重复 code，搬迁不会有主键冲突）。
    pk_cols = [row[1] for row in conn.execute("PRAGMA table_info(watchlist)") if row[5] > 0]
    if pk_cols == ["code"]:
        conn.execute("ALTER TABLE watchlist RENAME TO watchlist_old")
        conn.execute(WATCHLIST_CREATE_SQL)
        conn.execute("""
            INSERT INTO watchlist (code, name, source, trigger_date, trigger_price, post_high,
                                    status, note, board, board_pct_chg, macd_confirmed, macd_note,
                                    added_at, updated_at)
            SELECT code, name, source, trigger_date, trigger_price, post_high,
                   status, note, board, board_pct_chg, macd_confirmed, macd_note,
                   added_at, updated_at
            FROM watchlist_old
        """)
        conn.execute("DROP TABLE watchlist_old")


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# watchlist（底部首板信号池——每一条 (code, trigger_date) 都是独立信号，永久保留）
# ---------------------------------------------------------------------------

def upsert_watchlist(code, name, source, trigger_date, trigger_price, note="",
                      status="watching", post_high=None):
    """新增一条信号，或更新同一只股票、同一个 trigger_date 那条信号的内容（比如板块热度
    补标注）。不同 trigger_date 永远是不同的信号，不会互相覆盖。added_at 只在首次插入时
    写入，后续更新不变。
    """
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT code FROM watchlist WHERE code=? AND trigger_date=?", (code, trigger_date)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE watchlist SET name=?, source=?, trigger_price=?, "
                "post_high=COALESCE(?, post_high), status=?, note=?, updated_at=? "
                "WHERE code=? AND trigger_date=?",
                (name, source, trigger_price, post_high, status, note,
                 now_str(), code, trigger_date),
            )
        else:
            conn.execute(
                "INSERT INTO watchlist (code, name, source, trigger_date, trigger_price, "
                "post_high, status, note, added_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (code, name, source, trigger_date, trigger_price,
                 post_high if post_high is not None else trigger_price,
                 status, note, now_str(), now_str()),
            )


def update_watchlist_status(code, trigger_date, status, post_high=None, note=None):
    with get_conn() as conn:
        if post_high is not None and note is not None:
            conn.execute(
                "UPDATE watchlist SET status=?, post_high=?, note=?, updated_at=? "
                "WHERE code=? AND trigger_date=?",
                (status, post_high, note, now_str(), code, trigger_date),
            )
        elif post_high is not None:
            conn.execute(
                "UPDATE watchlist SET status=?, post_high=?, updated_at=? WHERE code=? AND trigger_date=?",
                (status, post_high, now_str(), code, trigger_date),
            )
        else:
            conn.execute(
                "UPDATE watchlist SET status=?, updated_at=? WHERE code=? AND trigger_date=?",
                (status, now_str(), code, trigger_date),
            )


def update_watchlist_board(code, trigger_date, board, board_pct_chg):
    with get_conn() as conn:
        conn.execute(
            "UPDATE watchlist SET board=?, board_pct_chg=? WHERE code=? AND trigger_date=?",
            (board, board_pct_chg, code, trigger_date),
        )


def update_watchlist_macd(code, trigger_date, confirmed, note):
    """confirmed: True/False（存成 1/0），note: check_macd_confirmation() 里的 detail 文字。"""
    with get_conn() as conn:
        conn.execute(
            "UPDATE watchlist SET macd_confirmed=?, macd_note=? WHERE code=? AND trigger_date=?",
            (1 if confirmed else 0, note, code, trigger_date),
        )


def list_watchlist(status=None):
    """返回全部信号（按 trigger_date 倒序），每条 (code, trigger_date) 都是独立信号，
    同一只股票如果历史上多次触发过首板，会出现多行——这就是完整的信号记录。
    """
    with get_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM watchlist WHERE status=? ORDER BY trigger_date DESC, code",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM watchlist ORDER BY trigger_date DESC, code").fetchall()
        return [dict(r) for r in rows]


def list_watchlist_added_on(date_str: str):
    """返回 added_at 日期等于 date_str（'YYYY-MM-DD'）的信号，即"这一天的扫描新增的信号"。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM watchlist WHERE substr(added_at, 1, 10) = ? ORDER BY trigger_date DESC, code",
            (date_str,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# volume_surge_pool（第二个股票池："地量后放量突破"，跟 watchlist 完全独立）
# ---------------------------------------------------------------------------

def upsert_volume_surge(code, name, trigger_date, trigger_price, avg_turnover_30d,
                         today_turnover, avg_price_30d, note=""):
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT code FROM volume_surge_pool WHERE code=? AND trigger_date=?", (code, trigger_date)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE volume_surge_pool SET name=?, trigger_price=?, avg_turnover_30d=?, "
                "today_turnover=?, avg_price_30d=?, note=? WHERE code=? AND trigger_date=?",
                (name, trigger_price, avg_turnover_30d, today_turnover, avg_price_30d, note,
                 code, trigger_date),
            )
        else:
            conn.execute(
                "INSERT INTO volume_surge_pool (code, name, trigger_date, trigger_price, "
                "avg_turnover_30d, today_turnover, avg_price_30d, note, added_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (code, name, trigger_date, trigger_price, avg_turnover_30d, today_turnover,
                 avg_price_30d, note, now_str()),
            )


def list_volume_surge():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM volume_surge_pool ORDER BY trigger_date DESC, code"
        ).fetchall()
        return [dict(r) for r in rows]


def list_volume_surge_added_on(date_str: str):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM volume_surge_pool WHERE substr(added_at, 1, 10) = ? "
            "ORDER BY trigger_date DESC, code",
            (date_str,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# price_cache
# ---------------------------------------------------------------------------

def get_cached_history(code):
    """返回某只股票已缓存的历史行情（按日期升序），DataFrame 由调用方自行构造。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM price_cache WHERE code=? ORDER BY date ASC", (code,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_last_cached_date(code):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(date) as d FROM price_cache WHERE code=?", (code,)
        ).fetchone()
        return row["d"] if row and row["d"] else None


def save_history_rows(code, rows):
    """rows: list[dict] with keys date,open,high,low,close,volume,turnover_rate,pct_chg"""
    if not rows:
        return
    with get_conn() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO price_cache "
            "(code, date, open, high, low, close, volume, turnover_rate, pct_chg) "
            "VALUES (:code, :date, :open, :high, :low, :close, :volume, :turnover_rate, :pct_chg)",
            [{**r, "code": code} for r in rows],
        )


# ---------------------------------------------------------------------------
# settings（扫描运行时状态：最后扫描时间、板块热度快照等）
# ---------------------------------------------------------------------------

def get_setting(key, default=None):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
