#!/bin/bash
# 每天的发布流程：两个股票池分开跑，各自独立地"扫描 -> 生成网页 -> 推到 GitHub"，
# 不等两个池子都扫完才一起推——股票池一比较快，跑完先推一版，股票池二慢很多
# （可能十几分钟），跑完再推第二版。GitHub Pages 会各自触发一次自动重新部署。
# 用法：./publish.sh
set -e
cd "$(dirname "$0")"
source .venv/bin/activate

commit_and_push() {
  # $1: 提交信息里的池子名称
  git add -A
  if git diff --cached --quiet; then
    echo "没有变化，跳过提交。"
  else
    git commit -m "更新每日数据：$1 $(date +%Y-%m-%d)"
    git push
    echo "已推送，GitHub Pages 一两分钟内会更新。"
  fi
}

echo "==> [股票池一] 1/3 扫描底部首板（daily_scan.py）"
python daily_scan.py

echo "==> [股票池一] 2/3 核对净资产真实值，剔除不达标的（check_book_value.py，只查还没查过的股票，很快）"
python check_book_value.py

echo "==> [股票池一] 3/3 生成网页并推送"
python generate_site.py
commit_and_push "股票池一"

echo "==> [股票池二] 1/3 扫描地量后放量突破（scan_volume_surge.py，会比较慢）"
python scan_volume_surge.py

echo "==> [股票池二] 2/3 核对净资产真实值，剔除不达标的（check_book_value.py，只查还没查过的股票，很快）"
python check_book_value.py

echo "==> [股票池二] 3/3 生成网页并推送"
python generate_site.py
commit_and_push "股票池二"
