/**
 * Vue2 侧统一的 markdown 渲染入口：共享 marked 渲染器 + highlight.js 高亮。
 *
 * 2026-10-20 从 ChatMessage.vue 抽出（原来的 renderer 常量 + renderMarkdown 方法），
 * 让 MarkdownBody、FilePreview 等入口共用同一个渲染器实例与代码块结构。
 */

import { createMarkdownRenderer, renderMarkdown as renderWith } from '../../markdownSetup.js'
import { highlightCode } from './highlight.js'

/** 全站共用的 marked 渲染器（代码块外壳、外链、来源卡片） */
export const markdownRenderer = createMarkdownRenderer({ highlight: highlightCode })

/**
 * 渲染 markdown 为 HTML。
 *
 * @param {string} content markdown 文本（调用方负责先做业务改写，如知识引用）
 * @returns {string} HTML
 */
export function renderMarkdown(content) {
  return renderWith(content, markdownRenderer)
}
