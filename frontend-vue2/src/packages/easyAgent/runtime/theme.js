// 语义化颜色令牌（适配合同 theme）。
//
// 默认值即浅色主题，定义在 styles/base.css 的 .easy-agent-root 上；这里的覆盖值以内联 CSS 变量
// 写到模块根节点（EasyAgentPage 根元素 / SDK 影子根内的挂载点），任何 var(--token) 都随之改变。
// 不再写 document.documentElement：嵌入宿主后同名变量（--primary-color 等）会覆盖宿主自己的主题。
import { configureEasyAgent, getRuntime } from './config.js'

export const THEME_ROOT_CLASS = 'easy-agent-root'

export const DEFAULT_COLORS = {
  '--primary-color': '#0ea5e9',
  '--accent-color': '#0ea5e9',
  '--bg-primary': '#f8fafc',
  '--bg-secondary': '#ffffff',
  '--bg-tertiary': '#f1f5f9',
  '--bg-surface': '#ffffff',
  '--text-primary': '#1e293b',
  '--text-secondary': '#64748b',
  '--border-color': '#e2e8f0',
}

const STORAGE_KEY = 'easy_agent_theme_colors'

/** 只保留 `--` 开头且有值的令牌 */
export function themeStyle(colors = {}) {
  const style = {}
  for (const [token, value] of Object.entries(colors || {})) {
    if (token.startsWith('--') && value) style[token] = value
  }
  return style
}

/** 当前生效的颜色覆盖（根节点内联 style） */
export function currentThemeStyle() {
  return themeStyle(getRuntime().theme.colors)
}

/** 当前主题模式（light | dark） */
export function currentThemeMode() {
  return getRuntime().theme.mode === 'dark' ? 'dark' : 'light'
}

/** 读取用户持久化的颜色覆盖（独立应用启动时由适配器读取） */
export function readStoredThemeColors() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}') || {}
  } catch (_) {
    return {}
  }
}

// 预留 API：未来「更换颜色」调用；传 {} 恢复默认
export function setThemeColors(overrides = {}) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(overrides))
  } catch (_) { /* localStorage 不可用时忽略 */ }
  configureEasyAgent({ theme: { colors: overrides } })
  return { ...DEFAULT_COLORS, ...themeStyle(overrides) }
}

/** el 所在的模块根节点（找不到时返回 null） */
export function themeRootOf(el) {
  if (!el || typeof el.closest !== 'function') return null
  return el.closest(`.${THEME_ROOT_CLASS}`)
}

/** el 所在模块根节点是否为暗色主题（不读宿主 html 的 data-theme） */
export function isDarkElement(el) {
  const root = themeRootOf(el)
  return !!root && root.getAttribute('data-theme') === 'dark'
}
