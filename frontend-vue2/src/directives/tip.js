/**
 * 轻量全局悬浮提示：v-tip 指令（Vue2 版，与 frontend/src/directives/tip.js 行为一致）。
 *
 * 用法：
 *   v-tip="'发送（Enter）'"            悬停 + 键盘焦点触发
 *   v-tip:hover="'……'"               仅悬停触发（适合 textarea/input，避免打字干扰）
 *
 * 实现要点：单例浮层（role="tooltip"）复用 DOM；显隐走 opacity + translateY 的
 * CSS 过渡；定位优先上方、放不下落下方并水平 clamp 视口内；显示时给目标元素
 * 设置 aria-describedby；触摸设备（hover:none）改为点击切换。
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
let hideTimer = null
// 触摸设备（hover:none）没有悬停：按钮类元素点击后获得焦点即可触发提示；
// 文本输入类元素（:hover 修饰符）在触摸设备上不弹提示，避免遮挡软键盘输入
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
  if (!el.__tipText) return
  tip.textContent = el.__tipText
  el.setAttribute('aria-describedby', TIP_ID)
  place(el)
  requestAnimationFrame(() => tip.classList.add('show'))
  if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
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
  hideTimer = setTimeout(() => {
    hideTimer = null
    if (!currentEl) tipLayer.textContent = ''
  }, 220)
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

export default {
  bind(el, binding) {
    bindEl(el, binding.value, binding.modifiers && binding.modifiers.hover)
  },
  update(el, binding) {
    if (binding.value !== binding.oldValue) {
      el.__tipText = binding.value || ''
      if (currentEl === el && tipLayer) {
        tipLayer.textContent = el.__tipText
        place(el)
      }
    }
  },
  unbind(el) {
    unbindEl(el)
  },
}
