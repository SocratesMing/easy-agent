#!/bin/sh
# ============================================================
# 运行期前端配置生成器（POSIX sh 包装）
#
# 生成逻辑在 generate-runtime-config.mjs：用 node 的 JSON.stringify 做转义。
# 这里只用 POSIX sh 语法（不依赖 bash），ARM Linux / Alpine 等只有
# dash 或 busybox ash 的精简镜像也能正常执行。
#
# 环境变量与输出说明见 generate-runtime-config.mjs（默认 AGENT_ENV=prod、
# DIST_DIR=dist；显式 API_BASE_URL > .env.<AGENT_ENV> > "/"）。
# ============================================================
set -e

# 生成依赖 node（JSON 转义）：缺失时直接失败，避免产出不可信的配置文件。
if ! command -v node >/dev/null 2>&1; then
  echo "未找到 node，无法生成运行期前端配置（JSON 转义需要 node）" >&2
  exit 1
fi

# 脚本自身目录：保证从任意 cwd（如仓库根）调用都能找到 node 入口。
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
exec node "$SCRIPT_DIR/generate-runtime-config.mjs" "$@"
