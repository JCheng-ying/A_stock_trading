#!/bin/bash
# 每天的发布流程：两个股票池都扫一遍 -> 生成静态网页 -> 推到 GitHub（Pages 自动重新部署）。
# 用法：./publish.sh
set -e
cd "$(dirname "$0")"
source .venv/bin/activate

echo "==> 1/5 扫描股票池一：底部首板（daily_scan.py）"
python daily_scan.py

echo "==> 2/5 扫描股票池二：地量后放量突破（scan_volume_surge.py，会比较慢）"
python scan_volume_surge.py

echo "==> 3/5 核对净资产真实值，剔除不达标的（check_book_value.py，只查还没查过的股票，很快）"
python check_book_value.py

echo "==> 4/5 生成静态网页（generate_site.py）"
python generate_site.py

echo "==> 5/5 提交并推送到 GitHub"
git add -A
if git diff --cached --quiet; then
  echo "没有变化，跳过提交。"
else
  git commit -m "更新每日数据 $(date +%Y-%m-%d)"
  git push
  echo "已推送，GitHub Pages 一两分钟内会更新。"
fi
