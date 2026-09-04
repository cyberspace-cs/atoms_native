#!/usr/bin/env bash
# Linux compatibility entry: same mandatory gate as Windows and Actions.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -x server/venv/bin/python ]; then
  exec server/venv/bin/python scripts/ci_gate.py
else
  exec python3 scripts/ci_gate.py
fi
