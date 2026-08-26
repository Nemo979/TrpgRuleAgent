#!/usr/bin/env bash
set -euo pipefail

runtime_root="${TRPG_RUNTIME_ROOT:-/Users/nemo_xu/.codex/trpg-rule-agent-runtime}"
runtime_env="${runtime_root}/secrets.env"
runtime_config="${runtime_root}/app.yaml"
runtime_python="${runtime_root}/venv/bin/python"

if [[ ! -f "${runtime_env}" ]]; then
  echo "缺少 ${runtime_env}。" >&2
  exit 1
fi

if [[ ! -x "${runtime_python}" ]]; then
  echo "缺少共享 Python 运行环境 ${runtime_root}/venv。" >&2
  exit 1
fi

set -a
source "${runtime_env}"
set +a

export TRPG_CONFIG="${runtime_config}"
export PYTHONPATH="services/app-python/src:services/retrieval-python/src${PYTHONPATH:+:${PYTHONPATH}}"

if ! "${runtime_python}" -c \
  "from trpg_app.config import load_config; load_config()"; then
  echo "应用配置校验失败；请检查 ${runtime_config} 和 ${runtime_env}。" >&2
  exit 1
fi

exec "${runtime_python}" -m trpg_app.main
