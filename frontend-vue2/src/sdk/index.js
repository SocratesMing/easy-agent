/**
 * Easy Agent AI 侧边栏 SDK：把 AiSidebar 嵌入任意第三方页面。
 *
 * - 自带 Vue 2 运行时，宿主页面不限框架（原生 / Vue 2 / Vue 3 / React / Angular …）；
 * - 挂载在 Shadow DOM 中：样式双向隔离，宿主 CSS 不影响侧边栏，侧边栏 CSS 也不泄漏；
 * - 会话、技能、MCP 均来自 Easy Agent 后端，与主站共用同一份数据。
 *
 * 构建：npm run build:sdk（见 scripts/build-sdk.mjs），产物在 dist-sdk/。
 * 用法与配置项：docs/AI侧边栏SDK集成指南.md
 */
import Vue from 'vue'
import '../style.css'
import AiSidebar from '../features/chat/components/AiSidebar.vue'
import tipDirective from '../directives/tip.js'
import { chatStore } from '../features/chat/store/chatStore.js'
import request, { configureRequest, storeAuth, clearAuth, getStoredToken } from '../utils/request.js'
import { passwordlessLogin } from '../api/auth.js'
import { getUserProfile } from '../api/files.js'
import { getModels } from '../api/settings.js'
import { setExternalPageContext } from '../utils/pageContext.js'
import { DEFAULT_COLORS } from '../theme.js'

// 以下占位符由 scripts/build-sdk.mjs 在构建后替换为真实内容
const CSS_TEXT = '__EASY_AGENT_SDK_CSS__'
const FONT_CSS_TEXT = '__EASY_AGENT_SDK_FONT_CSS__'
export const version = '__EASY_AGENT_SDK_VERSION__'

const SDK_TOKEN_KEY = 'easy_agent_sdk_token'
const SDK_USERNAME_KEY = 'easy_agent_sdk_username'
const MAIN_TOKEN_KEY = 'mini_agent_token'
const MAIN_USERNAME_KEY = 'mini_agent_username'
const FONT_STYLE_ID = 'easy-agent-sidebar-fonts'
const DEFAULT_Z_INDEX = 3000

// <script> 方式加载时记录脚本地址，用于解析字体等静态资源（打包器引入时为空）
const scriptSrc = (typeof document !== 'undefined' && document.currentScript && document.currentScript.src) || ''

Vue.config.productionTip = false
Vue.config.devtools = false
Vue.directive('tip', tipDirective)

// chatStore 等共享状态是模块级单例，同一页面只允许一个侧边栏实例
let current = null

function resolveElement(target, fallback) {
  if (target === false || target === null) return null
  if (!target) return fallback
  return typeof target === 'string' ? document.querySelector(target) : target
}

function hostCss(theme) {
  const vars = { ...DEFAULT_COLORS, ...(theme || {}) }
  const decl = Object.keys(vars)
    .filter(k => k.startsWith('--') && vars[k])
    .map(k => `${k}: ${vars[k]};`)
    .join(' ')
  // all: initial 切断宿主页面可继承属性（字体、颜色、行高…）对侧边栏的影响
  return `:host { all: initial; ${decl} font-family: 'Microsoft YaHei', '微软雅黑', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; font-size: 14px; line-height: 1.6; color: var(--text-primary); }\n`
}

// Shadow DOM 内声明的 @font-face 不生效，字体规则需注入宿主 document（仅 @font-face，不含选择器）
function injectFonts(assetsBaseURL) {
  if (!FONT_CSS_TEXT || FONT_CSS_TEXT.startsWith('__EASY_AGENT')) return
  if (document.getElementById(FONT_STYLE_ID)) return
  const base = assetsBaseURL || scriptSrc
  if (!base) return
  const css = FONT_CSS_TEXT.replace(/url\((['"]?)([^'")]+)\1\)/g, (m, q, path) => {
    if (/^(data:|https?:|\/\/)/.test(path)) return m
    try {
      return `url(${q}${new URL(path, base).href}${q})`
    } catch (_) {
      return m
    }
  })
  const style = document.createElement('style')
  style.id = FONT_STYLE_ID
  style.textContent = css
  document.head.appendChild(style)
}

function normalizePageContext(ctx) {
  if (!ctx) return { context: null, content: '' }
  const obj = typeof ctx === 'string' ? { content: ctx } : ctx
  return {
    context: {
      // key 用于「当前页面会话优先」：同一 key 再次唤起时回到该页面最近的会话
      key: String(obj.key || window.location.pathname || 'host'),
      page: 'host',
      title: obj.title || document.title || '当前页面',
      description: obj.description || '',
      suggestions: Array.isArray(obj.suggestions) ? obj.suggestions.filter(Boolean).slice(0, 4) : [],
    },
    content: typeof obj.content === 'string' ? obj.content : '',
  }
}

function withQueryParam(url, key, value) {
  const sep = url.includes('?') ? '&' : '?'
  return `${url}${sep}${key}=${encodeURIComponent(value)}`
}

/**
 * 初始化并挂载侧边栏。
 *
 * @param {Object} options 见 docs/AI侧边栏SDK集成指南.md「配置项」
 * @returns {Object} 实例 API：open / close / toggle / send / setPageContext / setToken / destroy …
 */
export function init(options = {}) {
  if (typeof window === 'undefined' || typeof document === 'undefined') {
    throw new Error('[EasyAgentSidebar] 仅支持在浏览器中运行')
  }
  if (current) {
    console.warn('[EasyAgentSidebar] 已初始化，返回现有实例；如需重建请先调用 destroy()')
    return current
  }
  if (!options.baseURL) {
    console.warn('[EasyAgentSidebar] 未配置 baseURL，将以相对路径 /agent/... 请求当前域名')
  }

  const auth = options.auth || {}
  const shareLogin = !!auth.shareMainAppLogin
  const features = { ...(options.features || {}) }
  // openInMain 同时控制「在主站打开会话」与「+」菜单的「在管理面板中打开」
  if (!options.mainAppUrl && !options.onOpenInMain && !options.onOpenPanel) features.openInMain = false

  let bootstrapping = true
  let reauthing = null
  let destroyed = false
  let dockTarget = null
  let dockOriginalMargin = ''
  let dockOriginalTransition = ''

  configureRequest({
    baseURL: options.baseURL || '',
    tokenKey: shareLogin ? MAIN_TOKEN_KEY : SDK_TOKEN_KEY,
    usernameKey: shareLogin ? MAIN_USERNAME_KEY : SDK_USERNAME_KEY,
    // 401：换新 token 后由请求层把失败的请求重发一次，用户无感
    refreshAuth: () => relogin(),
    // 无法重发的场景（如上传）：续期后刷新会话列表
    onAuthExpired: () => { handleAuthExpired() },
  })

  // ── 挂载容器：fixed 零尺寸宿主 + Shadow Root ────────────────────────
  const hostEl = document.createElement('div')
  hostEl.setAttribute('data-easy-agent-sidebar', version)
  hostEl.style.cssText = `position: fixed; top: 0; left: 0; width: 0; height: 0; z-index: ${Number(options.zIndex) || DEFAULT_Z_INDEX};`
  const containerEl = resolveElement(options.container, document.body) || document.body
  containerEl.appendChild(hostEl)

  const shadow = hostEl.attachShadow({ mode: 'open' })
  const styleEl = document.createElement('style')
  styleEl.textContent = hostCss(options.theme) + (CSS_TEXT.startsWith('__EASY_AGENT') ? '' : CSS_TEXT)
  shadow.appendChild(styleEl)
  const mountEl = document.createElement('div')
  shadow.appendChild(mountEl)
  injectFonts(options.assetsBaseURL)

  const state = Vue.observable({ ready: false, pageContext: null })

  function applyPageContext(ctx) {
    const resolved = typeof ctx === 'function' ? ctx() : ctx
    const { context, content } = normalizePageContext(resolved)
    setExternalPageContext(content)
    state.pageContext = context
  }

  function refreshPageContext() {
    if (typeof options.pageContext === 'function') applyPageContext(options.pageContext)
  }

  function applyDock({ dockWidth }) {
    if (typeof options.onLayoutChange === 'function') options.onLayoutChange({ dockWidth })
    if (!dockTarget) return
    if (dockWidth > 0) {
      dockTarget.style.transition = 'margin-right 0.22s cubic-bezier(0.22, 1, 0.36, 1)'
      dockTarget.style.marginRight = `${dockWidth}px`
    } else {
      dockTarget.style.marginRight = dockOriginalMargin
    }
  }

  const vm = new Vue({
    render(h) {
      if (!state.ready) return h('div')
      return h(AiSidebar, {
        ref: 'sidebar',
        props: {
          embedded: true,
          exposeGlobal: false,
          pageContext: state.pageContext,
          features,
          defaultMode: options.defaultMode || 'float',
          launcherLabel: options.launcherLabel || '问 AI',
          welcomeTitle: options.welcomeTitle || '有什么可以帮你？',
        },
        on: {
          // 唤起 / 展开 / 新建会话 / 点上下文标签时：重算函数形式的 pageContext，
          // 再交给宿主 refreshContext（宿主可在其中调用 setPageContext 推送最新内容）
          'refresh-context': () => {
            refreshPageContext()
            if (typeof options.refreshContext === 'function') options.refreshContext()
          },
          'open-change': (open, source) => {
            const cb = open ? options.onOpen : options.onClose
            if (typeof cb === 'function') cb(source)
          },
          'layout-change': applyDock,
          'open-in-main': (sessionId) => {
            if (typeof options.onOpenInMain === 'function') {
              options.onOpenInMain(sessionId)
            } else if (options.mainAppUrl) {
              window.open(withQueryParam(options.mainAppUrl, 'session', sessionId), '_blank', 'noopener')
            }
          },
          'open-panel': (tab) => {
            if (typeof options.onOpenPanel === 'function') {
              options.onOpenPanel(tab)
            } else if (options.mainAppUrl) {
              window.open(withQueryParam(options.mainAppUrl, 'panel', tab), '_blank', 'noopener')
            }
          },
        },
      })
    },
  }).$mount(mountEl)

  const sidebar = () => vm.$refs.sidebar

  // ── 鉴权 ─────────────────────────────────────────────────────────
  async function acquireToken({ forceLogin = false } = {}) {
    if (typeof auth.getToken === 'function') {
      const token = await auth.getToken()
      if (!token) throw new Error('getToken 未返回 token')
      storeAuth(token, '')
      return
    }
    if (auth.token && !forceLogin) {
      storeAuth(auth.token, '')
      return
    }
    if (auth.username) {
      // 已持有同一用户的 token 时先复用，避免每次加载页面都触发单点登录挤掉其他端
      const sameUser = localStorage.getItem(shareLogin ? MAIN_USERNAME_KEY : SDK_USERNAME_KEY) === auth.username
      if (!forceLogin && sameUser && getStoredToken()) return
      await passwordlessLogin(auth.username, auth.userId || '0')
      return
    }
    if (!getStoredToken()) {
      throw new Error('未登录：请通过 auth.token / auth.getToken / auth.username 提供登录信息')
    }
  }

  async function loadProfile() {
    const profile = await getUserProfile()
    if (!profile || !profile.username) throw new Error('获取用户信息失败')
    storeAuth(getStoredToken(), profile.username)
    chatStore.username = profile.username
  }

  /** 重新获取 token：并发调用合并为一次，成功 resolve true；失败通知宿主 onAuthExpired */
  function relogin() {
    if (bootstrapping || destroyed) return Promise.resolve(false)
    if (reauthing) return reauthing
    const canRelogin = typeof auth.getToken === 'function' || !!auth.username
    reauthing = (async () => {
      try {
        if (!canRelogin) throw new Error('登录已过期')
        await acquireToken({ forceLogin: true })
        await loadProfile()
        return true
      } catch (e) {
        if (typeof options.onAuthExpired === 'function') options.onAuthExpired(e)
        else console.warn('[EasyAgentSidebar] 登录已过期：', e && e.message)
        return false
      } finally {
        reauthing = null
      }
    })()
    return reauthing
  }

  async function handleAuthExpired() {
    if (await relogin() && sidebar()) await sidebar().loadSessions()
  }

  async function bootstrap() {
    try {
      await acquireToken()
      try {
        await loadProfile()
      } catch (e) {
        // 复用的旧 token 已失效：能重新登录就重登一次
        const status = e && e.response && e.response.status
        if (status !== 401 || !(typeof auth.getToken === 'function' || auth.username)) throw e
        await acquireToken({ forceLogin: true })
        await loadProfile()
      }
    } finally {
      bootstrapping = false
    }
    if (destroyed) return

    try {
      const cfg = await request({ url: '/agent/auth/config', method: 'get' })
      if (cfg && cfg.context_length) chatStore.contextLength = cfg.context_length
    } catch (_) { /* 非关键 */ }
    try {
      const data = await getModels()
      chatStore.models = data.models || []
      if (!chatStore.activeModel && data.active_model) chatStore.activeModel = data.active_model
    } catch (_) { /* 非关键：使用后端默认模型 */ }

    dockTarget = resolveElement(options.dockTarget, document.body)
    if (dockTarget) {
      dockOriginalMargin = dockTarget.style.marginRight
      dockOriginalTransition = dockTarget.style.transition
    }
    applyPageContext(options.pageContext)
    state.ready = true
    await Vue.nextTick()
    await sidebar().loadSessions()
  }

  const ready = bootstrap().then(
    () => {
      if (typeof options.onReady === 'function') options.onReady(api)
      return api
    },
    (err) => {
      console.error('[EasyAgentSidebar] 初始化失败：', err)
      if (typeof options.onError === 'function') options.onError(err)
      throw err
    }
  )
  // 避免宿主未处理 ready 时出现 unhandledrejection（错误已经由 onError / console 报告）
  ready.catch(() => {})

  const whenReady = (fn) => ready.then(() => (destroyed ? undefined : fn(sidebar())))

  const onPopState = () => refreshPageContext()
  window.addEventListener('popstate', onPopState)
  window.addEventListener('hashchange', onPopState)

  const api = {
    version,
    ready,
    /** 打开侧边栏：{ prompt?: 预填输入框, context?: 补充上下文文本, newSession?: 新建会话 } */
    open: (opts) => whenReady(s => s.openSidebar(opts || {})),
    close: () => whenReady(s => s.close()),
    toggle: () => whenReady(s => s.toggle()),
    isOpen: () => !!(sidebar() && sidebar().open),
    /** 打开侧边栏并直接发送一条消息 */
    send: (text) => whenReady(s => s.sendFromHost(text)),
    /** 预填输入框（不打开侧边栏、不发送） */
    setComposer: (text) => whenReady(s => s.setComposer(text)),
    /** 更新页面上下文（SPA 路由切换后调用）：对象 / 字符串 / 返回二者的函数 */
    setPageContext: (ctx) => {
      if (typeof ctx === 'function') options.pageContext = ctx
      applyPageContext(ctx)
    },
    /** 宿主刷新 token 后传入新 token */
    setToken: async (token) => {
      storeAuth(token, '')
      await loadProfile()
      if (sidebar()) await sidebar().loadSessions()
    },
    /** 宿主用户登出时调用：清除 SDK 保存的登录态并销毁侧边栏 */
    logout: () => {
      clearAuth()
      api.destroy()
    },
    destroy: () => {
      if (destroyed) return
      destroyed = true
      window.removeEventListener('popstate', onPopState)
      window.removeEventListener('hashchange', onPopState)
      if (dockTarget) {
        dockTarget.style.marginRight = dockOriginalMargin
        dockTarget.style.transition = dockOriginalTransition
      }
      vm.$destroy()
      hostEl.remove()
      setExternalPageContext('')
      chatStore.sessions = []
      chatStore.streamingSessions = []
      chatStore.username = ''
      current = null
    },
  }

  current = api
  return api
}

export default { init, version }
