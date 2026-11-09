// 预处理与 marked 之间的「无 HTML」约定。
//
// 以前 beautifySourceLinks / linkKnowledgeCitations 直接把 `<a ...>` 拼进 markdown
// 源文本，导致两个后果：一是渲染器不能安全地转义模型写的原始 HTML（会误伤自己
// 注入的标签），二是代码块里的内容被字符串改写污染。现在改成往源文本里放一个
// 不含 `<` 的占位标记，由 marked 扩展的 renderer 生成 HTML——标记只会出现在
// text token 里，代码块（code token）天然跳过。
//
// 标记自带全部数据（encodeURIComponent(JSON)），不依赖跨渲染的全局状态。
// 分隔符用私用区字符而不是 NUL：marked 的 inline 规则会吞掉控制字符。

const OPEN = '\uE000'
const CLOSE = '\uE001'

/** 标记前缀：start() 用它定位候选位置，预处理也用它跳过已处理行 */
export const MARKER_PREFIX = OPEN + 'EA'

// inline tokenizer 会在每个字符位置调用一次，正则必须复用
const MARKER_RE = new RegExp('^' + OPEN + 'EA(SRC|CITE):([^' + CLOSE + ']*)' + CLOSE)

/** 一行或多行连续来源卡片的标记 */
export function sourceCardMarker(cards) {
  return MARKER_PREFIX + 'SRC:' + encodeURIComponent(JSON.stringify(cards)) + CLOSE
}

/** 知识依据锚点的标记 */
export function citationMarker(display) {
  return MARKER_PREFIX + 'CITE:' + encodeURIComponent(String(display)) + CLOSE
}

/**
 * 解析 inline tokenizer 当前位置上的标记。
 * @param {string} src 从当前位置开始的剩余文本
 * @returns {{type:'src'|'cite', raw:string, cards?:Array, display?:string}|null}
 */
export function parseMarker(src) {
  const m = MARKER_RE.exec(src)
  if (!m) return null
  try {
    if (m[1] === 'SRC') {
      const cards = JSON.parse(decodeURIComponent(m[2]))
      return Array.isArray(cards) ? { type: 'src', raw: m[0], cards } : null
    }
    return { type: 'cite', raw: m[0], display: decodeURIComponent(m[2]) }
  } catch (e) {
    return null
  }
}
