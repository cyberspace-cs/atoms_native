#!/usr/bin/env bash
# Canary 发布脚本：备份 → 同步代码 → 重启 → 健康检查 → 异常自动回滚。
# 用法（在目标服务器上）：bash /home/ubuntu/atoms-native/deploy/canary.sh
# 可选环境变量：
#   SRC_REPO   源码目录（默认 /home/ubuntu/atoms-native 自身，配合 git pull）
#   HEALTH_URL 健康检查地址（默认 http://127.0.0.1:8088/api/models）
#   CANARY_SMOKE  冒烟检查地址（默认 http://127.0.0.1:8088/ 首页 200）
set -uo pipefail

APP_DIR="${APP_DIR:-/home/ubuntu/atoms-native}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8088/api/models}"
# 回滚后的验证地址固定指向真实服务端口（不随测试参数变化）
ROLLBACK_CHECK="${ROLLBACK_CHECK:-http://127.0.0.1:8088/api/models}"
SMOKE_URL="${SMOKE_URL:-http://127.0.0.1:8088/}"
STAMP="$(date +%m%d%H%M%S)"
BACKUP="/home/ubuntu/atoms-native.bak-canary-${STAMP}.tar.gz"
TMUX_SESSION="atoms"
START_CMD="cd ${APP_DIR}/server && ./venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8088 >> /tmp/atoms.log 2>&1"

echo "── [1/6] 备份当前版本 → ${BACKUP}"
tar czf "$BACKUP" -C "$(dirname "$APP_DIR")" --exclude venv --exclude __pycache__ --exclude '*.db' "$(basename "$APP_DIR")" \
  || { echo "备份失败，中止"; exit 1; }

rollback() {
  echo "── ⚠️ 健康检查失败，自动回滚 → ${BACKUP}"
  tmux kill-session -t "$TMUX_SESSION" 2>/dev/null || true
  rm -rf "${APP_DIR:?}/server/__pycache__"
  tar xzf "$BACKUP" -C "$(dirname "$APP_DIR")"
  tmux new-session -d -s "$TMUX_SESSION" "$START_CMD"
  sleep 4
  if curl -sf "$ROLLBACK_CHECK" >/dev/null; then
    echo "── ✅ 回滚完成，服务健康"
  else
    echo "── ❌ 回滚后仍不健康！需人工介入：ls -lh /home/ubuntu/atoms-native.bak-*"
  fi
  exit 1
}

echo "── [2/6] 同步最新代码（git pull）"
if ! git -C "$APP_DIR" pull --ff-only; then
  echo "git pull 失败（可能有本地改动），继续用备份基线"
fi

echo "── [3/6] 重启服务（tmux: ${TMUX_SESSION}）"
tmux kill-session -t "$TMUX_SESSION" 2>/dev/null || true
rm -rf "$APP_DIR/server/__pycache__"
tmux new-session -d -s "$TMUX_SESSION" "$START_CMD"

echo "── [4/6] 健康检查（最多等 30s）"
OK=0
for i in $(seq 1 30); do
  if curl -sf "$HEALTH_URL" >/dev/null; then OK=1; echo "  服务就绪（${i}s）"; break; fi
  sleep 1
done
[ "$OK" = "1" ] || rollback

echo "── [5/6] 冒烟检查（首页 + API 生成入口）"
curl -sf "$SMOKE_URL" >/dev/null || { echo "首页冒烟失败"; rollback; }
curl -sf "http://127.0.0.1:8088/api/models" >/dev/null || { echo "模型接口冒烟失败"; rollback; }

echo "── [6/6] canary 观察窗（60s 内错误日志抽样）"
sleep 60
if grep -qiE "Traceback|ERROR" /tmp/atoms.log 2>/dev/null; then
  RECENT=$(tail -50 /tmp/atoms.log | grep -ciE "Traceback|ERROR" || true)
  if [ "${RECENT:-0}" -gt 0 ]; then
    echo "  日志发现 ${RECENT} 条近期错误"
    rollback
  fi
fi

echo "── ✅ canary 通过：健康 + 冒烟 + 60s 日志无异常。备份保留于 ${BACKUP}（异常可随时回滚）"
