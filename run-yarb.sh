#!/usr/bin/env bash
# Cron/systemd 用：加载仓库根目录 .env 后执行 python3 -m src（见 README「定时任务」）。
set -euo pipefail
cd "$(dirname "$0")"
set -a
# shellcheck source=/dev/null
[ -f .env ] && . ./.env
set +a
exec path/to/venv/bin/python3 -m src "$@"
