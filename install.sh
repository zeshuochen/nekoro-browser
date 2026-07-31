#!/usr/bin/env bash
# nekoro-browser 安装脚本（macOS / Linux）
# 用法: ./install.sh [--skip-extension]
# 1. pip install  2. 引导加载 Chrome 扩展
set -euo pipefail

SKIP_EXTENSION=0
[ "${1:-}" = "--skip-extension" ] && SKIP_EXTENSION=1

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "================================================"
echo "  nekoro-browser 安装"
echo "================================================"

echo "[1/2] pip install"
PY="${PYTHON:-python3}"
"$PY" -m pip install -e "$PROJECT_ROOT" >/dev/null
echo "  OK"

if [ "$SKIP_EXTENSION" -eq 0 ]; then
    echo "[2/2] Chrome 扩展"
    echo "  1. 打开 chrome://extensions/  2. 开启开发者模式"
    echo "  3. 加载已解压的扩展 → $PROJECT_ROOT/extension"
    echo "  如之前加载过，先移除再重新加载"
fi

echo
echo "================================================"
echo "  完成！"
echo "  启动: nekoro-browser（前台，保持打开）"
echo "  命令: echo 'page_info()' | nekoro-browser（新终端）"
