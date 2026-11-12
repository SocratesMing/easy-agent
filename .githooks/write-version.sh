#!/bin/sh
# 由 post-commit / post-checkout / post-merge / post-rewrite 调用，刷新仓库根的 version.json。
# 失败不影响 git 操作本身。
ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
for py in "$ROOT/.venv/bin/python" "$ROOT/.venv/Scripts/python.exe" python3 python; do
    if command -v "$py" >/dev/null 2>&1; then
        "$py" "$ROOT/scripts/release/write_version.py" --source "git-hook:$1" --quiet || true
        exit 0
    fi
done
exit 0
