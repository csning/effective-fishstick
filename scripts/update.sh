#!/bin/bash
# Effective Fishstick — 快速更新脚本
# 用法: bash scripts/update.sh
set -e

PROJECT_DIR="/opt/effective-fishstick"
cd "$PROJECT_DIR"

echo ">>> 拉取最新代码..."
git pull

echo ">>> 更新依赖..."
source .venv/bin/activate
pip install -e ".[dev,feishu]" -q

echo ">>> 重启服务..."
systemctl restart effective-fishstick

sleep 2
echo ">>> 服务状态:"
systemctl status effective-fishstick --no-pager -l | head -5

echo ""
echo ">>> 飞书连通性诊断:"
.venv/bin/python scripts/check_feishu.py

echo ""
echo ">>> 最近事件:"
curl -s http://127.0.0.1:8000/feishu/health | python3 -m json.tool 2>/dev/null || echo "(无法获取)"

echo ""
echo "完成！"
