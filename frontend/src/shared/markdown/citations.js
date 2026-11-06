/**
 * 把正文里的 `[知识依据N]` 改写成可点击锚点（数据来自消息的 sources 映射）。
 * 与 Vue2 基线保持同一实现，供 MarkdownBody 复用。
 * 2026-11-06：改成占位标记 + marked 扩展生成 HTML（不再往源文本注入 <a>），
 * 并经 protectCodeRegions 跳过代码块，避免 `print("[知识依据1]")` 被改写。
 */
import { protectCodeRegions } from './codeRegions.js'
import { citationMarker } from './markers.js'

export function linkKnowledgeCitations(content, citeMap) {
  if (typeof content !== 'string') return content
  const map = citeMap || {}
  return protectCodeRegions(content, (text) =>
    text.replace(
      /\[知识(?:库)?依据\s*(\d+)\]/g,
      (match, num) => citationMarker(map[num] || Number(num))
    )
  )
}
