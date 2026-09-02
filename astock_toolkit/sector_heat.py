"""板块热门度：行业板块实时强度排名 + 给命中信号标注所属热门板块。

只服务于"底部首板"这一个战法的第4条筛选依据（结合板块热门度），不做选股/推荐。
被 daily_scan.py 调用；app.py 只读取扫描结果，不直接调用本模块里发起网络请求的函数。
"""

from __future__ import annotations

import pandas as pd

from . import config, data_source as ds, db


def get_current_mainline() -> str:
    """当前市场主线：优先用户在"设置"里保存的覆盖值，否则用 config.CURRENT_MAINLINE。"""
    return db.get_setting("current_mainline", config.CURRENT_MAINLINE)


def get_sector_strength_ranking() -> pd.DataFrame:
    """行业板块实时强度排名，并标注所属四大关注分组 + 主线加权后的得分。

    列：board, pct_chg, group(可能为空), score, leader_name, leader_pct_chg
    """
    ranking = ds.get_industry_board_ranking()
    if ranking.empty:
        return ranking

    def match_group(board_name: str) -> str | None:
        for group, keywords in config.SECTOR_GROUPS.items():
            for kw in keywords:
                if kw and kw in str(board_name):
                    return group
        return None

    mainline = get_current_mainline()
    ranking = ranking.copy()
    ranking["group"] = ranking["board"].map(match_group)
    ranking["score"] = ranking["pct_chg"].astype(float)
    is_mainline = ranking["group"] == mainline
    ranking.loc[is_mainline, "score"] += config.MAINLINE_BOOST
    return ranking.sort_values("score", ascending=False).reset_index(drop=True)


def build_board_membership_map(ranking: pd.DataFrame,
                                top_n: int = config.TOP_BOARDS_FOR_HEAT_TAGGING) -> dict[str, dict]:
    """对涨幅最靠前的 top_n 个板块尝试拉取成分股（东方财富专属接口），

    返回 {股票代码: {"board": 板块名, "board_pct_chg": 板块涨跌幅}}。
    该接口在东方财富不可用的网络下会跳过（返回空 dict），此时信号仍然产出，只是不标注板块。
    """
    membership: dict[str, dict] = {}
    if ranking.empty:
        return membership
    for _, row in ranking.head(top_n).iterrows():
        cons = ds.get_board_constituents(row["board"])
        if cons is None or cons.empty:
            continue
        for code in cons["code"]:
            # 涨幅最靠前的板块优先：同一只股票若已被更强板块标注过就不覆盖
            membership.setdefault(str(code), {"board": row["board"], "board_pct_chg": float(row["pct_chg"])})
    return membership
