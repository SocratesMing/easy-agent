/**
 * 可视化代码块（Vue 2 / Vue 3 共用的纯函数）：```echarts / ```mermaid / ```widget。
 *
 * markdown 渲染器遇到这三种代码块时，不直接输出普通代码块，而是包一层占位元素：
 *   <div class="ea-visual" data-visual-kind="echarts"> 普通代码块 </div>
 * MarkdownBody 渲染完后由 ./mount.js 把占位元素挂成沙箱 iframe 卡片，并隐藏里面的代码块
 * （「源码」按钮可再展开）。没有挂载的场景（文件预览等）样式不生效，照常看到代码块。
 *
 * 流式输出中、代码块还没闭合时输出固定的骨架，不露出半截 JSON；骨架 HTML 每帧都一样，
 * 块级 DOM 复用（blockDom.js）不会反复重建。闭合后才换成占位元素、只挂载一次。
 */

const KINDS = {
  echarts: 'echarts',
  mermaid: 'mermaid',
  widget: 'widget',
}

const LABELS = {
  echarts: '图表',
  mermaid: '流程图',
  widget: '交互组件',
}

/** 框架页地址（相对 API 前缀）：由后端 api/viz.py 托管 */
export const VISUAL_FRAME_PATH = '/agent/viz/frame.html'

/** 代码块语言 → 可视化类型；不是可视化代码块时返回 null */
export function visualKindOfLang(lang) {
  const key = String(lang || '').trim().split(/\s+/)[0].toLowerCase()
  return Object.prototype.hasOwnProperty.call(KINDS, key) ? KINDS[key] : null
}

/** 卡片标题用的中文名 */
export function visualLabel(kind) {
  return LABELS[kind] || '图表'
}

/**
 * 围栏代码块是否已经闭合：marked 对未闭合的围栏也会产出 code token（原文到输入末尾），
 * 流式中间帧靠这个区分「还在写」和「已写完」。
 */
export function isFenceClosed(raw) {
  const lines = String(raw || '').replace(/\n+$/, '').split('\n')
  if (lines.length < 2) return false
  const open = /^ {0,3}(`{3,}|~{3,})/.exec(lines[0])
  const close = /^ {0,3}(`{3,}|~{3,})[ \t]*$/.exec(lines[lines.length - 1])
  return !!(open && close && close[1][0] === open[1][0] && close[1].length >= open[1].length)
}

/** 代码块还在流式输出时的骨架（固定 HTML，逐帧不变） */
export function visualPendingHtml(kind) {
  return (
    `<div class="ea-visual is-pending" data-visual-kind="${kind}">` +
    `<div class="ea-visual-skeleton">${visualLabel(kind)}生成中…</div>` +
    '</div>'
  )
}

/** 已闭合的可视化代码块：普通代码块外面包一层占位元素，等 mount.js 挂载 */
export function visualPlaceholderHtml(kind, codeBlockHtml) {
  return `<div class="ea-visual" data-visual-kind="${kind}">${codeBlockHtml}</div>`
}
