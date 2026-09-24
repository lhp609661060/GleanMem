#!/usr/bin/env bash
# 一键准备工作区环境（不再依赖 /tmp）。
# 用法：bash scripts/setup.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
cd "$ROOT"

echo "[1/3] 创建 .venv (Python 3.13)"
uv venv .venv --python 3.13 >/dev/null

echo "[2/3] 安装固定依赖"
uv pip install --python .venv/bin/python -r requirements.txt

echo "[3/3] 确认 Playwright 浏览器"
if ! .venv/bin/python - <<'PY'
from pathlib import Path
import os
cache = Path.home()/"Library/Caches/ms-playwright"
ok = any(p.name.startswith("chromium_headless_shell-") for p in cache.glob("chromium_headless_shell-*")) if cache.exists() else False
raise SystemExit(0 if ok else 1)
PY
then
  .venv/bin/python -m playwright install chromium
fi

echo "完成。用 .venv/bin/python scripts/xxx.py 运行各脚本。"
