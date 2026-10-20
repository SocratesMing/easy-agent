// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/markdown/index.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
// ─────────────────────────────────────────────────────────────
// markdown 渲染内核（唯一实现）：两份前端共用同一份源码。
//   源文件：frontend-shared/markdown/index.js
//   同步：npm run sync:shared（copies 到 <app>/src/shared/markdown/index.js）
//   约束：src/utils/structure.test.js 只允许此文件 import marked
//
// 版本约定：marked 锁定 17.x（Renderer 回调为 token 对象签名）。
// ─────────────────────────────────────────────────────────────
// 共享的 marked 扩展注册与渲染器工厂：数学公式(KaTeX) + GitHub 风格 emoji 短代码(:smile: 等)
// 通过 initialized 标志保证全局 marked 实例上的扩展只注册一次，
// 避免 ChatMessage / FilePreview 等多个组件重复注册导致扩展冲突。
//
// 版本约定：marked 锁定 17.x（Renderer 回调为 token 对象签名）。
// 2026-10-15 由 4.3.0 升级，原因：4.3.0 的强调定界符匹配有缺陷——当 **...** 的内容以 ASCII 标点
// 结尾（"、]、) 等）、且结尾 ** 后紧跟全角标点（：、（、、等）时，加粗不会被解析。
// 实例：会话 xzz/6d3ae63e 的「**放量破位、呈"三连阴"**：10/13 …」渲染为字面量 **。
// 注意 marked 5.x 起已移除 headerIds / mangle 选项，标题不再生成 id（与旧版关闭时行为一致）。
import { marked } from 'marked'
import katexExtension from 'marked-katex-extension'
import { markedEmoji } from 'marked-emoji'
import { nameToEmoji } from 'gemoji'

let initialized = false

// 将模型可能“内联”写出的块级公式 $$...$$ 规整到独立行，
// 否则 marked-katex-extension 的行内规则要求 $$ 前后有空格、块级规则要求 $$ 位于行首，
// 模型把公式嵌在段落中间（如 `text$$x$$text`）时不会被识别，最终以纯文本显示。
// 处理前先保护代码块/行内代码，避免把代码里的 $ 误当公式。
export function normalizeMathDelimiters(content) {
  if (typeof content !== 'string') return content
  const protectedChunks = []
  const placeholder = (chunk) => {
    protectedChunks.push(chunk)
    return ' CODE' + (protectedChunks.length - 1) + ' '
  }
  // 末位分支 (?:```|$) 用于保护「流式输出中尚未闭合」的代码块：
  // 此时没有结尾 ```，若只匹配成对围栏，代码里的 $ 会被后面的公式规整当成数学公式改写，
  // 导致流式过程中代码被 KaTeX 渲染成公式（样式错乱），流结束后才恢复正常。
  let s = content
    .replace(/```[\s\S]*?(?:```|$)/g, placeholder)
    .replace(/`[^`\n]+`/g, placeholder)
  s = s.replace(/\$\$([\s\S]+?)\$\$/g, (_, inner) => '\n\n$$\n' + inner.trim() + '\n$$\n\n')
  s = s.replace(/ CODE(\d+) /g, (_, i) => protectedChunks[+i])
  return s
}

// ---------------------------------------------------------------------------
// 来源卡片：把 AI 回复里「标题 + 链接」独立成行的来源美化成可点击卡片。
// 诉求：不展示裸 URL，直接显示标题，点击标题跳转，卡片化外观。
//   同花顺涨停复盘 http://xxx.shtml
//   东方财富收评：https://xxx.html
//   [东方财富收评](https://xxx.html)
//   https://xxx.html（无标题时以域名作为卡片标题）
// 完整地址放在卡片 title 属性里，悬停可查看但界面不展示。
// ---------------------------------------------------------------------------

const SOURCE_CARD_ICON =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"' +
  ' stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
  '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path>' +
  '<polyline points="15 3 21 3 21 9"></polyline>' +
  '<line x1="10" y1="14" x2="21" y2="3"></line>' +
  '</svg>'

/** 纯链接行无标题时的兜底：展示可读域名 */
function getSourceCardDomain(url) {
  try {
    return new URL(url).hostname.replace(/^www\./i, '')
  } catch (e) {
    return url
  }
}

function buildSourceCardHtml(title, url) {
  return (
    '<a class="msg-source-card" href="' + escapeHtml(url) + '"' +
    ' target="_blank" rel="noopener noreferrer"' +
    ' title="' + escapeHtml(url) + '">' +
    SOURCE_CARD_ICON +
    '<span class="msg-source-card-title">' + escapeHtml(title) + '</span>' +
    '</a>'
  )
}

// 裸 URL 的字符集：只认 ASCII 里合法的 URL 字符，不用 \S+。
// 模型的中文回复常把 URL 紧贴在中文/全角标点里（「…/a.html。这是下一句」），
// \S+ 会把后面整句中文一起吞进 href，正文内容随之从界面上消失。
const URL_BODY = "https?:\\/\\/[A-Za-z0-9\\-._~:\\/?#\\[\\]@!$&'()*+,;=%]+"
const URL_RE_G = new RegExp(URL_BODY, 'g')

// URL 行尾的收尾标点：不属于链接本身，原样留在卡片/链接之后
const TRAIL_PUNCT = "[。，、；：！？…,.;:!?'\"）】》」』]*"
const TITLE_URL_RE = new RegExp(
  '^(.+?)\\s*[:：，,]?\\s*(' + URL_BODY + ')\\s*(' + TRAIL_PUNCT + ')\\s*$'
)
const URL_ONLY_RE = new RegExp('^(' + URL_BODY + ')\\s*(' + TRAIL_PUNCT + ')\\s*$')
const MD_LINK_LINE_RE = new RegExp(
  '^\\[([^\\]\\n]+)\\]\\((https?:\\/\\/[^\\s)]+)\\)\\s*(' + TRAIL_PUNCT + ')\\s*$'
)

/**
 * 拆分 URL 与其后紧跟的收尾标点。
 * 成对的右括号属于 URL 本身（如维基百科 Foo_(bar)），不成对的算收尾。
 */
function splitUrlTail(rawUrl) {
  let url = String(rawUrl || '')
  let tail = ''
  // * 与 ~ 既是 URL 允许字符，也是 markdown 强调/删除线定界符（**url**、~~url~~）
  const m = url.match(/[。，、；：！？…,.;:!?'"）】》」』*~]+$/)
  if (m) {
    tail = m[0]
    url = url.slice(0, -tail.length)
  }
  while (url.endsWith(')')) {
    const open = (url.match(/\(/g) || []).length
    const close = (url.match(/\)/g) || []).length
    if (close <= open) break
    tail = ')' + tail
    url = url.slice(0, -1)
  }
  return { url, tail }
}

/** 流式输出中未闭合的 markdown 链接/方括号，不当作来源行改写 */
function hasUnclosedMarkdown(line) {
  if (line.indexOf('](') !== -1) return true
  const opens = (line.match(/\[/g) || []).length
  const closes = (line.match(/\]/g) || []).length
  return opens > closes
}

/**
 * 行内裸 URL 规整：把紧邻中文/全角标点的裸 URL 收口成 <url> 自动链接。
 * 否则 marked 的 GFM 自动链接会把 URL 后面的中文一起算进链接，
 * 例如「https://a.com/x。这是下一句」会生成带 %E3%80%82 的坏 href。
 * 已存在于 markdown 链接/HTML 标签/行内代码里的 URL 不动。
 */
function normalizeInlineUrls(line) {
  if (!line || line.indexOf('http') === -1) return line
  // 来源卡片是已经生成好的 HTML（标题可能就是 URL），不能再包一层链接
  if (line.indexOf('msg-source-card') !== -1) return line
  const held = []
  const hold = (chunk) => {
    held.push(chunk)
    return '\u0000' + (held.length - 1) + '\u0000'
  }
  // 保护已有 markdown 链接/图片、HTML 标签（来源卡片就是 HTML）与 <url> 自动链接
  const s = line
    .replace(/!?\[[^\]\n]*\]\([^)\s]*\)/g, hold)
    .replace(/<[^>]*>/g, hold)

  const converted = s
    .split(/(`[^`\n]*`)/)
    .map((part, i) => {
      if (i % 2 === 1) return part // 行内代码原样保留
      return part.replace(URL_RE_G, (raw, offset, whole) => {
        // 流式输出中尚未闭合的 markdown 链接目标，别再套一层
        if (/\]\($/.test(whole.slice(0, offset))) return raw
        const { url, tail } = splitUrlTail(raw)
        return url ? '<' + url + '>' + tail : raw
      })
    })
    .join('')
  return converted.replace(/\u0000(\d+)\u0000/g, (_, i) => held[+i])
}

// 单行改写：命中来源链接模式则生成卡片 HTML（保留列表/标题前缀），否则原样返回
function beautifySourceLine(raw) {
  const lead = raw.match(/^(\s*(?:[-*+]|\d+[.)])?\s*#{0,6}\s*)/)
  const prefix = lead ? lead[1] : ''
  const line = raw.slice(prefix.length)
  if (!line) return raw

  // 1. markdown 链接独立成行：[标题](url)
  let m = line.match(MD_LINK_LINE_RE)
  if (m) {
    return prefix + buildSourceCardHtml(m[1].trim(), m[2]) + (m[3] || '')
  }

  // 2. 标题 + 裸链接：「东方财富收评：url」「凤凰财经/中国证券报 url」
  m = line.match(TITLE_URL_RE)
  if (m) {
    // 流式期间未闭合的 markdown 链接不当作来源行，避免卡片标题里露出 [ ] 字面量
    if (hasUnclosedMarkdown(m[1])) return raw
    let title = m[1].trim().replace(/[*_~`]+/g, '').replace(/[（(]+$/, '').trim()
    // 结论句里顺带提到链接（标题里还有其他 URL）时不算来源行，保持普通渲染
    if (!title || /https?:\/\//i.test(title) || title.length > 60) return raw
    const { url, tail } = splitUrlTail(m[2])
    if (!url) return raw
    return prefix + buildSourceCardHtml(title, url) + (m[3] || '') + tail
  }

  // 3. 纯链接行：没有可展示的标题，用域名替代
  m = line.match(URL_ONLY_RE)
  if (m) {
    const { url, tail } = splitUrlTail(m[1])
    if (!url) return raw
    return prefix + buildSourceCardHtml(getSourceCardDomain(url), url) + (m[2] || '') + tail
  }

  return raw
}

/** 判断该行是不是来源卡片（无列表前缀的独立卡片行才参与横向合并） */
function isCardLine(line) {
  return /^<a class="msg-source-card"[^]*<\/a>[。，、；：！？…,.;:!?'"）】》」』]*$/.test(line)
}

/** 连续卡片行包进 flex 容器：横向排列、装不下自动换行 */
function wrapSourceCards(cardLines) {
  return '<span class="msg-source-cards">' + cardLines.join(' ') + '</span>'
}

/**
 * 来源链接美化：把 AI 回复里的「标题 + 链接」行改写成来源卡片。
 * 连续的卡片行合并成一个 flex 容器（从左到右排列，超出自动换行）。
 * 代码块（含流式期间未闭合的围栏）整体保护，其中的 URL 不改写。
 *
 * @param {string} content markdown 文本
 * @returns {string} 替换后的 markdown 文本
 * @example
 * beautifySourceLinks('东方财富收评 https://finance.eastmoney.com/a/1.html')
 */
export function beautifySourceLinks(content) {
  if (typeof content !== 'string' || !content) return content
  const parts = content.split(/(```[\s\S]*?(?:```|$))/)
  let out = ''
  for (let i = 0; i < parts.length; i++) {
    if (i % 2 === 1) {
      // 奇数段是代码块本体，原样保留
      out += parts[i]
      continue
    }
    const lines = parts[i]
      .split('\n')
      .map(beautifySourceLine)
      .map(normalizeInlineUrls)
    const grouped = []
    let pendingCards = null
    for (const line of lines) {
      if (isCardLine(line)) {
        if (pendingCards) pendingCards.push(line)
        else pendingCards = [line]
        continue
      }
      if (pendingCards) {
        grouped.push(wrapSourceCards(pendingCards))
        pendingCards = null
      }
      grouped.push(line)
    }
    if (pendingCards) grouped.push(wrapSourceCards(pendingCards))
    out += grouped.join('\n')
  }
  return out
}

export function setupMarkedExtensions() {
  if (initialized) return
  initialized = true

  // 数学公式：$$...$$ 块级、 $...$ 行内（标准模式，避免把普通美元金额误判为公式）
  marked.use(
    katexExtension({
      throwOnError: false,
      nonStandard: false,
      strict: false,
      errorColor: '#cc0000',
    })
  )

  // GitHub 风格 emoji 短代码，例如 :smile: :rocket: :fire: :+1: :tada:
  // gemoji.nameToEmoji 提供 短代码 -> unicode 字符 的完整映射
  marked.use(
    markedEmoji({
      emojis: nameToEmoji,
      renderer(token) {
        return (
          '<span class="github-emoji" role="img" aria-label=":' +
          token.name +
          ':">' +
          token.emoji +
          '</span>'
        )
      },
    })
  )
}

// ---------------------------------------------------------------------------
// 代码块复制：markdown 渲染产物通过 v-html 注入，按钮用 inline onclick 调全局函数。
// 由 ChatMessage / FilePreview 共用，故注册在模块级且幂等，避免重复定义。
// ---------------------------------------------------------------------------

const CODE_COPY_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
  '<rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>' +
  '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>' +
  '</svg>'

/** HTML 转义（用于语言标签等由模型输出拼接进 HTML 的文本） */
export function escapeHtml(text) {
  return String(text == null ? '' : text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
}

/**
 * 复制文本到剪贴板：优先 Clipboard API，非安全上下文降级到 execCommand
 *
 * @param {string} text 待复制文本
 * @returns {Promise<boolean>} 是否复制成功
 * @example
 * copyTextToClipboard('hello')
 */
export function copyTextToClipboard(text) {
  const content = String(text == null ? '' : text)
  if (!content) return Promise.resolve(false)
  return (async () => {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(content)
        return true
      }
    } catch (e) {
      console.warn('Clipboard API 不可用，降级复制:', e)
    }
    try {
      const textarea = document.createElement('textarea')
      textarea.value = content
      textarea.setAttribute('readonly', '')
      textarea.style.position = 'fixed'
      textarea.style.top = '-9999px'
      document.body.appendChild(textarea)
      textarea.select()
      textarea.setSelectionRange(0, content.length)
      const ok = document.execCommand('copy')
      document.body.removeChild(textarea)
      return ok
    } catch (e) {
      console.error('复制失败:', e)
      return false
    }
  })()
}

/** 注册全局 window.copyCode（代码块复制按钮用），幂等 */
export function installCodeCopyHandler() {
  if (typeof window === 'undefined' || window.copyCode) return
  window.copyCode = async function (btn) {
    const wrapper = btn.closest('.code-block-wrapper')
    if (!wrapper) return
    const codeEl = wrapper.querySelector('pre code') || wrapper.querySelector('pre')
    const code = codeEl ? codeEl.textContent || '' : ''

    const span = btn.querySelector('span')
    const originalText = span ? span.textContent : ''
    const ok = await copyTextToClipboard(code)
    if (span) {
      span.textContent = ok ? '已复制!' : '复制失败'
      btn.classList.add(ok ? 'copied' : 'copy-error')
      setTimeout(() => {
        span.textContent = originalText
        btn.classList.remove('copied', 'copy-error')
      }, 2000)
    }
  }
}

/**
 * 创建统一的 markdown 渲染器（ChatMessage / FilePreview 共用同一套代码块结构）
 *
 * marked 17.x 的 Renderer 回调为 token 对象签名：
 *   code({ text, lang, escaped }) / link({ href, title, tokens })
 * link 的可见文本需自行用 this.parser.parseInline(tokens) 渲染（覆盖后 marked 不再代劳）。
 *
 * @param {Object} options 配置
 * @param {(code: string, lang: string) => string} options.highlight 高亮函数，返回完整代码块 HTML（含 <pre>）
 * @param {boolean} [options.externalLinks=true] 是否为外链添加 target="_blank" rel="noopener noreferrer"
 * @returns {Object} marked Renderer 实例
 * @example
 * const renderer = createMarkdownRenderer({ highlight: highlightCode })
 */
export function createMarkdownRenderer({ highlight, externalLinks = true }) {
  const renderer = new marked.Renderer()

  renderer.code = function ({ text, lang }) {
    const code = text || ''
    const rawLang = (lang || '').trim().split(/\s+/)[0]
    const langLabel = escapeHtml(rawLang || 'text')
    const highlighted = typeof highlight === 'function' ? highlight(code, rawLang) : ''
    return (
      '<div class="code-block-wrapper">' +
      '<div class="code-header">' +
      `<span class="code-lang">${langLabel}</span>` +
      `<button class="code-copy-btn" onclick="copyCode(this)">${CODE_COPY_SVG}<span>复制</span></button>` +
      '</div>' +
      highlighted +
      '</div>'
    )
  }

  if (externalLinks) {
    renderer.link = function ({ href, title, tokens }) {
      const url = href || ''
      const text = this.parser.parseInline(tokens)
      const isExternal = /^https?:\/\//i.test(url)
      const titleAttr = title ? ` title="${escapeHtml(title)}"` : ''
      const extra = isExternal ? ' target="_blank" rel="noopener noreferrer"' : ''
      return `<a href="${escapeHtml(url)}"${titleAttr}${extra}>${text}</a>`
    }
  }

  return renderer
}

/**
 * 统一渲染入口：规整数学公式后按 gfm + 换行策略渲染
 *
 * @param {string} content markdown 文本
 * @param {Object} renderer createMarkdownRenderer 创建的渲染器
 * @returns {string} HTML 字符串
 * @example
 * renderMarkdown('**hi**', renderer)
 */
export function renderMarkdown(content, renderer) {
  if (!content) return ''
  try {
    return marked.parse(beautifySourceLinks(normalizeMathDelimiters(content)), {
      renderer,
      breaks: true,
      gfm: true,
      // 显式同步：KaTeX / emoji 扩展均为同步扩展，结果直接是 HTML 字符串
      async: false,
    })
  } catch (e) {
    console.error('Markdown 渲染失败:', e)
    return escapeHtml(content)
  }
}
