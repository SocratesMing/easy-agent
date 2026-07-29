#!/bin/bash
# ============================================================
# 设置全局 AGENT_ENV（默认 test），使后端与前端都按该环境加载配置：
#   - 后端：easy-web / app.py 据此选择 config.<env>.yaml
#   - 前端：npm run build / npm run serve 据此选择 .env.<mode> 与运行期配置
#
# 用法：
#   source scripts/set-agent-env.sh            # 当前终端立即生效 + 持久化到 rc
#   source scripts/set-agent-env.sh prod       # 指定其它环境（prod/test/dev）
#   bash  scripts/set-agent-env.sh            # 仅持久化到 rc（不影响当前 shell）
# ============================================================
set -e

ENV_VALUE="${1:-test}"

# 1) 立即对当前 shell 生效（仅当用 source 执行时有效）
export AGENT_ENV="$ENV_VALUE"

# 2) 持久化到 shell 配置文件，使所有新终端自动带上该变量
#    优先 .zshrc（zsh 会话），否则 .bashrc（bash 会话）
if [ -n "$ZSH_VERSION" ]; then
  RC_FILE="$HOME/.zshrc"
elif [ -n "$BASH_VERSION" ]; then
  RC_FILE="$HOME/.bashrc"
elif [ -f "$HOME/.zshrc" ]; then
  RC_FILE="$HOME/.zshrc"
else
  RC_FILE="$HOME/.bashrc"
fi

LINE="export AGENT_ENV=$ENV_VALUE"
if grep -q '^export AGENT_ENV=' "$RC_FILE" 2>/dev/null; then
  # 已存在则更新其值（避免重复写入）
  sed -i "s|^export AGENT_ENV=.*|$LINE|" "$RC_FILE"
  echo "==> 已更新 $RC_FILE : $LINE"
else
  printf '\n# 由 scripts/set-agent-env.sh 写入（easy-agent 运行环境）\n%s\n' "$LINE" >> "$RC_FILE"
  echo "==> 已写入 $RC_FILE : $LINE"
fi

echo "==> 全局 AGENT_ENV 已设为: $ENV_VALUE"
echo "    新开终端自动生效；当前终端可执行: source $RC_FILE"
