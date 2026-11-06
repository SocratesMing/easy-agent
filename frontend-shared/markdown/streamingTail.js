// 流式渲染的末行缓冲：模型逐字吐出 markdown 时，最后一个未完成的行会被
// marked 当成另一种结构（setext 标题、分隔线、未闭合的强调/公式），下一帧又
// 变回去，表现为闪烁。partial=true 时先把这一行藏起来或补上闭合符；流结束
// 后按原文完整渲染，最终结果不变。
//
// 不用正则后行断言（lookbehind）：不支持它的引擎（Safari < 16.4、老 Chromium）解析到就会报语法错误，
// 整个 markdown 内核模块加载失败、对话全部渲染不出来（marked 自己也对后行断言做了特性检测）。
import { protectCodeRegions } from './codeRegions.js'

/** 结构标记独占一行：setext 标题的 `-` / `=`、列表符号、表格分隔行残片等 */
const STRUCTURAL_LINE = /^\s*(?:[-=*+]|\d+[.)]|#{1,6}|>|`{1,}|\|[\s:|\-+]*)\s*$/

/** 长度恰好为 1 的连续字符段个数（例如单个 ` 或单个 *，不算 `` / ** 里的） */
function singleRuns(line, re) {
  return (line.match(re) || []).filter((run) => run.length === 1).length
}

/** 行内还有没写完的构造：宁可整行先不显示，也不要露出 `**` / TeX 源码 */
function hasUnclosedInline(line) {
  const count = (re) => (line.match(re) || []).length
  if (count(/\*\*/g) % 2 === 1) return true
  if (count(/\$\$/g) % 2 === 1) return true
  if (singleRuns(line, /`+/g) % 2 === 1) return true
  if (count(/\[/g) > count(/\]/g)) return true
  if (singleRuns(line, /\*+/g) % 2 === 1) return true
  return false
}

/** 块公式的 $$ 还没闭合（开头在前面的行）：从最后一个 $$ 起先不显示，避免露出 TeX 源码；代码里的 $$ 不算 */
function cutUnclosedDisplayMath(content) {
  return protectCodeRegions(content, (text) => {
    const count = (text.match(/\$\$/g) || []).length
    return count % 2 === 1 ? text.slice(0, text.lastIndexOf('$$')).replace(/\s+$/, '') : text
  })
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
  const text = cutUnclosedDisplayMath(content)
  if (!text) return text
  const lines = text.split('\n')
  if (lines.length < 2) return hasUnclosedInline(lines[0]) ? '' : text
  const last = lines[lines.length - 1]
  if (STRUCTURAL_LINE.test(last) || hasUnclosedInline(last)) {
    return lines.slice(0, -1).join('\n')
  }
  return text
}
