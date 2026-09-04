#!/usr/bin/env bash
# 本地一键 CI 门禁（Gitee 环境的 GitHub Actions 等价物）。
# 全链路：语法编译 → 单元测试 → 门禁自测（负例注入）→ 防回退守护 →
#         mock 起服 → smoke + E2E 旅程（真浏览器） → eval 门禁。
# 任何一步失败立即退出非零（红）。
set -uo pipefail
cd "$(dirname "$0")/.."

# Python 解释器：优先用 server/venv（fastapi 等依赖只在 venv 里），
# 退化到 PATH 上的 python3（本地无 venv 的场景）。
VENV_PY="$(pwd)/server/venv/bin/python"
if [ -x "$VENV_PY" ]; then PY="$VENV_PY"; else PY="python3"; fi

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

step "1/8 语法编译 (compileall)" "$PY" -m compileall -q server tests scripts
step "2/8 单元测试" "$PY" tests/unit_tests.py
step "3/8 门禁自测（负例注入）" "$PY" tests/gate_test.py
step "4/8 防回退守护（基线/特征/契约）" "$PY" tests/regression_guard.py

# mock 模式起服（无 key 也可跑）。先彻底清理同端口残留实例（SIGTERM→SIGKILL），
# 否则上次被 timeout 杀掉的脚本的子进程会占住端口导致 smoke 连接拒绝。
PORT="${CI_GATE_PORT:-8098}"
pkill -f "uvicorn main:app --host 127.0.0.1 --port ${PORT}" 2>/dev/null || true
sleep 1
pkill -9 -f "uvicorn main:app --host 127.0.0.1 --port ${PORT}" 2>/dev/null || true
(cd server && LLM_PROVIDER=deepseek "$PY" -m uvicorn main:app --host 127.0.0.1 --port ${PORT} >/tmp/ci_gate_uvicorn.log 2>&1 & echo $! > /tmp/ci_gate.pid)
trap 'kill "$(cat /tmp/ci_gate.pid 2>/dev/null)" 2>/dev/null || true' EXIT

READY=0
for i in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:${PORT}/api/models" >/dev/null; then READY=1; echo "  服务就绪（${i}s）"; break; fi
  sleep 1
done
if [ "$READY" != "1" ]; then
  echo "❌ 5/8 mock 服务未就绪，uvicorn 日志尾部："
  tail -20 /tmp/ci_gate_uvicorn.log
  echo; echo "门禁结果：RED（服务启动失败）"
  exit 1
fi
echo "✅ 5/8 mock 服务就绪"; PASS=$((PASS+1))

step "6/8 smoke 全链路" env ATOMS_BASE="http://127.0.0.1:${PORT}" "$PY" tests/smoke.py

# E2E 用户旅程（真浏览器）：需要 playwright + chromium；服务器无 GUI，
# 无头模式由脚本自身默认（chromium.launch() 即 headless）。缺 playwright
# 时降级为 WARN（不阻塞），本地/有依赖环境全跑。
if "$PY" -c "import playwright" 2>/dev/null; then
  step "7/8 E2E 用户旅程（真浏览器）" env ATOMS_BASE="http://127.0.0.1:${PORT}" "$PY" tests/e2e_journeys.py
else
  echo "⚠️ 7/8 E2E 用户旅程：跳过（未安装 playwright）"
fi

step "8/8 评估门禁 (mock, run-twice-average)" bash -c \
  "(cd server && '$PY' -m evals.runner --runs 2 --model mock_ci_unavailable) && '$PY' scripts/eval_gate.py --expect-mock"
# 说明：CI 门禁固定走 mock（无 key 也可跑、秒级、确定性）；
# 全量真实评估成本高（47 case × N 次 × 数分钟），按需单独执行：
#   cd server && python -m evals.runner --runs 2                 # 真实全量（约小时级）
#   cd server && python -m evals.runner --runs 2 --only gen_pure_bmi   # 真实单 case

echo
echo "门禁结果：GREEN（${PASS} 步全部通过）——可合并/发布。"
