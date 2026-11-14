#!/usr/bin/env bash
# Tekla 中继一键启动/重启（Linux 代理侧）。
# 解决「Tekla 代理正忙（已有任务在执行）」：杀掉旧中继进程并以新版源码重启，
# 立即清空卡死的任务槽。新版中继自带孤儿任务自愈（--stale）+ /reset 接口。
#
# 用法：  bash tools/start-relay.sh
# token： 环境变量 TEKLA_RELAY_TOKEN，或文件 ~/.tekla_relay_token（仓库不含密钥）。
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOKEN="${TEKLA_RELAY_TOKEN:-$(cat "$HOME/.tekla_relay_token" 2>/dev/null || true)}"
PORT="${TEKLA_RELAY_PORT:-8765}"
if [ -z "$TOKEN" ]; then
  echo "缺少 token：请设 TEKLA_RELAY_TOKEN 或写入 ~/.tekla_relay_token" >&2
  exit 1
fi
# 杀旧中继（仅匹配 remote_relay.py 进程）
pkill -f "tools/remote_relay.py" 2>/dev/null || true
sleep 1
# 启新（nohup 持久化，日志追加到 ~/.tekla_relay.log）
nohup python3 "$DIR/tools/remote_relay.py" --token "$TOKEN" --host 0.0.0.0 --port "$PORT" \
  >> "$HOME/.tekla_relay.log" 2>&1 &
disown || true
sleep 1.5
# 验证
HEALTH="$(curl -s -m 5 "http://127.0.0.1:$PORT/health" || true)"
if echo "$HEALTH" | grep -q '"ok"'; then
  echo "relay 已启动: http://127.0.0.1:$PORT"
  echo "$HEALTH"
else
  echo "启动失败，最近日志：" >&2
  tail -5 "$HOME/.tekla_relay.log" >&2 || true
  exit 1
fi
