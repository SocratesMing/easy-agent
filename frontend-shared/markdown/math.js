// 行内公式的定界规则：替换 marked-katex-extension 的行内规则（块级 $$ 与 KaTeX 渲染器仍用扩展自带的）。
//
// 扩展的「标准模式」要求开头 $ 前是空格、结尾 $ 后只能是空白或 ?!.,:？！。，： 这几个字符，中文里常见的写法都会出问题：
//   设变量$x$满足、（$x$）    开头 $ 前是汉字 / 全角括号 → 不识别，原样显示 $
//   其中 $a$、$b$ 为常数      结尾 $ 后是顿号 → 一直配到后面的 $，整段报红（KaTeX 解析错误）
//   \(E=mc^2\)               模型常用的另一种定界符不认，反斜杠还被当成转义吃掉
// 这里的 $ 规则按 Pandoc 的做法再加防误判，保证美元金额、shell 变量不被当成公式：
//   - 开头 $ 后紧跟非空白，结尾 $ 前紧跟非空白：「从 $5 到 $10」不成对；
//   - 开头 $ 前、结尾 $ 后都不能紧挨英文字母或数字：「花了$5，又花了$10」「US$5」「$HOME/$PATH」都不成对；
//   - 只取开头之后的第一个 $ 作结尾，不跨过它往后找，两段公式不会连成一段；
//   - 公式不跨行，$$ 留给块级规则（normalizeMathDelimiters 已把 $$…$$ 规整成独立块）。
// \( … \) 是显式定界符，不做以上猜测；\[ … \] 在 normalizeMathDelimiters 里规整成 $$ 块。

const ALNUM = /[A-Za-z0-9]/
// 开头 $ 之后：非空白起头、不含未转义的 $、不跨行，非空白结尾，结尾 $ 后不紧挨字母数字
const DOLLAR_BODY = String.raw`\$(?=[^\s$])((?:\\.|[^\\\n$])*?(?:\\.|[^\s\\$]))\$(?![A-Za-z0-9])`
const DOLLAR_INLINE = new RegExp('^' + DOLLAR_BODY)
const DOLLAR_AT = new RegExp(DOLLAR_BODY, 'y')
const PAREN_INLINE = /^\\\((.+?)\\\)/
const PAREN_AT = /\\\((.+?)\\\)/y

/** 当前位置前一个字符：取同一段行内文本里上一个 token 的最后一个字符（段首为空串） */
function previousChar(tokens) {
  const last = Array.isArray(tokens) && tokens.length ? tokens[tokens.length - 1] : null
  const raw = last && typeof last.raw === 'string' ? last.raw : ''
  return raw ? raw[raw.length - 1] : ''
}

/** src 中第一个可能开始行内公式的位置（给 marked 切分文本用；段首字符的前一个字符看不到，交给 tokenizer 再判） */
function findInlineMathStart(src) {
  const candidates = /\\\(|\$/g
  let m
  while ((m = candidates.exec(src)) !== null) {
    const i = m.index
    if (m[0] === '$') {
      if (src[i + 1] === '$') {
        candidates.lastIndex = i + 2
        continue
      }
      const prev = i > 0 ? src[i - 1] : ''
      if (prev === '$' || prev === '\\' || ALNUM.test(prev)) continue
      DOLLAR_AT.lastIndex = i
      if (!DOLLAR_AT.test(src)) continue
    } else {
      if (i > 0 && src[i - 1] === '\\') continue // \\( 是转义的反斜杠加普通括号
      PAREN_AT.lastIndex = i
      if (!PAREN_AT.test(src)) continue
    }
    return i
  }
  return undefined
}

/**
 * 行内公式扩展（token 与 marked-katex-extension 的 inlineKatex 同形，渲染器由调用方传入扩展自带的那个）。
 *
 * @param {(token: object) => string} renderer marked-katex-extension 里 inlineKatex 的 renderer
 * @returns {object} marked 扩展（name: inlineKatex，level: inline）
 * @example
 * const ext = katexExtension(options)
 * const inline = ext.extensions.find((e) => e.name === 'inlineKatex')
 * marked.use({ extensions: [createInlineMathExtension(inline.renderer), ...] })
 */
export function createInlineMathExtension(renderer) {
  return {
    name: 'inlineKatex',
    level: 'inline',
    start: findInlineMathStart,
    tokenizer(src, tokens) {
      if (src.startsWith('\\(')) {
        const m = PAREN_INLINE.exec(src)
        if (!m || !m[1].trim()) return undefined
        return { type: 'inlineKatex', raw: m[0], text: m[1].trim(), displayMode: false }
      }
      if (src[0] !== '$' || src[1] === '$') return undefined
      if (ALNUM.test(previousChar(tokens))) return undefined
      const m = DOLLAR_INLINE.exec(src)
      if (!m) return undefined
      return { type: 'inlineKatex', raw: m[0], text: m[1], displayMode: false }
    },
    renderer,
  }
}
