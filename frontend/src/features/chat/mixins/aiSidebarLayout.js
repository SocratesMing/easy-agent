import { eventTarget } from '../../../utils/domEvent.js'

/**
 * AI 侧边栏的布局与页面交互（拖动改宽、唤起按钮拖动、划词提问、快捷键、偏好持久化），
 * 从 AiSidebar.vue 拆出；状态字段（width / launcherTop / selectionAsk 等）仍定义在组件 data 中。
 */
export const PREFS_KEY = 'easy_agent_ai_sidebar_prefs'
export const PAGE_SESSIONS_KEY = 'easy_agent_ai_page_sessions'
export const DEFAULT_WIDTH = 440
export const MIN_WIDTH = 340
export const RAIL_WIDTH = 52
// 小于该宽度的视口强制悬浮全宽显示
export const NARROW_VIEWPORT = 768
export const MAX_PAGE_SESSIONS = 20
export const QUOTE_MAX = 32000
export const DEFAULT_FEATURES = {
  launcher: true,
  selectionAsk: true,
  shortcut: true,
  dock: true,
  openInMain: true,
}

export function readJson(storage, key, fallback) {
  try {
    const raw = storage.getItem(key)
    return raw ? JSON.parse(raw) : fallback
  } catch (_) {
    return fallback
  }
}

export function writeJson(storage, key, value) {
  try {
    storage.setItem(key, JSON.stringify(value))
  } catch (_) { /* 存储不可用时忽略 */ }
}

export default {
  methods: {
    // ── 拖动分隔栏改变宽度 ────────────────────────────────────────────
    startResize(e) {
      if (this.isNarrowViewport) return
      this.resizing = true
      this.resizeStartX = e.clientX
      this.resizeStartWidth = this.effectiveWidth
      document.addEventListener('mousemove', this.onResizeMove)
      document.addEventListener('mouseup', this.stopResize)
    },
    onResizeMove(e) {
      if (!this.resizing) return
      const next = this.resizeStartWidth + (this.resizeStartX - e.clientX)
      this.width = Math.min(Math.max(next, MIN_WIDTH), this.maxWidth)
    },
    stopResize() {
      document.removeEventListener('mousemove', this.onResizeMove)
      document.removeEventListener('mouseup', this.stopResize)
      if (this.resizing) {
        this.resizing = false
        this.savePrefs()
      }
    },
    resetWidth() {
      this.width = DEFAULT_WIDTH
      this.savePrefs()
    },

    // ── 唤起按钮：上下拖动 ────────────────────────────────────────────
    startLauncherDrag(e) {
      if (e.button !== 0) return
      this.launcherMoved = false
      this.dragStartY = e.clientY
      this.dragStartTop = this.launcherTop
      document.addEventListener('mousemove', this.onLauncherMove)
      document.addEventListener('mouseup', this.stopLauncherDrag)
    },
    onLauncherMove(e) {
      const dy = e.clientY - this.dragStartY
      if (!this.launcherMoved && Math.abs(dy) < 4) return
      this.launcherMoved = true
      this.launcherDragging = true
      this.launcherTop = this.dragStartTop + dy
      this.clampLauncher()
    },
    stopLauncherDrag() {
      document.removeEventListener('mousemove', this.onLauncherMove)
      document.removeEventListener('mouseup', this.stopLauncherDrag)
      if (this.launcherDragging) {
        this.launcherDragging = false
        this.savePrefs()
      }
    },
    onLauncherClick() {
      // 拖动结束时浏览器仍会派发 click：拖动过则不当作点击
      if (this.launcherMoved) {
        this.launcherMoved = false
        return
      }
      this.openSidebar()
    },
    clampLauncher() {
      const max = window.innerHeight - 56
      this.launcherTop = Math.min(Math.max(this.launcherTop, 12), Math.max(12, max))
    },

    // ── 划词提问 ──────────────────────────────────────────────────────
    onDocumentMouseDown(e) {
      const target = eventTarget(e)
      if (this.selectionAsk.visible && !(target && target.closest && target.closest('.ais-selection-ask'))) {
        this.hideSelectionAsk()
      }
    },
    onDocumentMouseUp(e) {
      if (!this.feat.selectionAsk) return
      const target = eventTarget(e)
      if (!target || !target.closest) return
      if (target.closest('.ais-panel, .ais-launcher, .ais-selection-ask, input, textarea, [contenteditable="true"]')) return
      // 等浏览器完成选区更新后再读取
      setTimeout(() => {
        const sel = window.getSelection()
        const text = sel ? sel.toString().trim() : ''
        if (!sel || sel.rangeCount === 0 || text.length < 2) return
        const rect = sel.getRangeAt(0).getBoundingClientRect()
        if (!rect || (!rect.width && !rect.height)) return
        this.selectionAsk = {
          visible: true,
          x: Math.min(rect.right + 6, window.innerWidth - 90),
          y: Math.min(rect.bottom + 6, window.innerHeight - 40),
          text: text.slice(0, QUOTE_MAX),
        }
      }, 0)
    },
    hideSelectionAsk() {
      if (this.selectionAsk.visible) this.selectionAsk = { visible: false, x: 0, y: 0, text: '' }
    },
    askWithSelection() {
      const text = this.selectionAsk.text
      this.hideSelectionAsk()
      if (window.getSelection) window.getSelection().removeAllRanges()
      this.openSidebar()
      this.quote = text
    },

    // ── 全局事件 ──────────────────────────────────────────────────────
    onGlobalKeydown(e) {
      if (!this.feat.shortcut) return
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && (e.key === 'j' || e.key === 'J')) {
        e.preventDefault()
        if (this.open && this.collapsed) this.expand()
        else this.toggle()
      }
    },
    onWindowResize() {
      this.viewportWidth = window.innerWidth
      this.clampLauncher()
    },
    savePrefs() {
      writeJson(localStorage, PREFS_KEY, {
        mode: this.mode,
        width: Math.round(this.width),
        launcherTop: Math.round(this.launcherTop),
      })
    },
  },
}
