/**
 * Easy Agent 运行时配置：模块唯一的配置入口（适配合同的实现）。
 *
 * - 外网独立应用（src/standalone/adapter.js）与内网宿主适配器（src/integrations/easyAgent.js）
 *   在 EasyAgentPage 首次渲染前调用 configureEasyAgent()；
 * - 模块内一律在「使用时」经 getRuntime() 取值，不在 import 时固化 window / env，
 *   因此晚于模块 import 的注入同样生效；
 * - 各字段的语义、默认值与示例见 integration/README.md「适配合同」。合同字段有不兼容变化时
 *   EASY_AGENT_CONTRACT_VERSION +1，同步工具据此醒目提示宿主调整适配器。
 *
 * 限制：同一 JS 运行时（一个打包产物）只有一份配置。主页面与页面内的 AI 侧边栏共用它；
 * 独立构建的侧边栏 SDK 自带一份模块副本，配置互不覆盖。
 */
import Vue from 'vue'

export const EASY_AGENT_CONTRACT_VERSION = 1

export const DEFAULT_APP_TITLE = 'Easy Agent'

/** 模块会请求的后端路径前缀（宿主若全局拦截 XHR / fetch，需对这些前缀放行） */
export const EASY_AGENT_API_PREFIXES = Object.freeze(['/agent/', '/api/knowledge/', '/api/personnel/'])

const MODES = ['standalone', 'embedded']

const SECTION_DEFAULTS = {
  api: {
    // 拼在所有模块请求路径（/agent、/api/knowledge/v1、/api/personnel、SSE、下载/预览地址）之前
    baseURL: '',
    // 可选 (path) => url：需要按前缀分别改写时使用（如 /agent 与 /api 走不同网关）；优先于 baseURL
    resolveUrl: null,
    // 可选 () => object：每个模块请求附加的请求头（网关头等）。不要返回宿主自己的登录凭证
    headers: null,
    withCredentials: false,
  },
  auth: {
    // Agent 登录态（token / 用户名）的本地存储键
    tokenKey: 'mini_agent_token',
    usernameKey: 'mini_agent_username',
    // 无有效登录态时显示模块自带的登录 / 注册页；false 显示「未授权访问」提示
    loginPage: true,
    // 允许地址栏 ?username=&user_id= 免密直登（外网开发 / 联调用）
    urlLogin: false,
    // async (helpers) => false | true | { profile }：用宿主身份换 Agent token，见 auth.js#runSignIn
    signIn: null,
    // async () => boolean：401 时续期；不配置时有 signIn 就复用 signIn，都没有则直接失效
    refresh: null,
    // (info: { reason, detail }) => void：Agent 登录态失效通知（只影响 Agent，不得据此登出宿主）
    onExpired: null,
  },
  app: {
    title: DEFAULT_APP_TITLE,
    welcomeTitle: `${DEFAULT_APP_TITLE}，让工作化繁为简`,
    // 是否把 app.title 写进 document.title（嵌入宿主时由宿主自己管标题）
    setDocumentTitle: false,
  },
  navigation: {
    // (vm) => object：读取当前路由 query（嵌入模式由宿主路由实现）
    getQuery: null,
    // (vm, patch) => void：patch 中值为 null 的键删除、其余替换，保留 path、hash 与其他 query
    replaceQuery: null,
  },
  theme: {
    mode: 'light',
    // 颜色令牌覆盖，如 { '--primary-color': '#0ea5e9' }；写在模块根节点上，不写宿主 html
    colors: {},
  },
  features: {
    // 页面内的 AI 侧边栏
    aiSidebar: true,
    // 是否挂载 window.EasyAgentAI（嵌入宿主时默认不挂，避免污染宿主全局）
    exposeGlobal: false,
  },
}

const MODE_DEFAULTS = {
  standalone: { app: { setDocumentTitle: true }, features: { exposeGlobal: true } },
  embedded: { auth: { loginPage: false, urlLogin: false }, app: { setDocumentTitle: false }, features: { exposeGlobal: false } },
}

const SECTIONS = Object.keys(SECTION_DEFAULTS)

function buildDefaults(mode) {
  const result = { mode }
  for (const key of SECTIONS) {
    result[key] = { ...SECTION_DEFAULTS[key], ...((MODE_DEFAULTS[mode] || {})[key] || {}) }
  }
  return result
}

// 响应式：主题、欢迎语等运行期改动可直接驱动界面更新
const runtime = Vue.observable({ ...buildDefaults('standalone'), configured: false })

let explicitOptions = {}

function warn(message) {
  if (typeof console !== 'undefined') console.warn(`[EasyAgent] ${message}`)
}

function validate(options) {
  for (const key of Object.keys(options)) {
    if (key !== 'mode' && !SECTIONS.includes(key)) warn(`未知配置项 ${key}（已忽略）`)
  }
  if (options.mode !== undefined && !MODES.includes(options.mode)) {
    throw new Error(`[EasyAgent] mode 只能是 ${MODES.join(' / ')}，收到 ${options.mode}`)
  }
  for (const key of SECTIONS) {
    const section = options[key]
    if (section === undefined) continue
    if (!section || typeof section !== 'object') throw new Error(`[EasyAgent] ${key} 必须是对象`)
    for (const field of Object.keys(section)) {
      if (!(field in SECTION_DEFAULTS[key])) warn(`未知配置项 ${key}.${field}（已忽略）`)
    }
  }
}

/**
 * 写入运行时配置（可重复调用，按段浅合并；同一份配置重复调用无副作用）。
 * 必须在 EasyAgentPage 渲染前调用；运行期间不要切换 auth.tokenKey / usernameKey。
 *
 * @param {object} options 见文件头说明
 * @returns {object} 生效后的配置（只读使用）
 */
export function configureEasyAgent(options = {}) {
  validate(options)
  const merged = { ...explicitOptions }
  if (options.mode !== undefined) merged.mode = options.mode
  for (const key of SECTIONS) {
    if (options[key]) {
      const known = {}
      for (const field of Object.keys(options[key])) {
        if (field in SECTION_DEFAULTS[key]) known[field] = options[key][field]
      }
      merged[key] = { ...(explicitOptions[key] || {}), ...known }
    }
  }
  explicitOptions = merged

  const next = buildDefaults(merged.mode || runtime.mode)
  for (const key of SECTIONS) Object.assign(next[key], merged[key] || {})
  next.api.baseURL = String(next.api.baseURL || '').replace(/\/+$/, '')
  // 整段替换（而不是逐字段改）：新对象由 Vue 重新观测，colors 等新增键也能触发更新
  runtime.mode = next.mode
  for (const key of SECTIONS) runtime[key] = next[key]
  runtime.configured = true
  return runtime
}

/** 当前生效配置（响应式对象；只读使用，修改请调用 configureEasyAgent） */
export function getRuntime() {
  return runtime
}

/** 恢复默认配置（仅测试与 SDK destroy 使用） */
export function resetEasyAgentConfig() {
  explicitOptions = {}
  const next = buildDefaults('standalone')
  runtime.mode = next.mode
  for (const key of SECTIONS) runtime[key] = next[key]
  runtime.configured = false
}
