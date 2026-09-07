#!/usr/bin/env python3
"""每天运行一次的扫描入口：底部首板信号扫描 + 观察池回调状态刷新 + 板块热门度标注。

默认用"快速方案"，覆盖沪深主板 + 创业板 + 科创板（不含北交所、不含ST）：
    1. 先用东方财富"涨停股池"接口直接拿到近7个交易日涨停过的股票——但这个接口本身
       不含科创板（东方财富的限制，不是本工具的选择），所以这一步只覆盖沪深主板+创业板，
       是一个很小的名单，通常几百只以内。
    2. 同时对科创板（688/689，不到1000只）单独做一次小范围全量历史扫描，补上涨停股池
       覆盖不到的部分——这样加起来就是完整的"沪深主板+创业板+科创板"选股范围。
    3. 对以上两部分候选做完整的首板/地量/钝化/相对低位历史校验——比把全市场几千只
       股票的历史都拉一遍快很多。

用法：
    python daily_scan.py                        # 快速方案（推荐日常使用），含科创板
    python daily_scan.py --days 10               # 涨停股池回溯的交易日数（默认7）
    python daily_scan.py --skip-star-market       # 跳过科创板部分，只用涨停股池那一小步（更快）
    python daily_scan.py --full-universe         # 旧方案：全市场逐只扫描（很慢，收盘后跑）
    python daily_scan.py --full-universe --limit 300   # 全市场方案的小范围测试
    python daily_scan.py --workers 16                   # 调整并发进程数（默认8）

建议：
    - 收盘后（15:30之后）运行，当天行情已经走完，扫描结果更稳定。
    - 快速方案本身较快，但科创板部分要单独扫将近1000只股票，会明显拉长总耗时，尤其
      在东方财富接口不可用、降级为新浪分页拉取的网络环境下。第一次给某只股票拉历史
      较慢，之后每天都是增量拉取，会快很多。
    - 可以用 cron / launchd 设置成每天自动运行一次，写法见 README.md。
    - app.py 只读取本脚本写入数据库的结果，本身不发起任何扫描请求、也没有"刷新"按钮。
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

from astock_toolkit import bottom_pattern_scanner as bps, config, data_source as ds, db, sector_heat

# 重定向到文件/管道（比如 cron 里 `>> log 2>&1`）时 stdout 默认是整块缓冲的，进度
# 打印会攒到进程结束才一次性写出。这里强制行缓冲，保证日志能实时看到扫描进度。
try:
    sys.stdout.reconfigure(line_buffering=True)
except (AttributeError, ValueError):
    pass


def _tag_board_heat(context: str):
    """尽力而为地给观察池里的信号补上所属热门板块（东方财富板块成分股接口不可用时跳过，
    不影响已经保存的信号本身）。"""
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 拉取板块热门度并标注（{context}）...")
    try:
        ranking = sector_heat.get_sector_strength_ranking()
        if ranking.empty:
            print("    板块热门度获取失败，本次跳过。最近内部错误：", ds.LAST_ERROR)
            return
        membership = sector_heat.build_board_membership_map(ranking)
        all_signals = db.list_watchlist()
        tagged = 0
        for item in all_signals:
            info = membership.get(item["code"])
            if info:
                db.update_watchlist_board(item["code"], item["trigger_date"], info["board"], info["board_pct_chg"])
                tagged += 1
        print(f"    {tagged}/{len(all_signals)} 只信号标注上了所属热门板块"
              f"（其余为该股不在热度前{config.TOP_BOARDS_FOR_HEAT_TAGGING}的板块里，"
              f"或涨停股池已给过所属行业、板块成分股接口未覆盖到）。")
        db.set_setting("last_board_ranking_json",
                        ranking.head(20).to_json(orient="records", force_ascii=False))
    except Exception as e:  # noqa: BLE001
        print(f"    板块热门度标注过程出错（不影响已保存的信号结果）：{e!r}")


def run_fast(trading_days: int, skip_star_market: bool, workers: int):
    t0 = time.time()
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 拉取近{trading_days}个交易日涨停股池"
          f"（沪深主板+创业板，快速候选名单）...")
    pool = ds.get_recent_limit_up_candidates(trading_days=trading_days)
    if pool.empty:
        print("❌ 未能获取涨停股池数据（可能是非交易日窗口，或接口暂不可用）。最近内部错误：", ds.LAST_ERROR)
        sys.exit(1)
    n_codes = pool["code"].nunique()
    print(f"    候选名单：{n_codes} 只股票（{len(pool)} 条涨停记录——同一只股票窗口内如果连续涨停"
          f"多次会记多条，比如\"连板5天\"就会出现5条，所以记录数比股票数多是正常的）。"
          f"这份名单本身不含ST、不含科创板，是数据源接口自己的限制。并发 {workers} 进程。")

    def _progress(i, total):
        if i % 20 == 0 or i == total:
            print(f"    校验进度 {i}/{total}（已用时 {time.time()-t0:.0f}s）")

    found = bps.scan_recent_limit_up_pool(trading_days=trading_days, progress_cb=_progress, max_workers=workers)
    print(f"    候选名单中命中底部首板信号 {len(found)} 只。")

    if not skip_star_market:
        print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 扫描科创板（涨停股池接口不覆盖，单独补上）...")
        universe = ds.get_a_share_universe(exclude_st=config.UNIVERSE_EXCLUDE_ST)
        star_codes = [c for c in universe.to_dict("records") if str(c["code"]).startswith(("688", "689"))]
        if len(star_codes) < 100:
            # 科创板正常有大几百只，个位数/0只基本可以肯定是这一步的请求失败了（比如
            # 短时间内请求太多被限流），不是真的没有科创板股票——明确报出来，不要悄悄
            # 当成"科创板这次没有信号"糊弄过去，那样会掩盖真实的失败。
            print(f"    ⚠️ 科创板范围内只有 {len(star_codes)} 只，明显不正常（正常应有大几百只），"
                  f"本次跳过科创板扫描。最近内部错误：{ds.LAST_ERROR}")
        else:
            print(f"    科创板范围内共 {len(star_codes)} 只。")

            def _progress_star(i, total):
                if i % 50 == 0 or i == total:
                    print(f"    科创板扫描进度 {i}/{total}（已用时 {time.time()-t0:.0f}s）")

            star_found = bps.scan_universe_for_new_setups(star_codes, progress_cb=_progress_star, max_workers=workers)
            print(f"    科创板命中 {len(star_found)} 只。")
            found = found + star_found
    else:
        print("    已跳过科创板（--skip-star-market）。")

    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 刷新观察池回调状态（并发 {workers} 进程）...")
    updates = bps.refresh_pullback_signals(max_workers=workers)
    buy_signals = [u for u in updates if u["status"] == "buy_signal"]
    expired = [u for u in updates if u["status"] == "expired"]
    print(f"    {len(buy_signals)} 只进入回调买点区间，{len(expired)} 只观察期结束。")

    db.set_setting("last_scan_at", db.now_str())
    db.set_setting("last_scan_universe_count",
                    f"{n_codes} 只（近{trading_days}个交易日涨停股池：沪深主板+创业板）"
                    + ("" if skip_star_market else " + 科创板全量"))

    _tag_board_heat("快速方案")
    elapsed = time.time() - t0
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 扫描完成，共用时 {elapsed/60:.1f} 分钟。")


def run_full_universe(limit: int | None, workers: int):
    t0 = time.time()
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 获取选股范围（沪深主板+创业板+科创板，不含北交所）...")
    universe = ds.get_a_share_universe(exclude_st=config.UNIVERSE_EXCLUDE_ST)
    if universe.empty:
        print("❌ 未能获取股票代码列表，请检查网络后重试。最近内部错误：", ds.LAST_ERROR)
        sys.exit(1)
    codes = universe.to_dict("records")
    if limit:
        codes = codes[:limit]
    print(f"    范围内共 {len(universe)} 只，本次扫描 {len(codes)} 只（并发 {workers} 进程）。")

    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 扫描底部首板信号（逐只拉取历史行情，请耐心等待）...")

    def _progress(i, total):
        if i % 50 == 0 or i == total:
            print(f"    进度 {i}/{total}（已用时 {time.time()-t0:.0f}s）")

    found = bps.scan_universe_for_new_setups(codes, progress_cb=_progress, max_workers=workers)
    print(f"    本次新增命中 {len(found)} 只。")

    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 刷新观察池回调状态（并发 {workers} 进程）...")
    updates = bps.refresh_pullback_signals(max_workers=workers)
    buy_signals = [u for u in updates if u["status"] == "buy_signal"]
    expired = [u for u in updates if u["status"] == "expired"]
    print(f"    {len(buy_signals)} 只进入回调买点区间，{len(expired)} 只观察期结束。")

    db.set_setting("last_scan_at", db.now_str())
    db.set_setting("last_scan_universe_count", f"{len(codes)} 只（全市场方案）")

    _tag_board_heat("全市场方案")
    elapsed = time.time() - t0
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 扫描完成，共用时 {elapsed/60:.1f} 分钟。")


def main():
    parser = argparse.ArgumentParser(description="底部首板每日扫描")
    parser.add_argument("--days", type=int, default=7, help="快速方案：涨停股池回溯的交易日数（默认7）")
    parser.add_argument("--skip-star-market", action="store_true",
                         help="快速方案默认包含科创板（单独补扫一次），加此参数跳过以求更快")
    parser.add_argument("--full-universe", action="store_true", help="改用旧的全市场逐只扫描方案（很慢）")
    parser.add_argument("--limit", type=int, default=None, help="仅 --full-universe 时生效：只扫描前N只（测试用）")
    parser.add_argument("--workers", type=int, default=8, help="并发扫描的进程数（默认8）")
    args = parser.parse_args()

    db.init_db()
    if args.full_universe:
        run_full_universe(limit=args.limit, workers=args.workers)
    else:
        run_fast(trading_days=args.days, skip_star_market=args.skip_star_market, workers=args.workers)


if __name__ == "__main__":
    main()
