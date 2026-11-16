// 文本预览的行号几何：行号必须与正文行一一对应（两个前端的 FilePreview 共用本模块）。
//
// 2026-11-16 修的两处问题：
// 1) 行号原先是独立一列 <span>、正文是 white-space: pre-wrap 的 <pre>。超宽的行被软换行后
//    占掉一个视觉行却不增加行号，行号列从第一个折行处开始整体上移，中后段表现为
//    「行号到某个数就不再显示、正文还在继续」，而且之前的行号已经对错了行。
//    改成按行渲染（.code-line 一行一条记录）后，折行只让这一行变高，行号永远对齐。
// 2) 正文按 50000 字符硬截断会切在句子中间，行号总数也跟着被截断的数字走。
//    现在保留完整文本，只按行分页控制渲染量；字符上限仅在文件极大时兜底，且裁在行边界。
//
// 纯函数放这里是为了能单测（./textLines.test.js），组件只负责渲染。

/** 每次「加载更多」增加的行数 */
export const TEXT_LINE_PAGE = 3000

/** 单文件字符上限：再大就按行边界裁掉，避免 split 与逐行渲染吃光内存 */
export const TEXT_CHAR_LIMIT = 2000000

/**
 * 按行切分源文本。
 *
 * 换行口径必须与浏览器一致：`\r\n` 与单独的 `\r` 都算一次换行（HTML 解析器同样把
 * CRLF/CR 归一成 LF）。只按 `\n` 切会让 CRLF 之外的老式行尾少算行，行号随后全错位。
 *
 * @param {string} text 源文本
 * @returns {string[]} 行数组；空文本返回空数组（不返回 ['']，避免出现一个空行号）
 * @example
 * splitSourceLines('a\r\nb\rc') // ['a', 'b', 'c']
 */
export function splitSourceLines(text) {
  if (typeof text !== 'string' || text === '') return []
  return text.replace(/\r\n?/g, '\n').split('\n')
}

/**
 * 超大文件的字符兜底：超过上限时裁到最近的换行处，绝不切在行中间。
 *
 * @param {string} text 源文本
 * @param {number} [limit] 字符上限
 * @returns {{ text: string, truncated: boolean, limit: number }}
 * @example
 * clampSourceText('a\nb\nc', 3) // { text: 'a\nb', truncated: true, limit: 3 }
 */
export function clampSourceText(text, limit = TEXT_CHAR_LIMIT) {
  const source = String(text == null ? '' : text)
  const max = Math.max(1, Math.floor(limit) || TEXT_CHAR_LIMIT)
  if (source.length <= max) return { text: source, truncated: false, limit: max }
  const cut = source.lastIndexOf('\n', max)
  // 找不到换行（整篇一行）时只能按字符裁，否则一行都显示不出来
  return { text: source.slice(0, cut > 0 ? cut : max), truncated: true, limit: max }
}

/**
 * 取一页行，带上行号。
 *
 * @param {string[]} lines splitSourceLines 的结果
 * @param {number} maxLines 本页最多渲染多少行
 * @returns {{ lines: Array<{ n: number, text: string }>, total: number, shown: number, hasMore: boolean }}
 * @example
 * takeLines(['a', 'b', 'c'], 2) // { lines: [{ n: 1, text: 'a' }, { n: 2, text: 'b' }], total: 3, shown: 2, hasMore: true }
 */
export function takeLines(lines, maxLines) {
  const all = Array.isArray(lines) ? lines : []
  const total = all.length
  const requested = Math.floor(Number(maxLines))
  const limit = requested > 0 ? requested : total
  const shown = Math.min(total, limit)
  return {
    lines: all.slice(0, shown).map((text, index) => ({ n: index + 1, text })),
    total,
    shown,
    hasMore: shown < total,
  }
}

/**
 * 行号列的宽度（px）：整列统一宽度，各行行号才不会因为位数不同而左右错位。
 *
 * @param {number} totalLines 总行数（决定最大位数）
 * @returns {string} 供 CSS 变量使用的像素值
 * @example
 * lineNumberGutterWidth(3800) // '56px'
 */
export function lineNumberGutterWidth(totalLines) {
  const digits = String(Math.max(1, Math.floor(Number(totalLines)) || 1)).length
  // 13px 等宽字体一位数字约 8px，再加 20px 内边距
  return Math.max(40, digits * 9 + 20) + 'px'
}
