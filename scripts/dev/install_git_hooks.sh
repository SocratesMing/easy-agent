#!/bin/sh
# 启用仓库自带的 git hooks（.githooks/）：每次提交、切分支、合并、rebase 后刷新 version.json。
# 每个 clone 执行一次即可。
set -e
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
chmod +x .githooks/*
git config core.hooksPath .githooks
python3 scripts/release/write_version.py --source install-hooks
echo "已启用 .githooks（core.hooksPath=.githooks）"
