// 全局语义化颜色令牌基础设施。
//
// 默认值即当前浅色主题；所有令牌会以内联 CSS 变量写到 <html>，
// 覆盖 :root 中的默认定义 —— 因此任何使用 var(--token) 的样式都能在
// 运行时被动态改变。后续「更换颜色」功能只需调用 setThemeColors()，
// 无需改动组件。未使用变量的硬编码颜色不在本次范围内。
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

// 应用颜色令牌（允许部分覆盖），返回最终生效的完整集合
export function applyThemeColors(overrides = {}) {
  const colors = { ...DEFAULT_COLORS, ...overrides }
  const root = document.documentElement
  for (const [token, value] of Object.entries(colors)) {
    if (value) root.style.setProperty(token, value)
  }
  return colors
}

function readStored() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}') || {}
  } catch (_) {
    return {}
  }
}

// 启动时调用：读取并应用持久化的自定义颜色
export function loadStoredThemeColors() {
  return applyThemeColors(readStored())
}

// 预留 API：未来「更换颜色」调用；传 {} 恢复默认
export function setThemeColors(overrides = {}) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(overrides))
  } catch (_) { /* localStorage 不可用时忽略 */ }
  return applyThemeColors(overrides)
}
