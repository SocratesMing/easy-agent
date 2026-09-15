// 共享的 marked 扩展注册与渲染器工厂：数学公式(KaTeX) + GitHub 风格 emoji 短代码(:smile: 等)
// 通过 initialized 标志保证全局 marked 实例上的扩展只注册一次，
// 避免 ChatMessage / FilePreview 等多个组件重复注册导致扩展冲突。
//
// 版本约定：marked 使用 17.x —— Renderer 回调只接收一个 token 对象
// （5.x 起由 (code, infostring) 字符串签名改为 token 对象）。
// 下方回调内部保留了旧版字符串签名的兼容分支：一旦依赖被回退到 4.x，
// 也不会静默渲染成 [object Object]。
import { marked } from 'marked'
import katexExtension from 'marked-katex-extension'
import { markedEmoji } from 'marked-emoji'
import { nameToEmoji } from 'gemoji'
import 'katex/dist/katex.min.css'

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
 * marked 5+ 的 Renderer 回调只接收 token 对象：
 *   code(token) / link(token)；4.x 的字符串签名仍被兼容。
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

  renderer.code = function (token) {
    // marked 5+ 只传 token 对象；4.x 仍是 (code, infostring) 字符串签名。
    // 不做兼容会导致 code 参数变成对象，最终渲染出 [object Object]。
    let code = ''
    let infostring = ''
    if (token && typeof token === 'object') {
      code = token.text || token.raw || ''
      infostring = token.lang || ''
    } else {
      code = token || ''
      infostring = (arguments[1] || '')
    }
    const rawLang = String(infostring).trim().split(/\s+/)[0]
    const langLabel = escapeHtml(rawLang || 'text')
    const highlighted = typeof highlight === 'function' ? highlight(String(code), rawLang) : ''
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
    renderer.link = function (token) {
      // 同样做双签名兼容（marked 5+ 传 token 对象）。
      let href = ''
      let title = ''
      let text = ''
      let tokens = null
      if (token && typeof token === 'object') {
        href = token.href || ''
        title = token.title || ''
        text = token.text || ''
        tokens = token.tokens || null
      } else {
        href = token || ''
        title = arguments[1] || ''
        text = arguments[2] || ''
      }

      const url = href || ''
      const isExternal = /^https?:\/\//i.test(url)
      const titleAttr = title ? ` title="${escapeHtml(title)}"` : ''
      const extra = isExternal ? ' target="_blank" rel="noopener noreferrer"' : ''

      // marked 5+ 的 token.text 是未解析的原始 markdown，必须交回 marked 做 inline 解析，
      // 否则链接文字里的 **加粗** / `代码` 等语法会原样显示。
      let body = ''
      if (tokens && this && this.parser) {
        try {
          body = this.parser.parseInline(tokens)
        } catch (e) {
          body = escapeHtml(text)
        }
      } else {
        // 4.x 传入的 text 已是渲染好的 HTML，直接使用。
        body = text
      }
      return `<a href="${escapeHtml(url)}"${titleAttr}${extra}>${body}</a>`
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
    // headerIds / mangle 是 marked 4.x 专有选项，5.x 起已移除（传了会被忽略），
    // 这里与 Vue3 侧保持一致的调用方式。
    return marked.parse(normalizeMathDelimiters(content), {
      renderer,
      breaks: true,
      gfm: true,
    })
  } catch (e) {
    console.error('Markdown 渲染失败:', e)
    return escapeHtml(content)
  }
}
