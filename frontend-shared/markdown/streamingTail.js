// 流式渲染的末行缓冲：模型逐字吐出 markdown 时，最后一个未完成的行会被
// marked 当成另一种结构（setext 标题、分隔线、未闭合的强调/公式），下一帧又
// 变回去，表现为闪烁。partial=true 时先把这一行藏起来或补上闭合符；流结束
// 后按原文完整渲染，最终结果不变。

/** 结构标记独占一行：setext 标题的 `-` / `=`、列表符号、表格分隔行残片等 */
const STRUCTURAL_LINE = /^\s*(?:[-=*+]|\d+[.)]|#{1,6}|>|`{1,}|\|[\s:|\-+]*)\s*$/

/** 行内还有没写完的构造：宁可整行先不显示，也不要露出 `**` / TeX 源码 */
function hasUnclosedInline(line) {
  const count = (re) => (line.match(re) || []).length
  if (count(/\*\*/g) % 2 === 1) return true
  if (count(/\$\$/g) % 2 === 1) return true
  // 单反引号（排除成对的多反引号）按奇偶判断
  if (count(/(?<!`)`(?!`)/g) % 2 === 1) return true
  if (count(/\[/g) > count(/\]/g)) return true
  if (count(/(?<!\*)\*(?!\*)/g) % 2 === 1) return true
  return false
}

/**
 * 流式中间帧的末行缓冲。
 * @param {string} content 当前已收到的完整文本
 * @returns {string} 用于中间帧渲染的文本
 */
export function bufferStreamingTail(content) {
  if (typeof content !== 'string' || !content) return content
  // 未闭合围栏：整个尾部都在代码块里，交给 marked 按 code token 处理，不动末行
  const fences = (content.match(/^ {0,3}(?:`{3,}|~{3,})/gm) || []).length
  if (fences % 2 === 1) return content
  const lines = content.split('\n')
  if (lines.length < 2) return hasUnclosedInline(lines[0]) ? '' : content
  const last = lines[lines.length - 1]
  if (STRUCTURAL_LINE.test(last) || hasUnclosedInline(last)) {
    return lines.slice(0, -1).join('\n')
  }
  return content
}
