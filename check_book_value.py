#!/usr/bin/env python3
"""查两个股票池里所有股票的财报真实数据："每股净资产"和"半年报营业总收入"（不是
股价/市净率反推的近似值、也不是年度收入除以2估算的）。

跟每天的 daily_scan.py / scan_volume_surge.py 是分开的、不需要每天跑的脚本：
这两个指标都来自财报，按季度/半年披露，不会天天变，一只股票查到过就一直有效，
直到下一期财报出来才需要更新。默认只查"还没查过的"股票，已经缓存过的直接跳过——
这正是"不是天天变，只需要查一次"这个诉求的实现方式。

用的是 `stock_financial_abstract` 接口，按单只股票查（不是批量快照），所以只适合
"扫描候选名单里这几百只"这种规模，不适合对全市场几千只都查一遍。

查完之后，会核对一遍两个股票池里现有的信号，删掉两类不达标的：
  1. 每股净资产 <= MIN_BOOK_VALUE_PER_SHARE（财务已经很差）；
  2. 总市值 / 半年营收 > MAX_MARKET_CAP_TO_H1_REVENUE（估值相对营收明显偏高）。
     注意这条用的总市值是当天实时值（不像净资产那样查一次就一直有效——分子市值
     天天在变，只有半年营收这个分母是查一次长期有效的），所以这条核对每次跑都会
     用最新市值重新算一遍，即使这只股票的财报数据早就缓存过了。

用法：
    python check_book_value.py              # 只查还没查过财报的股票（默认，最常用）
    python check_book_value.py --force       # 财报数据全部重新查一遍（比如新一期财报出来后）
    python check_book_value.py --workers 16  # 调整并发进程数（默认8）
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

from astock_toolkit import concurrency, config, data_source as ds, db, volume_surge_scanner as vss

try:
    sys.stdout.reconfigure(line_buffering=True)
except (AttributeError, ValueError):
    pass


def _fetch_one_fundamental(code: str) -> dict | None:
    """必须是模块顶层函数（不能是闭包）——多进程要靠 pickle 把它传给子进程。"""
    result = ds.get_fundamental_report_data(code)
    if result is None:
        return None
    db.upsert_book_value(code, result["book_value_per_share"], result["report_period"],
                          result["h1_revenue"], result["h1_revenue_period"])
    return {"code": code, **result}


def main():
    parser = argparse.ArgumentParser(description="查两个股票池里所有股票的财报真实数据")
    parser.add_argument("--force", action="store_true", help="忽略已有缓存，财报数据全部重新查一遍")
    parser.add_argument("--workers", type=int, default=8, help="并发查询的进程数（默认8）")
    args = parser.parse_args()

    db.init_db()
    t0 = time.time()

    watchlist = db.list_watchlist()
    vs_pool = db.list_volume_surge()
    codes = sorted({r["code"] for r in watchlist} | {r["code"] for r in vs_pool})
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 两个股票池合计 {len(codes)} 只不重复的股票。")

    cached = db.get_all_book_value_cache()
    if args.force:
        to_fetch = codes
    else:
        # 缓存里两个指标都有值才算真的查到过；只要有一个还是空的（比如上次接口被限流、
        # 请求没成功），默认也会重新查一遍——不用加 --force，下次自动补上。
        to_fetch = [
            c for c in codes
            if c not in cached
            or cached[c]["book_value_per_share"] is None
            or cached[c]["h1_revenue"] is None
        ]
    print(f"    已有缓存 {len(cached)} 只，本次需要查询 {len(to_fetch)} 只"
          f"（{'已加 --force，全部重查' if args.force else '跳过两个指标都已查到的，这是默认行为'}）。")

    if to_fetch:
        def _progress(i, total):
            if i % 20 == 0 or i == total:
                print(f"    查询进度 {i}/{total}（已用时 {time.time()-t0:.0f}s）")

        results = concurrency.run_concurrent(to_fetch, _fetch_one_fundamental,
                                              max_workers=args.workers, progress_cb=_progress)
        print(f"    成功查到 {len(results)}/{len(to_fetch)} 只（查不到的可能是刚上市没有财报、"
              f"或接口暂时限流，下次再跑会自动补上）。")

    all_cache = db.get_all_book_value_cache()

    # ------------------------------------------------------------------
    # 第1步：每股净资产核对（财报真实值，不是股价/市净率反推的）。
    # ------------------------------------------------------------------
    bvps_threshold = config.MIN_BOOK_VALUE_PER_SHARE
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 用真实每股净资产核对两个股票池"
          f"（要求 > {bvps_threshold}元）...")

    removed_wl = set()
    removed_vs = set()

    def _check_bvps(rows, table, removed_set):
        removed = []
        for r in rows:
            key = (r["code"], r["trigger_date"])
            if key in removed_set:
                continue
            c = all_cache.get(r["code"])
            if c and c["book_value_per_share"] is not None and c["book_value_per_share"] <= bvps_threshold:
                with db.get_conn() as conn:
                    conn.execute(f"DELETE FROM {table} WHERE code=? AND trigger_date=?", key)
                removed_set.add(key)
                removed.append((r["code"], r["name"], c["book_value_per_share"]))
        return removed

    r1 = _check_bvps(watchlist, "watchlist", removed_wl)
    r2 = _check_bvps(vs_pool, "volume_surge_pool", removed_vs)
    if r1:
        print(f"    股票池一删除了 {len(r1)} 条（每股净资产不达标）：")
        for code, name, bvps in r1:
            print(f"      {code} {name}  每股净资产={bvps:.2f}元")
    else:
        print("    股票池一：没有净资产不达标的，不用删。")
    if r2:
        print(f"    股票池二删除了 {len(r2)} 条（每股净资产不达标）：")
        for code, name, bvps in r2:
            print(f"      {code} {name}  每股净资产={bvps:.2f}元")
    else:
        print("    股票池二：没有净资产不达标的，不用删。")

    # ------------------------------------------------------------------
    # 第2步：总市值/半年营收核对。市值是当天实时值，每次都要重新拿一次
    # （不像净资产那样查一次就一直有效），半年营收用刚才缓存的真实值。
    # ------------------------------------------------------------------
    cap_threshold = config.MAX_MARKET_CAP_TO_H1_REVENUE
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 拿最新总市值，核对 总市值/半年营收"
          f"（要求 <= {cap_threshold}）...")
    cap_map, _bvps_map, fund_ok, fund_source = vss.get_fundamentals_maps()
    if not fund_ok:
        print("    ⚠️ 东方财富连不上也没有市值缓存，本次跳过这条核对。")
    else:
        src_desc = "本次实时快照" if fund_source == "live" else f"上次缓存（{fund_source}）"
        print(f"    市值数据来源：{src_desc}。")

        def _check_cap_ratio(rows, table, removed_set):
            removed = []
            for r in rows:
                key = (r["code"], r["trigger_date"])
                if key in removed_set:
                    continue
                c = all_cache.get(r["code"])
                h1 = c["h1_revenue"] if c else None
                mc = cap_map.get(r["code"])
                if h1 is None or mc is None:
                    continue
                ratio = (mc / h1) if h1 > 0 else float("inf")
                if ratio > cap_threshold:
                    with db.get_conn() as conn:
                        conn.execute(f"DELETE FROM {table} WHERE code=? AND trigger_date=?", key)
                    removed_set.add(key)
                    removed.append((r["code"], r["name"], ratio, mc, h1))
            return removed

        r3 = _check_cap_ratio(watchlist, "watchlist", removed_wl)
        r4 = _check_cap_ratio(vs_pool, "volume_surge_pool", removed_vs)
        if r3:
            print(f"    股票池一删除了 {len(r3)} 条（市值/半年营收超过{cap_threshold}）：")
            for code, name, ratio, mc, h1 in r3:
                print(f"      {code} {name}  比值={ratio:.1f}（总市值{mc/1e8:.1f}亿 / "
                      f"半年营收{h1/1e8:.2f}亿）")
        else:
            print(f"    股票池一：没有市值/半年营收超标的，不用删。")
        if r4:
            print(f"    股票池二删除了 {len(r4)} 条（市值/半年营收超过{cap_threshold}）：")
            for code, name, ratio, mc, h1 in r4:
                print(f"      {code} {name}  比值={ratio:.1f}（总市值{mc/1e8:.1f}亿 / "
                      f"半年营收{h1/1e8:.2f}亿）")
        else:
            print(f"    股票池二：没有市值/半年营收超标的，不用删。")

    elapsed = time.time() - t0
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 完成，共用时 {elapsed/60:.1f} 分钟。"
          f"记得跑一次 python generate_site.py 重新生成网页。")


if __name__ == "__main__":
    main()
