#!/bin/sh
# 由 post-commit / post-checkout / post-merge / post-rewrite 调用，刷新仓库根的 version.json。
# Linux 与 Windows（Git for Windows 自带 sh）共用；失败不影响 git 操作本身。
ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
SCRIPT="$ROOT/scripts/release/write_version.py"

# Windows 的 python3 / python 可能是应用商店占位程序（存在但不能运行），所以逐个试跑而不是只看 command -v
run_with() {
    "$@" -c "import sys; sys.exit(sys.version_info < (3, 11))" >/dev/null 2>&1 || return 1
    "$@" "$SCRIPT" --quiet || true
    exit 0
}

for py in "$ROOT/.venv/bin/python" "$ROOT/.venv/Scripts/python.exe" python3 python; do
    run_with "$py"
done
run_with py -3
exit 0
