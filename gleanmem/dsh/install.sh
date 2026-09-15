#!/usr/bin/env bash
# gleanmem -> DSH 一键接入脚本
# 等价于执行 python3 dsh/install.py "$@"
set -euo pipefail
cd "$(dirname "$0")"
exec /usr/bin/python3 ./install.py "$@"
