// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/visual/mount.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
/**
 * 可视化卡片挂载（Vue 2 / Vue 3 共用；直接操作 DOM，不依赖框架）。
 *
 * 把 markdown 渲染出的占位元素（./fence.js）或工具卡片里的图表挂成卡片：
 * 标题栏（源码 / 下载）+ 一个 sandbox="allow-scripts" 的 iframe。iframe 加载后端托管的
 * 框架页（/agent/viz/frame.html），不同源：拿不到外层页面、登录 token 和 localStorage；
 * 渲染内容只通过 postMessage 传进去，框架页回报高度、渲染结果和导出的图片。
 *
 * - 进入视口附近才创建 iframe（长对话里图多时省内存）；
 * - 框架页没响应、或当前环境没开启这类可视化时，退回显示源码；
 * - 主题跟随最近的 [data-theme] 祖先（Vue 2 是 .easy-agent-root，Vue 3 是 <html>），切换时通知框架页；
 * - 组件里的 sendPrompt(text) 以冒泡的 `ea-visual-prompt` 事件交给外层（detail.text）。
 */
import { visualLabel } from './fence.js'

const HOST = 'ea-visual-host'
const FRAME = 'ea-visual-frame'
const READY_TIMEOUT_MS = 10000
const MIN_HEIGHT = 40
const MAX_HEIGHT = 2000

const controllers = new WeakMap()
const live = new Set()
const waiting = new WeakMap()
const watchedThemeRoots = new WeakSet()
let listening = false
let viewObserver = null
let themeObserver = null

function onMessage(event) {
  const ctl = event.source ? controllers.get(event.source) : null
  const data = event.data
  if (!ctl || !data || data.source !== FRAME) return
  ctl.handle(data)
}

function ensureListener() {
  if (listening || typeof window === 'undefined') return
  window.addEventListener('message', onMessage)
  listening = true
}

function themeOf(el) {
  const root = el.closest('[data-theme]')
  return root && root.getAttribute('data-theme') === 'dark' ? 'dark' : 'light'
}

function prune() {
  for (const ctl of Array.from(live)) {
    if (!ctl.el.isConnected) live.delete(ctl)
  }
}

function broadcastTheme() {
  prune()
  for (const ctl of live) ctl.setTheme(themeOf(ctl.el))
}

function watchTheme(el) {
  const root = el.closest('[data-theme]')
  if (!root || watchedThemeRoots.has(root) || typeof MutationObserver !== 'function') return
  watchedThemeRoots.add(root)
  if (!themeObserver) themeObserver = new MutationObserver(broadcastTheme)
  themeObserver.observe(root, { attributes: true, attributeFilter: ['data-theme'] })
}

function whenVisible(el, start) {
  if (typeof IntersectionObserver !== 'function') {
    start()
    return
  }
  if (!viewObserver) {
    viewObserver = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue
        const run = waiting.get(entry.target)
        viewObserver.unobserve(entry.target)
        waiting.delete(entry.target)
        if (run) run()
      }
    }, { rootMargin: '300px 0px' })
  }
  waiting.set(el, start)
  viewObserver.observe(el)
}

function readSource(el) {
  const node = el.querySelector('.code-block-wrapper pre code, .code-block-wrapper pre, .ea-visual-source')
  return node ? node.textContent || '' : ''
}

function makeButton(text, title, onClick) {
  const btn = document.createElement('button')
  btn.type = 'button'
  btn.className = 'ea-visual-btn'
  btn.textContent = text
  btn.title = title
  btn.addEventListener('click', onClick)
  return btn
}

function timestamp() {
  const d = new Date()
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`
}

function saveAs(el, href, filename) {
  const link = document.createElement('a')
  link.href = href
  link.download = filename
  link.rel = 'noopener'
  link.style.display = 'none'
  el.appendChild(link)
  link.click()
  link.remove()
}

function openLink(url) {
  const value = String(url || '')
  if (/^https?:\/\//i.test(value)) window.open(value, '_blank', 'noopener,noreferrer')
}

function widgetDocument(source) {
  return `<!doctype html>\n<html lang="zh-CN">\n<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head>\n<body>\n${source}\n</body>\n</html>\n`
}

function createController(el, options) {
  const kind = options.kind || el.getAttribute('data-visual-kind') || 'echarts'
  const text = options.source != null ? String(options.source) : readSource(el)
  const label = options.title || visualLabel(kind)

  const card = document.createElement('div')
  card.className = 'ea-visual-card'
  const header = document.createElement('div')
  header.className = 'ea-visual-header'
  const title = document.createElement('span')
  title.className = 'ea-visual-title'
  title.textContent = label
  const actions = document.createElement('span')
  actions.className = 'ea-visual-actions'
  const stage = document.createElement('div')
  stage.className = 'ea-visual-stage'
  const status = document.createElement('div')
  status.className = 'ea-visual-status'
  status.textContent = '加载中…'
  stage.appendChild(status)

  const ctl = {
    el,
    kind,
    frame: null,
    theme: themeOf(el),
    timer: null,
    start() {
      // 等进入视口的这段时间里消息可能已被重渲，占位元素不在页面上就不建 iframe
      if (!el.isConnected || ctl.frame) return
      const frame = document.createElement('iframe')
      frame.className = 'ea-visual-frame'
      // 只给脚本权限：不同源（拿不到外层与 token）、不能弹窗、不能跳转外层页面、不能提交表单
      frame.setAttribute('sandbox', 'allow-scripts')
      frame.setAttribute('referrerpolicy', 'no-referrer')
      frame.setAttribute('title', label)
      frame.style.height = '0px'
      // iframe 元素与框架页的 color-scheme 不一致时浏览器会给它垫一层不透明底色（深色下是白块）
      frame.style.colorScheme = ctl.theme
      stage.appendChild(frame)
      frame.src = options.frameUrl
      controllers.set(frame.contentWindow, ctl)
      ctl.frame = frame
      el.setAttribute('data-visual-state', 'loading')
      ctl.timer = setTimeout(() => ctl.fallback('渲染器没有响应（可能被页面的安全策略拦截），先显示源码。'), READY_TIMEOUT_MS)
    },
    post(message) {
      const target = ctl.frame && ctl.frame.contentWindow
      // 沙箱 iframe 的源是不透明的 null，只能用 '*'；发出去的只有这张图自己的源码
      if (target) target.postMessage(Object.assign({}, message, { source: HOST }), '*')
    },
    handle(data) {
      if (data.type === 'ready') {
        clearTimeout(ctl.timer)
        if (Array.isArray(data.kinds) && data.kinds.indexOf(kind) !== -1) ctl.post({ type: 'render', kind, text, theme: ctl.theme })
        else ctl.fallback('当前环境没有开启这类可视化，显示源码。')
      } else if (data.type === 'size') {
        if (ctl.frame) ctl.frame.style.height = `${Math.max(MIN_HEIGHT, Math.min(MAX_HEIGHT, Number(data.height) || 0))}px`
      } else if (data.type === 'rendered') {
        el.setAttribute('data-visual-state', 'rendered')
        status.textContent = ''
        status.style.display = 'none'
      } else if (data.type === 'error') {
        ctl.fail(String(data.message || '渲染失败'))
      } else if (data.type === 'unsupported') {
        ctl.fallback('当前环境没有开启这类可视化，显示源码。')
      } else if (data.type === 'widget-error') {
        status.textContent = `组件脚本出错：${String(data.message || '')}`
        status.style.display = ''
        status.className = 'ea-visual-status is-error'
      } else if (data.type === 'export') {
        if (data.dataUrl) saveAs(el, data.dataUrl, `${label}-${timestamp()}.${data.format || 'png'}`)
      } else if (data.type === 'prompt') {
        el.dispatchEvent(new CustomEvent('ea-visual-prompt', { bubbles: true, detail: { text: String(data.text || ''), kind } }))
      } else if (data.type === 'link') {
        openLink(data.url)
      }
    },
    setTheme(theme) {
      if (theme === ctl.theme) return
      ctl.theme = theme
      if (ctl.frame) ctl.frame.style.colorScheme = theme
      ctl.post({ type: 'theme', theme })
    },
    fail(message) {
      el.setAttribute('data-visual-state', 'error')
      status.textContent = `渲染失败：${message}`
      status.className = 'ea-visual-status is-error'
      status.style.display = ''
      if (ctl.frame) ctl.frame.style.height = '0px'
      el.classList.add('show-source')
    },
    fallback(message) {
      clearTimeout(ctl.timer)
      el.setAttribute('data-visual-state', 'fallback')
      status.textContent = message
      status.className = 'ea-visual-status'
      status.style.display = ''
      if (ctl.frame) {
        ctl.frame.remove()
        ctl.frame = null
      }
      el.classList.add('show-source')
    },
    download() {
      if (kind === 'widget') {
        const url = URL.createObjectURL(new Blob([widgetDocument(text)], { type: 'text/html;charset=utf-8' }))
        saveAs(el, url, `${label}-${timestamp()}.html`)
        setTimeout(() => URL.revokeObjectURL(url), 1000)
        return
      }
      if (el.getAttribute('data-visual-state') === 'rendered') ctl.post({ type: 'export' })
    },
  }

  actions.appendChild(makeButton('源码', '查看 / 收起源码', () => el.classList.toggle('show-source')))
  actions.appendChild(makeButton('下载', kind === 'widget' ? '下载为 HTML 文件' : '下载图片', () => ctl.download()))
  header.appendChild(title)
  header.appendChild(actions)
  card.appendChild(header)
  card.appendChild(stage)
  el.insertBefore(card, el.firstChild)
  el.setAttribute('data-visual-state', 'waiting')
  return ctl
}

/**
 * 挂载单个占位元素（已挂载、还在流式中的跳过）。
 * @param {Element} el `.ea-visual` 占位元素
 * @param {{frameUrl: string, kind?: string, source?: string, title?: string}} options
 *   frameUrl 是拼好 API 前缀的框架页地址；kind / source 不传时从元素上读
 */
export function mountVisual(el, options = {}) {
  if (!el || el.nodeType !== 1 || el.getAttribute('data-visual-state') || el.classList.contains('is-pending')) return
  ensureListener()
  watchTheme(el)
  prune()
  const ctl = createController(el, options)
  live.add(ctl)
  if (!options.frameUrl) {
    ctl.fallback('缺少渲染器地址，显示源码。')
    return
  }
  whenVisible(el, () => ctl.start())
}

/** 挂载容器里所有尚未挂载的占位元素（MarkdownBody 每次刷新后调用，幂等） */
export function mountVisuals(root, options = {}) {
  if (!root || typeof root.querySelectorAll !== 'function') return
  const nodes = root.querySelectorAll('.ea-visual:not(.is-pending):not([data-visual-state])')
  for (let i = 0; i < nodes.length; i += 1) mountVisual(nodes[i], options)
}
