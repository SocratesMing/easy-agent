/**
 * 把正文里的 `[知识依据N]` 改写成可点击锚点（数据来自消息的 sources 映射）。
 * 与 Vue2 基线保持同一实现，供 MarkdownBody 复用。
 */
export function linkKnowledgeCitations(content, citeMap) {
  if (typeof content !== 'string') return content
  const map = citeMap || {}
  return content.replace(
    /\[知识(?:库)?依据\s*(\d+)\]/g,
    (match, num) => {
      const display = map[num] || Number(num)
      return `<a class="ke-cite" data-cite="${display}">[知识依据${display}]</a>`
    }
  )
}
