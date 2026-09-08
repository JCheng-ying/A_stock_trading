#!/usr/bin/env python3
"""查两个股票池里所有股票的"真实每股净资产"（财报数据，不是股价/市净率反推的近似值）。

跟每天的 daily_scan.py / scan_volume_surge.py 是分开的、不需要每天跑的脚本：
每股净资产来自财报，按季度披露，不会天天变，一只股票查到过就一直有效，直到下个
季度财报出来才需要更新。默认只查"还没查过的"股票，已经缓存过的直接跳过——这正是
"不是天天变，只需要查一次"这个诉求的实现方式。

用的是 `stock_financial_abstract` 接口，按单只股票查（不是批量快照），所以只适合
"扫描候选名单里这几百只"这种规模，不适合对全市场几千只都查一遍。

查完之后，会用真实值重新核对一遍两个股票池里现有的信号，把每股净资产实际
<= MIN_BOOK_VALUE_PER_SHARE 的揪出来（之前市值过滤用的是股价/市净率反推的近似值，
可能不够准），并从对应的股票池里删除——这些不该出现在"确保净资产健康"的选股结果里。

用法：
    python check_book_value.py              # 只查还没查过的股票（默认，最常用）
    python check_book_value.py --force       # 全部重新查一遍（比如新一季财报出来后）
    python check_book_value.py --workers 16  # 调整并发进程数（默认8）
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

from astock_toolkit import concurrency, config, data_source as ds, db

try:
    sys.stdout.reconfigure(line_buffering=True)
except (AttributeError, ValueError):
    pass


def _fetch_one_book_value(code: str) -> dict | None:
    """必须是模块顶层函数（不能是闭包）——多进程要靠 pickle 把它传给子进程。"""
    result = ds.get_book_value_per_share(code)
    if result is None:
        return None
    db.upsert_book_value(code, result["book_value_per_share"], result["report_period"])
    return {"code": code, **result}


def main():
    parser = argparse.ArgumentParser(description="查两个股票池里所有股票的真实每股净资产")
    parser.add_argument("--force", action="store_true", help="忽略已有缓存，全部重新查一遍")
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
        to_fetch = [c for c in codes if c not in cached]
    print(f"    已有缓存 {len(cached)} 只，本次需要查询 {len(to_fetch)} 只"
          f"（{'已加 --force，全部重查' if args.force else '跳过已缓存的，这是默认行为'}）。")

    if to_fetch:
        def _progress(i, total):
            if i % 20 == 0 or i == total:
                print(f"    查询进度 {i}/{total}（已用时 {time.time()-t0:.0f}s）")

        results = concurrency.run_concurrent(to_fetch, _fetch_one_book_value,
                                              max_workers=args.workers, progress_cb=_progress)
        print(f"    成功查到 {len(results)}/{len(to_fetch)} 只（查不到的可能是刚上市没有财报、"
              f"或接口暂时限流，下次再跑会自动补上）。")

    # 用真实值核对一遍两个股票池，揪出净资产不达标的（之前市值过滤用的是股价/市净率
    # 反推的近似值，这里用财报真实值再确认一遍，更准确）。
    all_cache = db.get_all_book_value_cache()
    threshold = config.MIN_BOOK_VALUE_PER_SHARE
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 用真实每股净资产核对两个股票池"
          f"（要求 > {threshold}元）...")

    removed_wl = []
    for r in watchlist:
        c = all_cache.get(r["code"])
        if c and c["book_value_per_share"] is not None and c["book_value_per_share"] <= threshold:
            with db.get_conn() as conn:
                conn.execute("DELETE FROM watchlist WHERE code=? AND trigger_date=?",
                             (r["code"], r["trigger_date"]))
            removed_wl.append((r["code"], r["name"], c["book_value_per_share"]))

    removed_vs = []
    for r in vs_pool:
        c = all_cache.get(r["code"])
        if c and c["book_value_per_share"] is not None and c["book_value_per_share"] <= threshold:
            with db.get_conn() as conn:
                conn.execute("DELETE FROM volume_surge_pool WHERE code=? AND trigger_date=?",
                             (r["code"], r["trigger_date"]))
            removed_vs.append((r["code"], r["name"], c["book_value_per_share"]))

    if removed_wl:
        print(f"    股票池一删除了 {len(removed_wl)} 条（每股净资产不达标）：")
        for code, name, bvps in removed_wl:
            print(f"      {code} {name}  每股净资产={bvps:.2f}元")
    else:
        print("    股票池一：没有净资产不达标的，不用删。")

    if removed_vs:
        print(f"    股票池二删除了 {len(removed_vs)} 条（每股净资产不达标）：")
        for code, name, bvps in removed_vs:
            print(f"      {code} {name}  每股净资产={bvps:.2f}元")
    else:
        print("    股票池二：没有净资产不达标的，不用删。")

    elapsed = time.time() - t0
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 完成，共用时 {elapsed/60:.1f} 分钟。"
          f"记得跑一次 python generate_site.py 重新生成网页。")


if __name__ == "__main__":
    main()
