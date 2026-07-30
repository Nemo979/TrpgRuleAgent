#!/usr/bin/env bash
set -euo pipefail

if [[ ! -f .env ]]; then
  echo "缺少 .env；请先根据 config/app.yaml 配置本地环境变量。" >&2
  exit 1
fi

if [[ ! -x .venv312/bin/trpg-app ]]; then
  echo "缺少 Python 3.12 运行环境 .venv312。" >&2
  exit 1
fi

set -a
source .env
set +a

if ! .venv312/bin/python -c \
  "from trpg_app.config import load_config; load_config()"; then
  echo "应用配置校验失败；请检查 config/app.yaml 和 .env 中的必填环境变量。" >&2
  exit 1
fi

exec .venv312/bin/trpg-app
