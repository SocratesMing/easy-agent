/**
 * 消息 markdown 渲染：shiki 高亮 + 共享 marked 渲染器（模块级单例）。
 *
 * 2026-10-20 从 ChatMessage.vue 抽出，两个目的：
 * 1. 高亮引擎只初始化一次——原实现每个 ChatMessage 实例在 onMounted 里各建一份
 *    createHighlighter（n 条消息 = n 份引擎，长会话切换明显卡）；
 * 2. 渲染器与语言映射不再散在组件里，后续拆 MarkdownBody 子组件时可直接复用。
 *
 * 用法：组件 setup 里调用 `ensureHighlighter()`（幂等，不阻塞首屏），
 * 模板/计算属性里直接用 `renderMarkdown(content)`。
 */

import { shallowRef } from 'vue'
import { createHighlighter } from 'shiki'
import {
  setupMarkedExtensions,
  createMarkdownRenderer,
  escapeHtml,
  renderMarkdown as renderMarkdownHtml,
} from '../../../markdownSetup.js'

setupMarkedExtensions()

/** 语言别名：模型常写 js/py/sh 等短名，shiki 需要完整语言 id */
const LANG_ALIASES = {
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

/** 需要预加载的语言（与原来 ChatMessage 里的列表保持一致） */
const LANGS = [
  'javascript', 'typescript', 'python', 'java', 'cpp', 'c', 'go', 'rust',
  'html', 'css', 'json', 'yaml', 'markdown', 'bash', 'shell', 'sql', 'xml',
  'vue', 'jsx', 'tsx', 'text',
]

const highlighter = shallowRef(null)
let initPromise = null

/** 初始化 shiki（首次加载完成后全局复用；失败只告警，不影响纯文本渲染） */
export function ensureHighlighter() {
  if (highlighter.value || initPromise) return initPromise
  initPromise = createHighlighter({ themes: ['github-light'], langs: LANGS })
    .then((instance) => {
      highlighter.value = instance
      return instance
    })
    .catch((e) => {
      console.error('Shiki 初始化失败:', e)
      return null
    })
  return initPromise
}

const FALLBACK_STYLE =
  'background: #f6f8fa; padding: 12px; border-radius: 8px; overflow-x: auto; ' +
  "border: 1px solid #e1e4e8; color: #24292e; font-family: 'Fira Code', Consolas, monospace; font-size: 13px;"

function fallbackBlock(code) {
  return `<pre style="${FALLBACK_STYLE}"><code style="color: #24292e; font-family: 'Fira Code', Consolas, monospace; font-size: 13px;">${escapeHtml(code)}</code></pre>`
}

/**
 * 代码高亮：返回完整 `<pre>` 块（marked 渲染器会把它塞进代码块外壳）。
 * 高亮器未就绪或语言不支持时降级为转义后的纯文本。
 *
 * @param {string} code 代码正文
 * @param {string} [lang] 语言标识（可为短名，如 py/js）
 * @returns {string} HTML
 */
export function highlightCode(code, lang) {
  if (!highlighter.value) return fallbackBlock(code)

  const normalized = lang ? lang.toLowerCase() : 'text'
  const mapped = LANG_ALIASES[normalized] || normalized
  const loaded = highlighter.value.getLoadedLanguages()
  const validLang = loaded.includes(mapped) ? mapped : 'text'

  try {
    return highlighter.value.codeToHtml(code, { lang: validLang, theme: 'github-light' })
  } catch (e) {
    console.error('Shiki 高亮失败:', e, 'lang:', validLang)
    return fallbackBlock(code)
  }
}

/** 全站共用的 marked 渲染器（代码块外壳、外链、来源卡片） */
export const markdownRenderer = createMarkdownRenderer({ highlight: highlightCode })

/**
 * 渲染 markdown 为 HTML（含来源卡片美化、公式规整、未闭合围栏保护）。
 *
 * @param {string} content markdown 文本
 * @returns {string} HTML
 */
export function renderMarkdown(content) {
  return renderMarkdownHtml(content, markdownRenderer)
}
