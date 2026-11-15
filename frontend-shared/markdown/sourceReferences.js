import { tokenizeInlineMarkdown } from './index.js'
import { protectCodeRegions } from './codeRegions.js'
import { sourceUrl } from '../citations/sources.js'

const REFERENCE = /\[知识(?:库)?依据\s*(\d+)\]|\[连接器依据\s+([^\]\n]+)\]|\[模型知识\]/g

/** 同一遍扫描用于分配编号与渲染，避免混合来源在表格、强调或代码中错位。 */
export function rewriteReferences(content, reference, webLink) {
  return protectCodeRegions(content, text => {
    const walk = tokens => tokens.map(token => {
      if (token.type === 'link') {
        const key = sourceUrl(token.href)
        return key ? webLink(`web:${key}`, token.raw) : token.raw
      }
      if (['strong', 'em', 'del'].includes(token.type) && token.tokens) {
        const offset = token.raw.indexOf(token.text)
        if (offset < 0) return token.raw
        return token.raw.slice(0, offset) + walk(token.tokens) + token.raw.slice(offset + token.text.length)
      }
      if (token.type !== 'text') return token.raw
      return token.raw.replace(REFERENCE, (raw, knowledge, connector) => {
        // 兼容已生成的旧消息：撤销的模型知识标记不再展示或分配引用编号。
        if (!knowledge && !connector) return ''
        const key = knowledge ? String(Number(knowledge)) : `mcp:${connector.trim()}`
        return reference(key, raw)
      })
    }).join('')
    return walk(tokenizeInlineMarkdown(text))
  })
}
