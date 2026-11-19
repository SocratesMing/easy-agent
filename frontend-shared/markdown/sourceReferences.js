import { tokenizeInlineMarkdown } from './index.js'
import { protectCodeRegions } from './codeRegions.js'
import { sourceUrl } from '../citations/sources.js'

const CODE = /\u0000EAI(\d+)\u0000/g
const TOOL = /^(?:[\w.-]+\/)*[\w.-]+$/
const REFERENCE = new RegExp([
  /\[知识(?:库)?依据\s*(?<knowledge>\d+)\]/.source,
  /\[连接器依据\s+(?<connector>[^\]\n]+)\]/.source,
  /(?:（|\()\s*来源\s*[:：]\s*外部连接器\s*[（(]\s*(?<wrapped>[^）)\n]+?)\s*[）)]\s*(?:）|\))?/.source,
  /来源\s*[:：]\s*外部连接器\s*[（(]\s*(?<plain>[^）)\n]+?)\s*[）)]/.source,
  /来源\s*[:：]\s*外部连接器\s*(?<bare>(?:[\w.-]+\/)*[\w.-]+|\u0000EAI\d+\u0000)(?=[\s，,；;）)。]|$)/.source,
  /[（(]\s*来源\s*[:：]\s*知识库\s*《(?<knowledgeTitle>[^》\n]+)》\s*[）)]/.source,
  /\[模型知识\]/.source,
  CODE.source,
].join('|'), 'g')

/** 同一遍扫描用于分配编号与渲染；围栏/缩进代码仍整体保护。 */
export function rewriteReferences(content, reference, webLink) {
  return protectCodeRegions(content, text => {
    const rewriteText = tokens => {
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
      return rawText.replace(REFERENCE, (...args) => {
        const match = args[0]
        const groups = args.at(-1)
        const raw = restore(match)
        if (groups.knowledge) return reference(String(Number(groups.knowledge)), raw)
        if (groups.knowledgeTitle) return reference(`knowledge:${groups.knowledgeTitle.trim()}`, raw)
        if (groups.connector) return reference(`mcp:${toolName(groups.connector)}`, raw)
        const legacy = groups.wrapped || groups.plain || groups.bare
        if (legacy) {
          const [name, ...details] = legacy.split(/[,，]/)
          const replacement = reference(`mcp:${toolName(name)}`, raw)
          // 只有来源被成功转换才重组说明；业务口径保留，未知/歧义来源逐字保留。
          return replacement !== raw && details.length
            ? `${replacement}（${restore(details.join('，')).trim()}）` : replacement
        }
        if (match === '[模型知识]') return ''
        const code = codes[Number(match.match(/\d+/)[0])]
        if (!code) return match
        const marker = code.text.trim().match(/^\[连接器依据\s+([^\]\n]+)\]$/)
        return marker && TOOL.test(marker[1].trim())
          ? reference(`mcp:${marker[1].trim()}`, code.raw) : code.raw
      })
    }
    const walk = tokens => {
      const out = []
      for (let i = 0; i < tokens.length; i++) {
        const token = tokens[i]
        if (token.type === 'text' || token.type === 'codespan') {
          const group = [token]
          while (['text', 'codespan'].includes(tokens[i + 1]?.type)) group.push(tokens[++i])
          out.push(rewriteText(group))
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
  }, { protectInline: false })
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
  const name = key.slice(4).trim()
  const matches = candidateKeys.filter(candidate => candidate.startsWith('mcp:') && candidate.slice(4).split('/').at(-1) === name)
  return matches.length === 1 ? matches[0] : key
}
