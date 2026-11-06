// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/markdown/sanitize.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
// HTML 消毒：markdown 渲染产物、第三方预览库生成的 HTML 进入 v-html / DOM 前的最后一道关。
//
// 模型回复、知识库文档、联网搜索结果、上传文件都可能被提示注入，而 marked 不做任何过滤：
// 原始 HTML（<img onerror>、<script>）与 javascript: 链接会原样进入 v-html，在本站上下文里
// 执行脚本，读走 localStorage 里的登录 token。这里用 DOMPurify 按白名单清洗。
//
// 约定：
// - 用独立的 DOMPurify 实例（首次调用时按当前 window 创建）：hook 与配置不影响宿主自己的 DOMPurify；
// - 没有 DOM 的环境（Node 单测、构建脚本）原样返回——那里没有注入点；有 DOM 但 DOMPurify
//   不可用时整段转义，宁可显示源码也不放行。
import DOMPurify from 'dompurify'

/** 代码块复制按钮的内联处理函数：渲染器写死这个值，消毒时按「标签 + 类名 + 完整取值」精确放行 */
export const CODE_COPY_ONCLICK = 'copyCode(this)'

const PURIFY_CONFIG = {
  // 外链在新标签页打开：DOMPurify 默认剥掉 target，这里放行，再由 hook 统一补 noopener
  ADD_ATTR: ['target'],
  // <style> 能改整页样式（盖住界面、伪造登录框），<form> 能把输入提交到任意地址；正文渲染用不到
  FORBID_TAGS: ['style', 'form'],
  // KaTeX 的 MathML 里 <annotation> 存的是 TeX 源码，DOMPurify 默认会拆掉标签却留下文字，
  // 读屏会把公式再念一遍源码：连内容一起去掉（公式的可视部分与 MathML 结构不受影响）
  ADD_FORBID_CONTENTS: ['annotation'],
}

const SAFE_LINK_PROTOCOLS = new Set(['http:', 'https:', 'mailto:', 'tel:'])

const purifiers = new WeakMap()

function keepCodeCopyHandler(node, data) {
  if (
    data.attrName === 'onclick' &&
    data.attrValue === CODE_COPY_ONCLICK &&
    node.nodeName === 'BUTTON' &&
    node.classList.contains('code-copy-btn')
  ) {
    // 模型伪造同样的按钮也只能触发复制，拿不到任意脚本执行；其余 on* 属性照常剥掉
    data.forceKeepAttr = true
  }
}

function forceSafeTarget(node) {
  if (!node.hasAttribute || !node.hasAttribute('target')) return
  // 任何 target 都改成新标签页并断开 opener，防止被打开的页面反过来改写本页地址
  node.setAttribute('target', '_blank')
  node.setAttribute('rel', 'noopener noreferrer')
}

function purifierFor(win) {
  if (!win || !win.document) return null
  let purifier = purifiers.get(win)
  if (!purifier) {
    purifier = DOMPurify(win)
    if (purifier.isSupported) {
      purifier.setConfig(PURIFY_CONFIG)
      purifier.addHook('uponSanitizeAttribute', keepCodeCopyHandler)
      purifier.addHook('afterSanitizeAttributes', forceSafeTarget)
    }
    purifiers.set(win, purifier)
  }
  return purifier
}

function escapeAll(text) {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
}

/**
 * 清洗一段准备交给 v-html 的 HTML：去掉脚本、事件属性、危险协议链接、<style> / <form>，
 * 保留渲染器产出的结构（代码块外壳与复制按钮、来源卡片、知识依据锚点、KaTeX、emoji）。
 *
 * @param {string} html 待清洗的 HTML
 * @returns {string} 可以安全交给 v-html 的 HTML
 * @example
 * sanitizeHtml('<img src=x onerror="alert(1)">') // '<img src="x">'
 */
export function sanitizeHtml(html) {
  if (!html) return ''
  const purifier = purifierFor(typeof window === 'undefined' ? null : window)
  if (!purifier) return String(html)
  if (!purifier.isSupported) return escapeAll(html)
  return purifier.sanitize(String(html))
}

/**
 * 链接地址是否可以保留：只认 http(s) / mailto / tel 与站内相对地址，javascript: / data: 等一律不要。
 *
 * @param {string} href 链接地址（getAttribute 读到的原始值）
 * @returns {boolean} 是否安全
 * @example
 * isSafeLinkUrl('java\tscript:alert(1)') // false（URL 解析会去掉制表符）
 */
export function isSafeLinkUrl(href) {
  const value = String(href == null ? '' : href).trim()
  if (!value) return true
  try {
    // 相对地址按 https 页面解析：只判断协议，不关心具体站点
    return SAFE_LINK_PROTOCOLS.has(new URL(value, 'https://easy-agent.invalid/').protocol)
  } catch (e) {
    return false
  }
}

/**
 * 第三方预览库（docx-preview 等）直接生成 DOM、不经过 sanitizeHtml：渲染完成后去掉其中
 * 不安全链接的 href（链接文字保留），避免点一下就执行脚本。
 *
 * @param {Element|null} root 渲染容器
 * @returns {number} 被去掉 href 的链接数
 */
export function neutralizeUnsafeLinks(root) {
  if (!root || typeof root.querySelectorAll !== 'function') return 0
  let count = 0
  for (const link of root.querySelectorAll('a[href], area[href]')) {
    if (isSafeLinkUrl(link.getAttribute('href'))) continue
    link.removeAttribute('href')
    count++
  }
  return count
}
