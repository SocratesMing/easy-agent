/**
 * 代码高亮（highlight.js，同步 API）：语言别名 + 结果缓存。
 *
 * 2026-10-20 从 ChatMessage.vue 抽出。原来缓存挂在组件模块作用域，虽已跨实例共享，
 * 但与 2700 行的消息组件混在一起；抽到 services 后 FilePreview 等其它代码块入口
 * 也可以复用同一份缓存与别名表。
 *
 * 注意：highlightAuto 是整份语法扫描，最贵；流式期间未闭合的代码块建议调用方
 * 直接输出纯文本，等 content_end 再走这里（见 ChatMessage 的既有策略）。
 */

import hljs from 'highlight.js'
import { escapeHtml } from '../../markdownSetup.js'

const langAliases = {
  js: 'javascript',
  ts: 'typescript',
  py: 'python',
  rb: 'ruby',
  sh: 'bash',
  yml: 'yaml',
  md: 'markdown',
  'c++': 'cpp',
  'c#': 'csharp',
  cs: 'csharp',
}

const highlightCache = new Map()
const HIGHLIGHT_CACHE_MAX = 200
const HIGHLIGHT_CACHE_CODE_MAX = 20000

function fallbackPre(code) {
  return `<pre class="hljs" style="background: #0d1117; padding: 12px 16px; border-radius: 8px; overflow-x: auto; border: 1px solid #21262d;"><code style="color: #c9d1d9; font-family: 'Fira Code', Consolas, monospace; font-size: 13px;">${escapeHtml(code)}</code></pre>`
}

/**
 * 高亮一段代码，返回完整 `<pre>` 块。
 *
 * @param {string} code 代码正文
 * @param {string} [lang] 语言标识（支持 js/py/sh 等别名）
 * @returns {string} HTML
 */
export function highlightCode(code, lang) {
  const normalizedLang = lang ? String(lang).toLowerCase() : ''
  const mappedLang = langAliases[normalizedLang] || normalizedLang
  // 未识别/未注册的语言交给 highlightAuto 自动识别，避免对未知语言抛错
  const validLang = mappedLang && hljs.getLanguage(mappedLang) ? mappedLang : ''

  const cacheKey = validLang + '\u0000' + code
  const cacheable = code.length <= HIGHLIGHT_CACHE_CODE_MAX
  if (cacheable) {
    const cached = highlightCache.get(cacheKey)
    if (cached) return cached
  }

  try {
    let inner = ''
    if (validLang) {
      inner = hljs.highlight(code, { language: validLang }).value
    }
    if (!inner) inner = hljs.highlightAuto(code).value
    const langClass = validLang ? ` class="language-${escapeHtml(validLang)}"` : ''
    const html = `<pre class="hljs"><code${langClass}>${inner}</code></pre>`
    if (cacheable) {
      if (highlightCache.size >= HIGHLIGHT_CACHE_MAX) {
        // 简易淘汰：清掉最早写入的一项
        highlightCache.delete(highlightCache.keys().next().value)
      }
      highlightCache.set(cacheKey, html)
    }
    return html
  } catch (e) {
    console.error('代码高亮失败:', e, 'lang:', validLang)
    return fallbackPre(code)
  }
}
