// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/markdown/sourceReferences.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
import { tokenizeInlineMarkdown } from './index.js'
import { protectCodeRegions } from './codeRegions.js'
import { sourceUrl } from '../citations/sources.js'
import { sourceFooterStart } from './sourceFooter.js'

const CODE = /\u0000EAI(\d+)\u0000/g
const TOOL = /^[^\s\[\]（）()，,；;`]+(?:\s*\/\s*[^\s\[\]（）()，,；;`]+)*$/
const REFERENCE = new RegExp([
  /\[知识(?:库)?依据\s*(\d+)\]/.source,
  /\[连接器依据\s*[:：]?\s*([^\]\n]+)\]/.source,
  /([（(]\s*(?:来源\s*[:：]\s*(?:外部连接器|知识库)|外部连接器|连接器依据)|(?:来源\s*[:：]\s*)?外部连接器|连接器依据(?=\s|[:：（(]))/.source,
  /\[模型知识\]/.source,
  CODE.source,
].join('|'), 'g')

/** 匹配完整括注，允许中英文嵌套；流式未闭合时不提前吃掉半条说明。 */
function parenthesisEnd(text, start) {
  const stack = []
  for (let i = start; i < text.length && text[i] !== '\n'; i++) {
    const char = text[i]
    if (char === '(' || char === '（') stack.push(char === '(' ? ')' : '）')
    else if (char === ')' || char === '）') {
      if (stack.pop() !== char) return -1
      if (!stack.length) return i + 1
    }
  }
  return -1
}

function sourceAnnotation(text, start, prefix) {
  const outer = /^[（(]/.test(prefix)
  const rest = text.slice(start + prefix.length)
  const inner = !outer && rest.match(/^\s*[（(]/)
  let end
  if (outer || inner) {
    end = parenthesisEnd(text, outer ? start : start + prefix.length + inner[0].length - 1)
    if (end < 0) {
      const newline = text.indexOf('\n', start)
      return { end: newline < 0 ? text.length : newline }
    }
  } else {
    const bare = rest.match(/^[^\n。；;（）()\[\]]*/)
    end = start + prefix.length + (bare ? bare[0].trimEnd().length : 0)
  }
  const raw = text.slice(start, end)
  const note = (outer ? raw.slice(1, -1) : raw).trim()
  const connector = note.match(/^(?:来源\s*[:：]\s*)?(?:外部连接器|连接器依据)\s*[:：]?\s*([\s\S]+)/)
  const knowledge = note.match(/^来源\s*[:：]\s*知识库\s*《([^》\n]+)》/)
  return { end, note, connector: connector?.[1], knowledge: knowledge?.[1] }
}

/** 同一遍扫描用于分配编号与渲染；围栏/缩进代码仍整体保护。 */
export function rewriteReferences(content, reference, webLink, { footerStart = sourceFooterStart(content) } = {}) {
  const body = footerStart >= 0 ? content.slice(0, footerStart) : content
  const footer = footerStart >= 0 ? content.slice(footerStart) : ''
  return protectCodeRegions(body, text => {
    const rewriteText = (tokens, escapedBracket = false) => {
      const codes = []
      // 只把相邻文本与行内代码一起扫描，链接、图片和转义 token 不参与。
      const rawText = tokens.map(token => {
        if (token.type === 'text') return token.raw
        codes.push(token)
        return `\u0000EAI${codes.length - 1}\u0000`
      }).join('')
      const restore = raw => raw.replace(CODE, (_, index) => codes[index]?.raw ?? _)
      const toolName = value => value.replace(CODE, (_, index) => {
        const code = codes[index]
        if (!code) return _
        const text = code.text.trim()
        return TOOL.test(text) ? text : code.raw
      }).trim()
      let cursor = 0
      let output = ''
      const convert = (match, groups) => {
        const raw = restore(match)
        if (groups.knowledge) return reference(String(Number(groups.knowledge)), raw)
        if (groups.connector) return reference(`mcp:${toolName(groups.connector)}`, raw,
          TOOL.test(toolName(groups.connector)) ? undefined : raw.slice(1, -1).replace(/`+/g, ''))
        if (match === '[模型知识]') return ''
        const code = codes[Number(match.match(/\d+/)[0])]
        if (!code) return match
        const marker = code.text.trim().match(/^\[连接器依据\s+([^\]\n]+)\]$/)
        return marker
          ? reference(`mcp:${marker[1].trim()}`, code.raw) : code.raw
      }
      // 分配编号和转换共用扫描结果，跳过完整括注中已消费的 token。
      // 组合正则使用位置捕获，避免 Vue2 Babel 转译 .source 时丢失命名捕获映射。
      REFERENCE.lastIndex = 0
      let match
      while ((match = REFERENCE.exec(rawText)) !== null) {
        if (match.index < cursor || (escapedBracket && match.index < rawText.indexOf(']') + 1)) continue
        output += rawText.slice(cursor, match.index)
        const groups = { knowledge: match[1], connector: match[2], sourceStart: match[3] }
        if (groups.sourceStart) {
          const source = sourceAnnotation(rawText, match.index, match[0])
          const raw = restore(rawText.slice(match.index, source.end))
          const key = source.connector ? `mcp:${toolName(source.connector)}`
            : source.knowledge ? `knowledge:${source.knowledge.trim()}` : null
          const note = source.note?.replace(CODE, (_, index) => codes[index]?.text ?? _)
          output += key ? reference(key, raw, note) : raw
          cursor = source.end
        } else {
          output += convert(match[0], groups)
          cursor = match.index + match[0].length
        }
      }
      return restore(output + rawText.slice(cursor))
    }
    const walk = tokens => {
      const out = []
      for (let i = 0; i < tokens.length; i++) {
        const token = tokens[i]
        if (token.type === 'text' || token.type === 'codespan') {
          const escapedBracket = tokens[i - 1]?.type === 'escape' && tokens[i - 1].raw === '\\['
          const group = [token]
          while (['text', 'codespan'].includes(tokens[i + 1]?.type)) group.push(tokens[++i])
          out.push(rewriteText(group, escapedBracket))
        } else if (token.type === 'link') {
          const key = sourceUrl(token.href)
          out.push(key ? webLink(`web:${key}`, token.raw) : token.raw)
        } else if (['strong', 'em', 'del'].includes(token.type) && token.tokens) {
          const offset = token.raw.indexOf(token.text)
          out.push(offset < 0 ? token.raw : token.raw.slice(0, offset) + walk(token.tokens) + token.raw.slice(offset + token.text.length))
        } else out.push(token.raw)
      }
      return out.join('')
    }
    return walk(tokenizeInlineMarkdown(text))
  }, { protectInline: false }) + footer
}

/** 按本轮证据解析旧来源说明；同名不同文档或工具不做猜测。 */
export function resolveReferenceKey(key, candidateKeys, sources = null) {
  if (candidateKeys.includes(key)) return key
  if (key.startsWith('knowledge:') && sources) {
    const name = key.slice('knowledge:'.length).trim()
    const matches = candidateKeys.filter(candidate => {
      if (!/^\d+$/.test(candidate)) return false
      const item = sources.get(candidate)
      const documentName = String(item?.document_name || '').trim()
      const baseName = item?.metadata?.base_name
      return documentName && (name === documentName || (baseName && name === `${baseName}/${documentName}`))
    })
    // 同一文档的多个片段沿用首条证据；不同文档或知识库的同名资料保留原文。
    const identities = new Set(matches.map(candidate => {
      const item = sources.get(candidate)
      const base = item.base_id || item.metadata?.base_id || item.metadata?.base_name || ''
      return item.document_id ? JSON.stringify([base, item.document_id]) : candidate
    }))
    return identities.size === 1 ? matches[0] : key
  }
  if (!key.startsWith('mcp:')) return key
  const name = normalizeMcpReference(key.slice(4))
  const candidates = candidateKeys.filter(candidate => candidate.startsWith('mcp:'))
  const contains = (text, value) => {
    const escaped = value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    return new RegExp(`(^|[^\\p{L}\\p{N}_.\\/-])${escaped}($|[^\\p{L}\\p{N}_.\\/-])`, 'u').test(text)
  }
  // 完整路径优先，名称说明可在路径前后；未知完整路径不能退化成同名工具。
  const exact = candidates.filter(candidate => contains(name, normalizeMcpReference(candidate.slice(4))))
  if (exact.length) return exact.length === 1 ? exact[0] : key
  if (name.includes('/')) return key
  const matches = candidates.filter(candidate => contains(name, normalizeMcpReference(candidate.slice(4).split('/').at(-1))))
  if (matches.length) return matches.length === 1 ? matches[0] : key
  const servers = candidates.filter(candidate => contains(name, normalizeMcpReference(candidate.slice(4).split('/')[0])))
  return servers.length === 1 ? servers[0] : key
}

function normalizeMcpReference(value) {
  return value.replace(/`+/g, '').replace(/\s*\/\s*/g, '/').replace(/\s+/g, ' ').trim()
}
