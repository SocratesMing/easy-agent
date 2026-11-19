// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/markdown/sourceFooter.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
import { tokenizeMarkdown } from './index.js'

// 独立的来源披露属于回答尾注；句子/标题后附着的括注仍属于行内引用。
const LABEL = /^(?:[（(]\s*)?(?:数据来源|参考来源|参考资料|引用来源|来源)\s*[:：]|^(?:外部连接器|连接器依据|知识库来源)\s*[:：（(]/
function plainLine(line) {
  return line.trim().replace(/^(?:#{1,6}\s+|>\s*|[-+*]\s+|\d+[.)]\s+)/, '').replace(/\*\*|__/g, '').trim()
}

/** 只在非代码块中识别尾部来源段，返回原文字符位置；-1 表示没有。 */
export function sourceFooterStart(content) {
  if (!content) return -1
  const lines = []
  let offset = 0
  for (const token of tokenizeMarkdown(content)) {
    const raw = token.raw || ''
    let lineOffset = offset
    for (const line of raw.split('\n')) {
      if (token.type !== 'code' && token.type !== 'html') lines.push({ raw: line, text: plainLine(line), offset: lineOffset })
      else if (line.trim()) lines.push({ raw: line, text: null, offset: lineOffset })
      lineOffset += line.length + 1
    }
    offset += raw.length
  }
  for (let i = 0; i < lines.length; i++) {
    if (!lines[i].text || !LABEL.test(lines[i].text)) continue
    // 来源标题之后允许来源条目、列表、链接和缩进续行；随后出现普通正文则不是尾注。
    const valid = lines.slice(i + 1).every(line => line.text !== null &&
      (!line.text || LABEL.test(line.text) || /^(?:[-+*]\s|\d+[.)]\s|\s{2,}\S|https?:\/\/|\[)/.test(line.raw.trimStart()) || /^\s{2,}\S/.test(line.raw)))
    if (valid) return lines[i].offset
  }
  return -1
}

/** 按完整回答计算每个渲染分块中的尾注位置，避免分块末尾被误判成回答末尾。 */
export function citationFooterOffsets(blocks, fallback = '') {
  const entries = blocks.map((block, index) => ({ block, index })).filter(entry => entry.block.type === 'content')
  const text = entries.length ? entries.map(entry => entry.block.content || '').join('\n') : fallback
  const start = sourceFooterStart(text)
  const offsets = blocks.map(() => -1)
  let offset = 0
  for (const { block, index } of entries) {
    const length = (block.content || '').length
    if (start >= 0 && start < offset + length) offsets[index] = Math.max(0, start - offset)
    offset += length + 1
  }
  return { whole: start, blocks: offsets }
}
