// ─────────────────────────────────────────────────────────────
// markdown 渲染内核（唯一实现）：两份前端共用同一份源码。
//   源文件：frontend-shared/markdown/index.js
//   同步：npm run sync:shared（copies 到 <app>/src/shared/markdown/index.js）
//   约束：src/utils/structure.test.js 只允许此文件 import marked
//   安全：renderMarkdown 的产物统一经 ./sanitize.js（DOMPurify）清洗后才交给 v-html
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
// 2026-11-06 中文排版两处修正（见 setupMarkedExtensions）：删除线只认 ~~ 双波浪线；强调与删除线的
// 定界符按 CJK 友好规则识别（./cjkFriendly.js 依赖 marked 17 的内部规则名，升级 marked 时须回归单测）。
// 2026-11-06 剩余问题修复：来源卡片 / 知识依据改用占位标记 + marked 扩展（不再往源文本注入
// HTML），因此 renderer.html 可以默认转义模型写的原始 HTML；预处理统一经
// ./codeRegions.js 保护代码；流式渲染走 ./streamingTail.js 与 renderMarkdownBlocks()。
import { marked } from 'marked'
import katexExtension from 'marked-katex-extension'
import { nameToEmoji } from 'gemoji'
import markedCjkFriendly from './cjkFriendly.js'
import { createInlineMathExtension } from './math.js'
import { CODE_COPY_ONCLICK, sanitizeHtml } from './sanitize.js'
import { CODE_HOLDER_PREFIX, protectCodeRegions } from './codeRegions.js'
import { MARKER_PREFIX, parseMarker, sourceCardMarker } from './markers.js'
import { bufferStreamingTail } from './streamingTail.js'

// 预览组件（Excel 兜底表格、docx 链接）经 markdownSetup 同一出口拿消毒函数
export { isSafeLinkUrl, neutralizeUnsafeLinks, sanitizeHtml } from './sanitize.js'
export { citationMarker, sourceCardMarker } from './markers.js'
export { protectCodeRegions } from './codeRegions.js'
export { bufferStreamingTail } from './streamingTail.js'

let initialized = false

// 将模型可能“内联”写出的块级公式 $$...$$ 与 \[...\] 规整到独立行，
// 否则 marked-katex-extension 的块级规则要求 $$ 位于行首（\[ \] 更是完全不认），
// 模型把公式嵌在段落中间（如 `text$$x$$text`）时不会被识别，最终以纯文本显示。
// 代码区域（``` / ~~~ 围栏、缩进代码块、多反引号行内代码、流式未闭合围栏）整体保护。
export function normalizeMathDelimiters(content) {
  if (typeof content !== 'string') return content
  return protectCodeRegions(content, (s) => {
    let out = s.replace(/\$\$([\s\S]+?)\$\$/g, (_, inner) => '\n\n$$\n' + inner.trim() + '\n$$\n\n')
    // \[ … \] 同样规整成 $$ 块。前面是反斜杠的不动（LaTeX 换行加间距 \\[2pt]、转义的反斜杠），
    // 内容只有数字的也不动（\[1\] 多半是转义的方括号）
    out = out.replace(/(^|[^\\])\\\[([\s\S]+?)\\\]/g, (all, lead, inner) =>
      /^\s*\d*\s*$/.test(inner) ? all : lead + '\n\n$$\n' + inner.trim() + '\n$$\n\n'
    )
    return out
  })
}

// ---------------------------------------------------------------------------
// 来源卡片：把 AI 回复里「标题 + 链接」独立成行的来源美化成可点击卡片。
// 诉求：不展示裸 URL，直接显示标题，点击标题跳转，卡片化外观。
//   同花顺涨停复盘 http://xxx.shtml
//   东方财富收评：https://xxx.html
//   [东方财富收评](https://xxx.html)
//   https://xxx.html（无标题时以域名作为卡片标题）
// 完整地址放在卡片 title 属性里，悬停可查看但界面不展示。
//
// 2026-11-06：只往源文本放占位标记（./markers.js），HTML 由 marked 扩展生成；
// 同时收紧判定，命令行 / 表格行 / 完整句子 / 含反引号的行一律不改写。
// ---------------------------------------------------------------------------

const SOURCE_CARD_ICON =
  // width/height 属性给出固有尺寸：所在容器没有卡片样式时也不会被撑成整块宽度的大图标
  '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"' +
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

/** 知识依据锚点：数据全部来自标记，display 由渲染器转义 */
function buildCitationHtml(display, compact) {
  const d = escapeHtml(display)
  if (compact) {
    return '<button type="button" class="ke-cite ke-cite-number" data-cite="' + d +
      '" aria-label="查看知识依据 ' + d + '">' + d + '</button>'
  }
  return '<a class="ke-cite" data-cite="' + d + '">[知识依据' + d + ']</a>'
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

// 命令行里带 URL 是极常见的（git clone / curl / npm install），不能整行变卡片
const COMMAND_RE = /(?:^|[\s（(：:])(?:git|curl|wget|pip3?|npm|npx|yarn|pnpm|docker|kubectl|sudo|apt|apt-get|yum|dnf|brew|composer|mvn|gradle|make|ssh|scp|rsync|tar|cd|ls|cat|echo|export|python3?|node)(?:\s|['"`])/i

// 「标题」明显是完整句子时不是来源行：含句号/逗号，或带指引性动词的中文长串。
// 注意不误伤「凤凰财经/中国证券报」「东方财富收评」这类纯名词的来源名。
const SENTENCE_PUNCT = /[。！？；，、]/
const SENTENCE_VERB = /(?:参考|参阅|详见|参见|查看|请看|安装|使用|运行|执行|下载|访问|了解|解释|说明|介绍|获取|克隆|点击|打开|输入|复制|如何|为什么|可以|建议)/

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

/** 标题是否是一句完整的话（而非来源名） */
function looksLikeSentence(title) {
  if (SENTENCE_PUNCT.test(title)) return true
  const han = (title.match(/[\u4e00-\u9fff]/g) || []).length
  return han >= 4 && SENTENCE_VERB.test(title)
}

/**
 * 行内裸 URL 规整：把紧邻中文/全角标点的裸 URL 收口成 <url> 自动链接。
 * 否则 marked 的 GFM 自动链接会把 URL 后面的中文一起算进链接，
 * 例如「https://a.com/x。这是下一句」会生成带 %E3%80%82 的坏 href。
 * 已存在于 markdown 链接/HTML 标签/行内代码里的 URL 不动。
 */
function normalizeInlineUrls(line) {
  if (!line || line.indexOf('http') === -1) return line
  // 代码占位符里的地址已经处理过，不能再包一层链接。
  // 注意不能因为「行里有知识依据标记」就整行跳过：同一行里的裸 URL 仍需收口，
  // 否则 marked 的 GFM 自动链接会把 URL 后面的中文一起吞进 href。
  if (line.indexOf(CODE_HOLDER_PREFIX) !== -1) return line
  const held = []
  const hold = (chunk) => {
    held.push(chunk)
    return '\u0000' + (held.length - 1) + '\u0000'
  }
  // 保护已有 markdown 链接/图片、HTML 标签与 <url> 自动链接
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

const EXTERNAL_LINK_ATTRS = ' target="_blank" rel="noopener noreferrer"'
const URL_ONLY_TEXT_RE = new RegExp('^' + URL_BODY + '$')

/**
 * 纯文本里的 http(s) 链接转成新标签页打开的 <a>，其余内容转义（用于工具结果等非 markdown 文本）。
 *
 * @param {string} text 纯文本
 * @returns {string} 可直接 v-html 的 HTML
 * @example
 * linkifyText('详见 https://example.com/a。') // 详见 <a href="https://example.com/a" ...>…</a>。
 */
export function linkifyText(text) {
  const source = String(text == null ? '' : text)
  let html = ''
  let last = 0
  for (const match of source.matchAll(URL_RE_G)) {
    const { url, tail } = splitUrlTail(match[0])
    html += escapeHtml(source.slice(last, match.index))
    html += url
      ? '<a href="' + escapeHtml(url) + '"' + EXTERNAL_LINK_ATTRS + '>' + escapeHtml(url) + '</a>' + escapeHtml(tail)
      : escapeHtml(match[0])
    last = match.index + match[0].length
  }
  return html + escapeHtml(source.slice(last))
}

/**
 * 单行解析：命中来源链接模式则返回卡片数据，否则返回 null。
 * 保留列表 / 标题前缀，改写后前缀仍留在 markdown 源文本里。
 */
function parseSourceLine(raw) {
  const lead = raw.match(/^(\s*(?:[-*+]|\d+[.)])?\s*#{0,6}\s*)/)
  const prefix = lead ? lead[1] : ''
  const line = raw.slice(prefix.length)
  if (!line) return null
  // 表格行（含无首尾竖线的写法）、含反引号的行、命令行都不参与改写
  if (line.indexOf('|') !== -1 || line.indexOf('`') !== -1 || COMMAND_RE.test(line)) return null

  // 1. markdown 链接独立成行：[标题](url)
  let m = line.match(MD_LINK_LINE_RE)
  if (m) return { prefix, title: m[1].trim(), url: m[2], tail: m[3] || '' }

  // 2. 标题 + 裸链接：「东方财富收评：url」「凤凰财经/中国证券报 url」
  m = line.match(TITLE_URL_RE)
  if (m) {
    // 流式期间未闭合的 markdown 链接不当作来源行，避免卡片标题里露出 [ ] 字面量
    if (hasUnclosedMarkdown(m[1])) return null
    const title = m[1].trim().replace(/[*_~`]+/g, '').replace(/[（(]+$/, '').trim()
    // 结论句里顺带提到链接（标题里还有其他 URL）时不算来源行，保持普通渲染
    if (!title || /https?:\/\//i.test(title) || title.length > 60) return null
    if (looksLikeSentence(title)) return null
    const { url, tail } = splitUrlTail(m[2])
    if (!url) return null
    return { prefix, title, url, tail: (m[3] || '') + tail }
  }

  // 3. 纯链接行：没有可展示的标题，用域名替代
  m = line.match(URL_ONLY_RE)
  if (m) {
    const { url, tail } = splitUrlTail(m[1])
    if (!url) return null
    return { prefix, title: getSourceCardDomain(url), url, tail: (m[2] || '') + tail }
  }
  return null
}

/** 连续卡片行包进 flex 容器：横向排列、装不下自动换行 */
function wrapSourceCards(cardHtmlLines) {
  return '<span class="msg-source-cards">' + cardHtmlLines.join(' ') + '</span>'
}

/**
 * 来源链接美化：把 AI 回复里的「标题 + 链接」行换成占位标记。
 * 连续的卡片行合并成一个标记（对应一个 flex 容器）。
 * 代码区域整体保护，其中的 URL / 公式 / `[知识依据N]` 均不改写。
 *
 * @param {string} content markdown 文本
 * @returns {string} 替换后的 markdown 文本
 */
export function beautifySourceLinks(content) {
  if (typeof content !== 'string' || !content) return content
  return protectCodeRegions(content, (text) => {
    const lines = text.split('\n').map((raw) => {
      if (raw.indexOf(CODE_HOLDER_PREFIX) !== -1) return raw
      const parsed = parseSourceLine(raw)
      if (!parsed) return normalizeInlineUrls(raw)
      const marker = sourceCardMarker([{ title: parsed.title, url: parsed.url }])
      if (parsed.prefix) return { line: parsed.prefix + marker + parsed.tail, card: null }
      return { line: marker + parsed.tail, card: { title: parsed.title, url: parsed.url } }
    })

    const grouped = []
    let pending = []
    const flush = () => {
      if (!pending.length) return
      grouped.push(sourceCardMarker(pending))
      pending = []
    }
    for (const entry of lines) {
      if (typeof entry === 'string') {
        flush()
        grouped.push(entry)
      } else if (entry.card) {
        pending.push(entry.card)
      } else {
        flush()
        grouped.push(entry.line)
      }
    }
    flush()
    return grouped.join('\n')
  })
}

// ---------------------------------------------------------------------------
// 原始 HTML：模型回复里写 `<b>` / `<Vec<T>>` / `<script>` 时，marked 会放行标签，
// DOMPurify 再删掉不认识的部分（<script> 连内容一起删），于是正文吞字。
// 只放行一个小白名单，其余 html token 当文本转义输出；文件预览传 rawHtml: 'allow'。
// ---------------------------------------------------------------------------
const RAW_HTML_ALLOW = new Set(['br', 'sub', 'sup', 'kbd', 'details', 'summary'])
const RAW_TAG_RE = /<!--[\s\S]*?-->|<!\[CDATA\[[\s\S]*?\]\]>|<\?[\s\S]*?\?>|<![^>]*>|<\/?([A-Za-z][A-Za-z0-9-]*)(?:\s[^<>]*?)?\/?>/g

function escapeRawHtml(text) {
  return String(text == null ? '' : text).replace(RAW_TAG_RE, (tag, name) => {
    if (name && RAW_HTML_ALLOW.has(name.toLowerCase())) {
      return tag.startsWith('</') ? '</' + name.toLowerCase() + '>' : '<' + name.toLowerCase() + '>'
    }
    return escapeHtml(tag)
  })
}

// ---------------------------------------------------------------------------
// emoji 短代码：gemoji 全量映射会把 `root:x:0:0`、`比例 a:b:c`、`status:ok:200`
// 里的 `:x:` / `:b:` / `:ok:` 替换掉。这里只在前后都是行首/空白/标点时替换，
// 并排除 1~2 个字母的短名与常见英文词。
// ---------------------------------------------------------------------------
const EMOJI_TOKEN_RE = /^:([A-Za-z0-9_+-]+):/
const EMOJI_SEARCH_RE = /:([A-Za-z0-9_+-]+):/
const EMOJI_BOUNDARY = /[\s\u0000-\u001f(（[「『《〈,，。；;:：!！?？、>》」』〉)）]/
const EMOJI_BLOCKLIST = new Set([
  'id', 'ok', 'on', 'cn', 'it', 'new', 'cool', 'vs', 'so', 'up', 'us', 'uk', 'eu',
  'tm', 'cc', 'wc', 'ab', 'cl', 'io', 'ai', 'free', 'soon', 'end', 'top', 'hot',
])

function emojiBoundaryOk(tokens, src, rawLen) {
  const last = tokens && tokens.length ? tokens[tokens.length - 1] : null
  if (last) {
    if (last.type !== 'text') return false
    const ch = (last.raw || last.text || '').slice(-1)
    if (ch && !EMOJI_BOUNDARY.test(ch)) return false
  }
  const next = src.charAt(rawLen)
  if (next && !EMOJI_BOUNDARY.test(next)) return false
  return true
}

function easyAgentMarkerExtension() {
  return {
    name: 'easyAgentMarkers',
    level: 'inline',
    start(src) {
      const i = src.indexOf(MARKER_PREFIX)
      return i >= 0 ? i : undefined
    },
    tokenizer(src) {
      const parsed = parseMarker(src)
      if (!parsed) return
      // marked 按扩展名（而非 token.type）注册 renderer，所有标记共用一个类型
      return {
        type: 'easyAgentMarkers',
        kind: parsed.type,
        raw: parsed.raw,
        cards: parsed.cards,
        display: parsed.display,
        compact: parsed.compact,
      }
    },
    renderer(token) {
      if (token.kind === 'src') {
        return wrapSourceCards(token.cards.map((c) => buildSourceCardHtml(c.title, c.url)))
      }
      return buildCitationHtml(token.display, token.compact)
    },
  }
}

function easyAgentEmojiExtension() {
  return {
    name: 'easyAgentEmoji',
    level: 'inline',
    start(src) {
      const m = EMOJI_SEARCH_RE.exec(src)
      return m ? m.index : undefined
    },
    tokenizer(src, tokens) {
      const m = EMOJI_TOKEN_RE.exec(src)
      if (!m) return
      const name = m[1]
      if (/^[a-z]{1,2}$/i.test(name) || EMOJI_BLOCKLIST.has(name.toLowerCase())) return
      const emoji = nameToEmoji[name] || nameToEmoji[name.toLowerCase()]
      if (!emoji || !emojiBoundaryOk(tokens, src, m[0].length)) return
      return { type: 'easyAgentEmoji', raw: m[0], name, emoji }
    },
    renderer(token) {
      return (
        '<span class="github-emoji" role="img" aria-label=":' + escapeHtml(token.name) + ':">' +
        token.emoji + '</span>'
      )
    },
  }
}

export function setupMarkedExtensions() {
  if (initialized) return
  initialized = true

  // 占位标记最先注册：它的开头是 NUL，不会与任何 markdown 语法冲突
  marked.use({ extensions: [easyAgentMarkerExtension()] })

  // 数学公式：$$...$$ 块级与 KaTeX 渲染器沿用 marked-katex-extension；行内公式换成中文友好的定界规则
  // （./math.js：$ 紧挨汉字、全角标点也能识别，认 \( \)，美元金额与 shell 变量不会被当成公式）
  const katexExt = katexExtension({
    throwOnError: false,
    nonStandard: false,
    strict: false,
    errorColor: '#cc0000',
  })
  marked.use({
    extensions: katexExt.extensions.map((ext) =>
      ext.name === 'inlineKatex' ? createInlineMathExtension(ext.renderer) : ext
    ),
  })

  // GitHub 风格 emoji 短代码，例如 :smile: :rocket: :fire: :+1: :tada:
  marked.use({ extensions: [easyAgentEmojiExtension()] })

  // 中日韩文字紧挨强调符时也能识别：`**注意：**请勿删除`、`这是**“重点”**内容`。
  // CommonMark 的定界规则把汉字当普通字母，这类写法的加粗要么原样露出星号，要么和后面的 ** 错配。
  marked.use(markedCjkFriendly())

  // 删除线只认 ~~双波浪线~~。中文里单个 ~ 是范围符（3~5 天、1.2495~2505），GFM 的单波浪线删除线
  // 会把同一段里的两个范围配成一对：~ 被吞掉，中间的文字被划掉。
  // 必须注册在 CJK 扩展之后：marked 先调用后注册的 tokenizer，返回 false 才交给先注册的那个；
  // 返回 undefined 表示「这里不是删除线」，到此为止。
  marked.use({
    tokenizer: {
      del(src) {
        return src.startsWith('~~') ? false : undefined
      },
    },
  })
}

// ---------------------------------------------------------------------------
// 代码块复制：markdown 渲染产物通过 v-html 注入，按钮用 inline onclick 调全局函数
// （消毒时只放行渲染器写死的 CODE_COPY_ONCLICK，见 ./sanitize.js）。
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

// 渲染器的逐次渲染状态挂在实例上：marked.parse / parser 都是同步调用，
// 不存在并发交错；不用全局变量，多个渲染器实例互不干扰。
const RENDER_STATE = new WeakMap()
const BLOCK_CACHES = new WeakMap()
const BLOCK_CACHE_MAX = 400
const PARSE_OPTIONS = { breaks: true, gfm: true, async: false }

function renderState(renderer, options) {
  let state = RENDER_STATE.get(renderer)
  if (!state) {
    state = { partial: false, rawHtml: 'escape' }
    RENDER_STATE.set(renderer, state)
  }
  state.partial = !!options.partial
  state.rawHtml = options.rawHtml === 'allow' ? 'allow' : 'escape'
  return state
}

/**
 * 创建统一的 markdown 渲染器（ChatMessage / FilePreview 共用同一套代码块结构）
 *
 * marked 17.x 的 Renderer 回调为 token 对象签名：
 *   code({ text, lang, escaped }) / link({ href, title, tokens }) / html({ text })
 * link 的可见文本需自行用 this.parser.parseInline(tokens) 渲染（覆盖后 marked 不再代劳）。
 *
 * @param {Object} options 配置
 * @param {(code: string, lang: string, state: {partial: boolean}) => string} options.highlight
 *   高亮函数，返回完整代码块 HTML（含 <pre>）；state.partial 为流式中间帧
 * @param {boolean} [options.externalLinks=true] 是否为外链添加 target="_blank" rel="noopener noreferrer"
 * @returns {Object} marked Renderer 实例
 */
export function createMarkdownRenderer({ highlight, externalLinks = true }) {
  const renderer = new marked.Renderer()
  const state = { partial: false, rawHtml: 'escape' }
  RENDER_STATE.set(renderer, state)

  renderer.code = function ({ text, lang }) {
    const code = text || ''
    const rawLang = (lang || '').trim().split(/\s+/)[0]
    const langLabel = escapeHtml(rawLang || 'text')
    const highlighted = typeof highlight === 'function' ? highlight(code, rawLang, state) : ''
    return (
      '<div class="code-block-wrapper">' +
      '<div class="code-header">' +
      `<span class="code-lang">${langLabel}</span>` +
      `<button class="code-copy-btn" onclick="${CODE_COPY_ONCLICK}">${CODE_COPY_SVG}<span>复制</span></button>` +
      '</div>' +
      highlighted +
      '</div>'
    )
  }

  // 默认把模型写的原始 HTML 当文本显示，只放行小白名单；文件预览等可信场景传 rawHtml: 'allow'
  renderer.html = function ({ text, block }) {
    if (state.rawHtml === 'allow') return text
    const escaped = escapeRawHtml(text)
    // 以标签开头的行会被识别成块级 HTML：转义成文字后按普通段落显示（保留换行），
    // 否则没有 <p> 包裹、段落样式缺失，多行内容还会挤成一行。白名单里的 details 块保持原结构
    if (block && !/^\s*<\/?(?:details|summary)\b/i.test(escaped)) {
      return '<p>' + escaped.replace(/\s+$/, '').replace(/\n/g, '<br>') + '</p>\n'
    }
    return escaped
  }

  if (externalLinks) {
    renderer.link = function ({ href, title, tokens }) {
      const url = href || ''
      const text = this.parser.parseInline(tokens)
      // 没有协议的相对地址 / 页内锚点（模型写的 report.docx、/workspace/…，脚注 [^1] 被当成的链接定义）：
      // 点了会在本页跳转、离开对话，只显示文字，悬停可看到原地址
      if (!/^(?:[a-z][a-z0-9+.-]*:|\/\/)/i.test(url.trim())) {
        return `<span class="md-link-disabled" title="${escapeHtml(url)}">${text}</span>`
      }
      const isExternal = /^https?:\/\//i.test(url)
      const titleAttr = title ? ` title="${escapeHtml(title)}"` : ''
      const extra = isExternal ? EXTERNAL_LINK_ATTRS : ''
      return `<a href="${escapeHtml(url)}"${titleAttr}${extra}>${text}</a>`
    }

    // 模型常把链接写成行内代码（`https://…`）：整段就是一个 URL 时也可点击，保留代码外观
    renderer.codespan = function ({ text }) {
      const code = `<code>${escapeHtml(text)}</code>`
      const url = String(text || '').trim()
      if (!URL_ONLY_TEXT_RE.test(url)) return code
      return `<a href="${escapeHtml(url)}"${EXTERNAL_LINK_ATTRS}>${code}</a>`
    }
  }

  return renderer
}

/**
 * lexer / parser 是 marked 的独立函数，不会自动合并通过 marked.use 注册的
 * defaults.extensions；必须显式带上，否则 KaTeX、CJK、占位标记等扩展全部失效。
 */
function parseOptions(renderer) {
  return { ...marked.defaults, ...PARSE_OPTIONS, renderer }
}

/** 把 token 列表渲染成已消毒的 HTML（单块与整篇共用同一条出口） */
function tokensToHtml(tokens, renderer, options) {
  const html = marked.parser(tokens, parseOptions(renderer))
  // 任务列表项打上 class，样式据此去掉圆点（li:has(> input) 需要 Chrome 105+，内网浏览器不支持）
  const tasked = html.replace(/<li><input /g, '<li class="task-list-item"><input ')
  // marked 不过滤原始 HTML 与 javascript: 链接：产物一律清洗后再交给 v-html
  return sanitizeHtml(tasked, { remoteImages: options.remoteImages === 'inline' ? 'inline' : 'link' })
}

/** 与渲染选项拼成缓存键：不同策略下的同一块文本不能共用 HTML */
function modeOf(options) {
  return (options.rawHtml === 'allow' ? 'A' : 'E') +
    (options.partial ? 'P' : 'F') +
    (options.remoteImages === 'inline' ? 'I' : 'L')
}

function hashText(text) {
  let h = 0
  for (let i = 0; i < text.length; i++) h = ((h << 5) - h + text.charCodeAt(i)) | 0
  return String(h)
}

function prepareMarkdown(content, options) {
  const src = options.partial ? bufferStreamingTail(content) : content
  const math = normalizeMathDelimiters(src)
  return options.sourceCards === false ? math : beautifySourceLinks(math)
}

/**
 * 统一渲染入口：规整数学公式后按 gfm + 换行策略渲染，产物经 DOMPurify 清洗
 *
 * @param {string} content markdown 文本
 * @param {Object} renderer createMarkdownRenderer 创建的渲染器
 * @param {{ sourceCards?: boolean, remoteImages?: 'link'|'inline', rawHtml?: 'escape'|'allow', partial?: boolean }} [options]
 *   sourceCards=false 时不做来源卡片改写（文件/文档预览）；
 *   remoteImages 默认 'link'：外站图片改成链接、不自动加载（防提示注入借图片地址外带数据），文件预览传 'inline'；
 *   rawHtml 默认 'escape'：模型写的原始 HTML 当文本显示，只放行 br/sub/sup/kbd/details/summary；
 *   partial=true 为流式中间帧：缓冲末行、代码块不做自动语言识别
 * @returns {string} 已消毒、可直接 v-html 的 HTML 字符串
 */
export function renderMarkdown(content, renderer, options = {}) {
  if (!content) return ''
  setupMarkedExtensions() // 幂等：直接从内核调用时也能保证扩展已注册
  renderState(renderer, options)
  try {
    const prepared = prepareMarkdown(content, options)
    return tokensToHtml(marked.lexer(prepared, parseOptions(renderer)), renderer, options)
  } catch (e) {
    console.error('Markdown 渲染失败:', e)
    return escapeHtml(content)
  }
}

/**
 * 分块渲染：以顶层 token.raw 为键缓存已完成块的 HTML（中间帧与最终渲染分别缓存），只重渲最后一块。
 * MarkdownBody 用 patchMarkdownBlocks（./blockDom.js）把各块节点直接放进根节点（不加包裹层），
 * 内容没变的块保留原节点（文字可选中、图片不闪），且单次渲染从「整篇」降到「一个块」。
 *
 * @param {string} content markdown 文本
 * @param {Object} renderer createMarkdownRenderer 创建的渲染器
 * @param {Object} [options] 同 renderMarkdown
 * @returns {Array<{key: string, html: string}>} 按原文顺序的块列表
 */
export function renderMarkdownBlocks(content, renderer, options = {}) {
  if (!content) return []
  setupMarkedExtensions()
  renderState(renderer, options)
  try {
    const prepared = prepareMarkdown(content, options)
    const tokens = marked.lexer(prepared, parseOptions(renderer))
    let cache = BLOCK_CACHES.get(renderer)
    if (!cache) {
      cache = new Map()
      BLOCK_CACHES.set(renderer, cache)
    }
    // 引用式链接的定义（[x]: url，以及被当成定义的脚注 [^1]: …）会改变其它块的渲染，一并进缓存键
    const defs = tokens.links && Object.keys(tokens.links).length ? hashText(JSON.stringify(tokens.links)) : ''
    const mode = modeOf(options) + defs
    const blocks = []
    for (let i = 0; i < tokens.length; i++) {
      const raw = tokens[i].raw || ''
      const isLast = i === tokens.length - 1
      const cacheKey = raw + '\u0000' + mode
      let html = isLast ? null : cache.get(cacheKey)
      if (html != null) {
        // 命中即刷新位置（LRU），正在流式输出的长回答的前面几块不会先被淘汰
        cache.delete(cacheKey)
        cache.set(cacheKey, html)
      } else {
        html = tokensToHtml([tokens[i]], renderer, options)
        // 最后一块还在增长，不缓存。中间帧也要写缓存（键里的模式已区分中间帧与最终渲染，
        // 两者只有代码块高亮不同）——只在最终渲染时写的话，流式期间每帧都会把所有块重渲一遍
        if (!isLast && raw.length <= 20000) {
          if (cache.size >= BLOCK_CACHE_MAX) cache.delete(cache.keys().next().value)
          cache.set(cacheKey, html)
        }
      }
      if (!html) continue
      // key 只看位置与原文、不含模式：中间帧与最终渲染之间 key 不变，
      // MarkdownBody 按 key + HTML 判断是否复用节点，内容没变的块 DOM 不动
      blocks.push({ key: i + ':' + hashText(raw), html })
    }
    return blocks
  } catch (e) {
    console.error('Markdown 分块渲染失败:', e)
    return [{ key: '0', html: escapeHtml(content) }]
  }
}

/**
 * 清掉某个渲染器的分块缓存。高亮器异步就绪（Vue3 的 shiki）后调用：
 * 缓存里是就绪前渲染的未高亮代码块，不清掉会一直命中旧结果。
 *
 * @param {Object} renderer createMarkdownRenderer 创建的渲染器
 */
export function clearMarkdownBlockCache(renderer) {
  BLOCK_CACHES.delete(renderer)
}
