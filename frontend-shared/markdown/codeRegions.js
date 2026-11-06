// 源文本预处理时的代码保护：把围栏代码块、缩进代码块、行内代码整体摘出来，
// 半预处理完再原样放回。normalizeMathDelimiters / beautifySourceLinks /
// linkKnowledgeCitations 都经由本模块处理，保证代码块里的内容逐字不变。
//
// 覆盖：``` / ~~~ 围栏（按开闭长度匹配的 >=3 个同字符，含 4 反引号嵌套围栏）、
// 流式输出中尚未闭合的围栏、4 空格 / Tab 缩进代码块、多反引号行内代码
// （`` a`b ``）、流式输出中尚未闭合的行内代码。

const HOLDER_RE = /\u0000EAC(\d+)\u0000/g

/** 占位符前缀：预处理函数用它判断「本行有代码，整行不改写」 */
export const CODE_HOLDER_PREFIX = '\u0000EAC'

/** 把一行里的成对行内代码换成占位符；未闭合时保护到行尾（流式场景） */
function protectInlineCode(line, hold) {
  const runs = []
  const re = /`+/g
  let m
  while ((m = re.exec(line))) runs.push({ start: m.index, len: m[0].length })
  if (!runs.length) return line

  let out = ''
  let cursor = 0
  let i = 0
  while (i < runs.length) {
    const open = runs[i]
    let closeAt = -1
    for (let j = i + 1; j < runs.length; j++) {
      if (runs[j].len === open.len) {
        closeAt = j
        break
      }
    }
    if (closeAt === -1) {
      return out + line.slice(cursor, open.start) + hold(line.slice(open.start))
    }
    const close = runs[closeAt]
    out += line.slice(cursor, open.start) + hold(line.slice(open.start, close.start + close.len))
    cursor = close.start + close.len
    i = closeAt + 1
  }
  return out + line.slice(cursor)
}

/**
 * 保护所有代码区域后执行字符串预处理。
 *
 * @param {string} content markdown 源文本
 * @param {(text: string) => string} transform 只作用于非代码文本的改写函数
 * @returns {string} 改写后、代码已原样还原的文本
 */
export function protectCodeRegions(content, transform) {
  if (typeof content !== 'string' || !content) return content
  const chunks = []
  const hold = (text) => {
    chunks.push(text)
    return CODE_HOLDER_PREFIX + (chunks.length - 1) + '\u0000'
  }

  const lines = content.split('\n')
  const out = []
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    const fence = /^ {0,3}(`{3,}|~{3,})/.exec(line)
    if (fence) {
      const char = fence[1][0] === '`' ? '`' : '~'
      const closing = new RegExp('^ {0,3}' + char + '{' + fence[1].length + ',}\\s*$')
      const block = [line]
      for (i = i + 1; i < lines.length; i++) {
        block.push(lines[i])
        if (closing.test(lines[i])) break
      }
      out.push(hold(block.join('\n')))
      continue
    }
    // 缩进代码块：整行保护。列表里缩进 4 空格的续行也会被视为代码，
    // 代价只是不做来源卡片 / 公式规整，不会改动内容。
    if (/^(?: {4}|\t)/.test(line)) {
      out.push(hold(line))
      continue
    }
    out.push(protectInlineCode(line, hold))
  }

  const result = transform(out.join('\n'))
  if (typeof result !== 'string') return result
  return result.replace(HOLDER_RE, (_, idx) => chunks[+idx] ?? '')
}
