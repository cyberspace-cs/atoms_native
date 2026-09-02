#!/usr/bin/env bash
# 本地一键 CI 门禁（Gitee 环境的 GitHub Actions 等价物）。
# 全链路：语法编译 → 单元测试 → 门禁自测（负例注入）→ mock 起服 → smoke → eval 门禁。
# 任何一步失败立即退出非零（红）。
set -uo pipefail
cd "$(dirname "$0")/.."

PASS=0; FAIL=0
step() {
  local name="$1"; shift
  echo "───────── ▶ ${name}"
  if "$@"; then
    echo "✅ ${name}"; PASS=$((PASS+1))
  else
    echo "❌ ${name}（exit=$?）"; FAIL=$((FAIL+1))
    echo; echo "门禁结果：RED（${FAIL} 步失败）"
    exit 1
  fi
}

step "1/6 语法编译 (compileall)" python3 -m compileall -q server tests scripts
step "2/6 单元测试" python3 tests/unit_tests.py
step "3/6 门禁自测（负例注入）" python3 tests/gate_test.py

# mock 模式起服（无 key 也可跑）。先彻底清理同端口残留实例（SIGTERM→SIGKILL），
# 否则上次被 timeout 杀掉的脚本的子进程会占住端口导致 smoke 连接拒绝。
PORT="${CI_GATE_PORT:-8098}"
pkill -f "uvicorn main:app --host 127.0.0.1 --port ${PORT}" 2>/dev/null || true
sleep 1
pkill -9 -f "uvicorn main:app --host 127.0.0.1 --port ${PORT}" 2>/dev/null || true
(cd server && LLM_PROVIDER=deepseek python3 -m uvicorn main:app --host 127.0.0.1 --port ${PORT} >/tmp/ci_gate_uvicorn.log 2>&1 & echo $! > /tmp/ci_gate.pid)
trap 'kill "$(cat /tmp/ci_gate.pid 2>/dev/null)" 2>/dev/null || true' EXIT

READY=0
for i in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:${PORT}/api/models" >/dev/null; then READY=1; echo "  服务就绪（${i}s）"; break; fi
  sleep 1
done
if [ "$READY" != "1" ]; then
  echo "❌ 4/6 mock 服务未就绪，uvicorn 日志尾部："
  tail -20 /tmp/ci_gate_uvicorn.log
  echo; echo "门禁结果：RED（服务启动失败）"
  exit 1
fi
echo "✅ 4/6 mock 服务就绪"; PASS=$((PASS+1))

step "5/6 smoke 全链路" env ATOMS_BASE="http://127.0.0.1:${PORT}" python3 tests/smoke.py

step "6/6 评估门禁 (mock, run-twice-average)" bash -c \
  "(cd server && python3 -m evals.runner --runs 2 --model mock_ci_unavailable) && python3 scripts/eval_gate.py --expect-mock"
# 说明：CI 门禁固定走 mock（无 key 也可跑、秒级、确定性）；
# 全量真实评估成本高（47 case × N 次 × 数分钟），按需单独执行：
#   cd server && python -m evals.runner --runs 2                 # 真实全量（约小时级）
#   cd server && python -m evals.runner --runs 2 --only gen_pure_bmi   # 真实单 case

echo
echo "门禁结果：GREEN（${PASS} 步全部通过）——可合并/发布。"
