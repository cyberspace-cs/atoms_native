#!/usr/bin/env bash
# Atoms Native 生产进程启动脚本（tmux 会话 atoms 专用）。
# 用法：tmux new-session -d -s atoms "bash /home/ubuntu/atoms-native/deploy/start_atoms.sh"
# 说明：tmux sh -c 对多层引号 + 相对路径的解析不稳（2026-09-04 部署事故），
#       这里统一用绝对路径，先 cd 再 exec，日志续写 /tmp/atoms.log。
cd /home/ubuntu/atoms-native/server || exit 1
exec /home/ubuntu/atoms-native/server/venv/bin/python -m uvicorn main:app \
  --host 0.0.0.0 --port 8088 >> /tmp/atoms.log 2>&1
