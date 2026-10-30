#!/bin/bash
# ============================================================
# 运行期前端配置生成器
#
# 解决“构建镜像时未知 AGENT_ENV / 后端地址”的问题：
# 前端在 build 时把 VITE_API_BASE_URL 固化进 bundle，无法预知部署环境的
# 后端地址。本脚本在 serve / 容器启动时根据环境变量写出 dist/runtime-config.js，
# 前端运行时读取 window.__RUNTIME_CONFIG__ 覆盖构建期值。
#
# 关键点：build 后前端按 AGENT_ENV 的值加载“该环境专属配置”。
#   本脚本读取各 .env.<mode> 中的 VITE_API_BASE_URL，构造 ENV_CONFIG 表写入
#   runtime-config.js，前端运行时用 AGENT_ENV 索引对应环境的后端地址。
#
# 环境变量：
#   API_BASE_URL  显式后端地址（推荐，容器/serve 分离部署必填，优先级最高）
#                例：http://easy-agent-backend:8000
#   AGENT_ENV     环境标识（dev/test/prod），决定加载哪份环境配置（默认 prod）
#   APP_TITLE     应用名称（可选）
#   APP_WELCOME_TITLE 首页欢迎语（可选，默认使用构建期 VITE_APP_WELCOME_TITLE）
#   DIST_DIR      输出目录（默认 dist）
#
# 后端地址选择优先级（高 -> 低）：
#   1) API_BASE_URL（显式传入，跨域部署 / 覆盖用）
#   2) .env.<AGENT_ENV> 的 VITE_API_BASE_URL（仅当显式设置了 AGENT_ENV 时才套用，
#      即“按 env 文件动态加载”）
#   3) "/"（同源相对路径，后端自己托管 dist 时的正确选择）
# 注：显式传 API_BASE_URL=/ 可强制走同源，忽略 env 文件中的地址。
# ============================================================
set -e

DIST_DIR="${DIST_DIR:-dist}"
OUT_FILE="$DIST_DIR/runtime-config.js"
mkdir -p "$DIST_DIR"

APP_TITLE="${APP_TITLE:-Easy Agent}"
APP_WELCOME_TITLE="${APP_WELCOME_TITLE:-}"
# 是否“显式”指定了 AGENT_ENV：只有显式指定时才按 .env.<AGENT_ENV> 自动套用后端地址，
# 未指定时保持同源 "/" 默认值，避免误用可能过期的环境地址。
AGENT_ENV_SET="${AGENT_ENV:-}"
AGENT_ENV_VAL="${AGENT_ENV_SET:-prod}"

# 读取各环境 .env.<mode> 中的 VITE_API_BASE_URL，作为该环境默认后端地址。
# 这是“按 AGENT_ENV 加载不同环境配置”的唯一数据源（与构建期保持一致）。
# .env.* 是 CRLF 行尾：去引号前先删掉 \r，否则它会落进下面的 JS 字符串字面量，
# 浏览器加载 runtime-config.js 即 SyntaxError（页面白屏）。
read_env_api() {
  local env="$1"
  local env_file=".env.${env}"
  local url=""
  if [ -f "$env_file" ]; then
    url=$(grep -E "^VITE_API_BASE_URL=" "$env_file" 2>/dev/null | tail -1 | cut -d= -f2- \
            | tr -d '\r' | sed "s/^[\"']//;s/[\"']$//")
  fi
  echo "$url"
}

DEV_API=$(read_env_api dev)
TEST_API=$(read_env_api test)
PROD_API=$(read_env_api prod)

# 当前 AGENT_ENV 对应的 .env 文件地址（按 env 文件动态加载的唯一入口）
ENV_API=""
if [ -n "$AGENT_ENV_SET" ]; then
  ENV_API=$(read_env_api "$AGENT_ENV_VAL")
fi

# 后端“不设置跨域(CORS)”时，前端必须与后端同源访问，否则浏览器会拦截请求。
# 优先级：显式 API_BASE_URL > .env.<AGENT_ENV> 的地址 > 相对路径 "/"。
#   - 显式传 API_BASE_URL=/ 可强制同源，忽略 env 文件里的地址；
#   - 未指定 AGENT_ENV 时保持 "/"，由后端自身托管 dist/ 跟随当前访问入口。
if [ -n "$API_BASE_URL" ]; then
  RT_API="$API_BASE_URL"
  RT_API_SRC="显式 API_BASE_URL (跨域，需后端开启 CORS)"
elif [ -n "$ENV_API" ]; then
  RT_API="$ENV_API"
  RT_API_SRC=".env.${AGENT_ENV_VAL} 的 VITE_API_BASE_URL (按 AGENT_ENV 动态加载)"
else
  RT_API="/"
  RT_API_SRC="相对路径 / (同源访问，无需跨域)"
fi

cat > "$OUT_FILE" <<EOF
// 本文件由启动脚本根据运行期环境变量自动生成，请勿手工编辑。
// 前端运行时根据 AGENT_ENV 值从 ENV_CONFIG 加载对应环境配置。
window.__RUNTIME_CONFIG__ = {
  AGENT_ENV: "${AGENT_ENV_VAL}",
  API_BASE_URL: "${RT_API}",
  APP_TITLE: "${APP_TITLE}",
  APP_WELCOME_TITLE: "${APP_WELCOME_TITLE}",
  ENV_CONFIG: {
    dev:  { API_BASE_URL: "${DEV_API:-}" },
    test: { API_BASE_URL: "${TEST_API:-}" },
    prod: { API_BASE_URL: "${PROD_API:-}" }
  }
};
EOF

# 生成结果必须是合法 JS：取值里混进 \r、换行或引号都会让页面加载即白屏，
# 这里直接让 build / serve 失败，而不是静默产出坏文件。
if command -v node >/dev/null 2>&1; then
  if ! node --check "$OUT_FILE"; then
    echo "$OUT_FILE 不是合法 JavaScript，请检查注入的环境变量 / .env.* 取值" >&2
    exit 1
  fi
else
  echo "未找到 node，跳过 $OUT_FILE 语法校验" >&2
fi

echo "==> 已生成运行期前端配置: $OUT_FILE"
echo "    AGENT_ENV        : ${AGENT_ENV_VAL}"
echo "    生效后端地址     : ${RT_API}  (来源: ${RT_API_SRC})"
echo "    首页欢迎语       : ${APP_WELCOME_TITLE:-<使用构建期配置>}"
echo "    各环境默认地址   : dev=${DEV_API:-<空>}  test=${TEST_API:-<空>}  prod=${PROD_API:-<空>}"
