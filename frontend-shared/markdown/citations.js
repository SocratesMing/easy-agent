import { protectCodeRegions } from './codeRegions.js'
import { citationMarker } from './markers.js'
import { externalSources } from '../citations/sources.js'
import { resolveReferenceKey, rewriteReferences } from './sourceReferences.js'

const CITATION = /\[知识(?:库)?依据\s*(\d+)\]/g

/** 主聊天：正文中的有效编号按首次出现排序，代码里的示例不计入。 */
export function relatedEvidence(message) {
  const evidence = message.knowledge_evidence || []
  const blocks = (message.blocks || []).filter(block => block.type === 'content')
  const text = blocks.length
    ? [...blocks].sort((a, b) => (a.order || 0) - (b.order || 0)).map(block => block.content || '').join('\n')
    : message.content || ''
  const sources = externalSources(message)
  evidence.forEach((item, index) => sources.set(String(index + 1), item))
  const numbers = new Set()
  const aliases = new Map()
  const collect = (key, raw) => {
    const resolved = resolveReferenceKey(key, [...sources.keys()], sources)
    if (sources.has(resolved)) {
      numbers.add(resolved)
      if (key.startsWith('knowledge:') && key !== resolved) aliases.set(key, resolved)
    }
    return raw
  }
  rewriteReferences(text, collect, collect)
  const numberMap = Object.fromEntries([...numbers].map((key, index) => [key, index + 1]))
  for (const [alias, key] of aliases) numberMap[alias] = numberMap[key]
  return {
    numberMap,
    items: numbers.size ? [...numbers].map(key => sources.get(key)) : evidence,
  }
}

export function linkKnowledgeCitations(content, citeMap, compact = false) {
  if (typeof content !== 'string') return content
  const map = citeMap || {}
  if (compact) {
    const replace = (key, raw) => {
      const resolved = resolveReferenceKey(key, Object.keys(map))
      return map[resolved] ? citationMarker(map[resolved], true) : raw
    }
    return rewriteReferences(content, replace, replace)
  }
  return protectCodeRegions(content, text =>
    text.replace(CITATION, (match, digits) => {
      const number = Number(digits)
      const display = map[number]
      if (compact && !display) return match
      return citationMarker(display || number, compact)
    })
  )
}

/** 工作台保留既有的文字标记编号规则。 */
export function inlineCitationMap(content, evidenceCount) {
  if (!evidenceCount) return {}
  const seen = new Set()
  for (const match of (content || '').matchAll(CITATION)) {
    const number = Number(match[1])
    if (number > 0 && number <= evidenceCount) seen.add(number)
  }
  return Object.fromEntries([...seen].map((number, index) => [number, index + 1]))
}
