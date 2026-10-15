/**
 * 轻量全局悬浮提示：v-tip 指令。
 *
 * 用法：
 *   v-tip="'发送（Enter）'"            悬停 + 键盘焦点触发
 *   v-tip.hover="'……'"               仅悬停触发（适合 textarea/input，避免打字干扰）
 *
 * 行为与实现要点：
 * - 单例浮层 DOM（role="tooltip"），切换只改文本/位置/类名，无轮询、无重排风暴；
 *   显隐用 opacity + translateY 的 CSS 过渡（0.18s ease-out）。
 * - 定位：优先放目标上方，放不下自动落到下方；水平方向 clamp 在视口内
 *   （8px 边距），窗口滚动/缩放下每次显示前重算，无需常驻监听。
 * - 可访问性：显示时给目标元素设置 aria-describedby 指向浮层 id，
 *   键盘 focus 触发、Esc 关闭；触摸设备（hover:none）改为点击切换。
 * - 隐藏时延迟清空文本，保证淡出动画完整。
 */

const TIP_ID = 'ea-tip-layer'
const STYLE_ID = 'ea-tip-style'

const TIP_STYLE = `
.ea-tip {
  position: fixed;
  z-index: 10000;
  max-width: 280px;
  padding: 7px 11px;
  border: 1px solid var(--border-color, #e2e8f0);
  border-radius: 8px;
  background: var(--bg-secondary, #ffffff);
  color: var(--text-primary, #1e293b);
  box-shadow: 0 6px 20px rgba(15, 23, 42, 0.14);
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-line;
  word-break: break-word;
  pointer-events: none;
  opacity: 0;
  transform: translateY(4px);
  transition: opacity 0.18s ease-out, transform 0.18s ease-out;
}
.ea-tip.show {
  opacity: 1;
  transform: translateY(0);
}
@media (prefers-reduced-motion: reduce) {
  .ea-tip { transition: none; }
}
`

let tipLayer = null
let currentEl = null
let currentText = ''
let hideTimer = null
// 触摸设备（hover:none）没有悬停，按钮类元素点击后获得焦点即可触发提示；
// 文本输入类元素（.hover 修饰符）在触摸设备上不弹提示，避免遮挡软键盘输入
const finePointer =
  typeof window !== 'undefined' && window.matchMedia
    ? window.matchMedia('(hover: hover) and (pointer: fine)').matches
    : true

function ensureLayer() {
  if (tipLayer) return tipLayer
  if (!document.getElementById(STYLE_ID)) {
    const style = document.createElement('style')
    style.id = STYLE_ID
    style.textContent = TIP_STYLE
    document.head.appendChild(style)
  }
  tipLayer = document.createElement('div')
  tipLayer.className = 'ea-tip'
  tipLayer.id = TIP_ID
  tipLayer.setAttribute('role', 'tooltip')
  document.body.appendChild(tipLayer)
  return tipLayer
}

function clearHideTimer() {
  if (hideTimer) {
    clearTimeout(hideTimer)
    hideTimer = null
  }
}

function place(el) {
  const tip = ensureLayer()
  const rect = el.getBoundingClientRect()
  const tw = tip.offsetWidth
  const th = tip.offsetHeight
  // 优先在上方；上方放不下落到下方；水平 clamp 在视口内
  let top = rect.top - th - 8
  if (top < 8) top = Math.min(rect.bottom + 8, window.innerHeight - th - 8)
  let left = rect.left + rect.width / 2 - tw / 2
  left = Math.max(8, Math.min(left, window.innerWidth - tw - 8))
  tip.style.top = `${Math.max(8, top)}px`
  tip.style.left = `${left}px`
}

function showTip(el) {
  clearHideTimer()
  const tip = ensureLayer()
  if (currentEl && currentEl !== el) currentEl.removeAttribute('aria-describedby')
  currentEl = el
  currentText = el.__tipText || ''
  if (!currentText) return
  tip.textContent = currentText
  el.setAttribute('aria-describedby', TIP_ID)
  place(el)
  // 下一帧再加 show，确保从隐藏态过渡（淡入 + 上滑）
  requestAnimationFrame(() => tip.classList.add('show'))
  if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
    // 文本框获得焦点后一旦开始输入即收起，避免遮挡输入内容
    const hideOnInput = () => {
      hideTip(el)
      el.removeEventListener('input', hideOnInput)
    }
    el.addEventListener('input', hideOnInput)
  }
}

function hideTip(el) {
  clearHideTimer()
  if (!tipLayer) return
  if (el && currentEl === el) {
    el.removeAttribute('aria-describedby')
    currentEl = null
  }
  tipLayer.classList.remove('show')
  // 淡出动画结束后再清文本
  hideTimer = setTimeout(() => {
    hideTimer = null
    if (!currentEl) tipLayer.textContent = ''
  }, 220)
}

function onMouseEnter(event) {
  // 触摸设备没有悬停：忽略模拟 mouseenter，点击按钮后由 focus 触发
  if (!finePointer) return
  showTip(event.currentTarget)
}

function onFocusEnter(event) {
  // 键盘 Tab / 触摸点击按钮都会产生 focus，不受 hover:none 限制
  showTip(event.currentTarget)
}

function onLeave(event) {
  hideTip(event.currentTarget)
}

function onKeydown(event) {
  if (event.key === 'Escape') hideTip(event.currentTarget)
}

function bindEl(el, text, hoverOnly) {
  el.__tipText = text || ''
  el.addEventListener('mouseenter', onMouseEnter)
  el.addEventListener('mouseleave', onLeave)
  el.addEventListener('keydown', onKeydown)
  if (!hoverOnly) {
    el.addEventListener('focus', onFocusEnter)
    el.addEventListener('blur', onLeave)
  }
}

function unbindEl(el) {
  hideTip(el)
  el.removeEventListener('mouseenter', onMouseEnter)
  el.removeEventListener('mouseleave', onLeave)
  el.removeEventListener('keydown', onKeydown)
  el.removeEventListener('focus', onFocusEnter)
  el.removeEventListener('blur', onLeave)
}

export const tipDirective = {
  mounted(el, binding) {
    bindEl(el, binding.value, binding.modifiers && binding.modifiers.hover)
  },
  updated(el, binding) {
    // 绑定值为动态时（如联网搜索的动态说明）同步文本；显示中则即时刷新
    if (binding.value !== binding.oldValue) {
      el.__tipText = binding.value || ''
      if (currentEl === el && tipLayer) tipLayer.textContent = el.__tipText
      if (currentEl === el) place(el)
    }
  },
  unmounted(el) {
    unbindEl(el)
  },
}

export default tipDirective
