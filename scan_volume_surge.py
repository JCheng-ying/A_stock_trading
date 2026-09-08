#!/usr/bin/env python3
"""第二个股票池："地量后放量突破"扫描入口，跟 daily_scan.py（底部首板）完全独立。

条件：总市值 < 200亿 + 每股净资产 > 1元 + 前30日平均换手率 < 2% + 当日换手率 >=
前30日均值2倍 + 当日收盘价 < 前30日均价的1.2倍。不要求涨停，"当日"以运行这个脚本
那一刻能拿到的最新交易日为准。

这个股票池目前是全市场扫描（先按市值/净资产过滤掉大盘股和财务差的公司，但剩下的
仍然有大几千只，每只都要拉一遍历史行情算30日平均），比 daily_scan.py 的"涨停股池
快速方案"慢得多，建议单独找时间跑（比如周末跑一次），不用每天跟着 daily_scan.py
一起跑。

市值/净资产过滤：东方财富快照能连上时会实时过滤 + 顺带缓存一份到本地；连不上时
自动退回上一次成功缓存的快照（哪怕缓存有几天旧了，也比完全不过滤扫全市场强）。

用法：
    python scan_volume_surge.py                # 全量扫描（市值<200亿的股票，可能要跑很久）
    python scan_volume_surge.py --limit 300     # 先测试
    python scan_volume_surge.py --workers 16    # 调整并发进程数（默认8，网络容易超时可以调低）
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

from astock_toolkit import config, data_source as ds, db, volume_surge_scanner as vss

try:
    sys.stdout.reconfigure(line_buffering=True)
except (AttributeError, ValueError):
    pass


def main():
    parser = argparse.ArgumentParser(description="地量后放量突破 扫描")
    parser.add_argument("--limit", type=int, default=None, help="只扫描前N只（测试用）")
    parser.add_argument("--workers", type=int, default=8, help="并发扫描的进程数（默认8）")
    args = parser.parse_args()

    db.init_db()
    t0 = time.time()

    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 获取市值 < {config.MARKET_CAP_MAX_YI}亿 且"
          f"每股净资产 > {config.MIN_BOOK_VALUE_PER_SHARE}元的选股范围...")
    universe, cap_filter_applied, cap_source = vss.get_market_cap_filtered_universe()
    if universe.empty:
        print("❌ 未能获取股票代码列表，请检查网络后重试。最近内部错误：", ds.LAST_ERROR)
        sys.exit(1)
    if not cap_filter_applied:
        print("⚠️  东方财富实时快照当前不可用，也没有历史缓存可用——无法按市值/净资产过滤，"
              "本次扫描范围是沪深主板+创业板+科创板全市场（不含北交所/ST），会比预期慢很多。")
    elif cap_source == "live":
        print("    市值/净资产数据来自本次实时快照（已顺带缓存，供以后东方财富连不上时使用）。")
    else:
        print(f"    ⚠️ 东方财富本次连不上，市值/净资产数据用的是上次缓存（缓存时间：{cap_source}）。")
    codes = universe.to_dict("records")
    if args.limit:
        codes = codes[: args.limit]
    print(f"    范围内共 {len(universe)} 只，本次扫描 {len(codes)} 只（并发 {args.workers} 进程）。")

    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 扫描地量后放量信号（逐只拉取历史行情，请耐心等待）...")

    def _progress(i, total):
        if i % 50 == 0 or i == total:
            print(f"    进度 {i}/{total}（已用时 {time.time()-t0:.0f}s）")

    found = vss.scan_volume_surge(codes, progress_cb=_progress, max_workers=args.workers)
    print(f"    本次命中 {len(found)} 只。")

    db.set_setting("last_volume_surge_scan_at", db.now_str())
    db.set_setting("last_volume_surge_universe_count",
                    f"{len(codes)} 只（市值<{config.MARKET_CAP_MAX_YI}亿、"
                    f"每股净资产>{config.MIN_BOOK_VALUE_PER_SHARE}元"
                    + ("" if cap_filter_applied else "，本次未做市值/净资产过滤") + "）")

    elapsed = time.time() - t0
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 扫描完成，共用时 {elapsed/60:.1f} 分钟。")


if __name__ == "__main__":
    main()
