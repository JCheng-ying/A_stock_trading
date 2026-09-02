#!/bin/bash
# 每天的发布流程：扫描 -> 生成静态网页 -> 推到 GitHub（Pages 自动重新部署）。
# 用法：./publish.sh
set -e
cd "$(dirname "$0")"
source .venv/bin/activate

echo "==> 1/3 扫描（daily_scan.py）"
python daily_scan.py

echo "==> 2/3 生成静态网页（generate_site.py）"
python generate_site.py

echo "==> 3/3 提交并推送到 GitHub"
git add -A
if git diff --cached --quiet; then
  echo "没有变化，跳过提交。"
else
  git commit -m "更新每日数据 $(date +%Y-%m-%d)"
  git push
  echo "已推送，GitHub Pages 一两分钟内会更新。"
fi
