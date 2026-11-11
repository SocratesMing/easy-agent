import { protectCodeRegions } from './codeRegions.js'
import { citationMarker } from './markers.js'

const CITATION = /\[知识(?:库)?依据\s*(\d+)\]/g

/** 主聊天：正文中的有效编号按首次出现排序，代码里的示例不计入。 */
export function relatedEvidence(message) {
  const evidence = message.knowledge_evidence || []
  const blocks = (message.blocks || []).filter(block => block.type === 'content')
  const text = blocks.length
    ? [...blocks].sort((a, b) => (a.order || 0) - (b.order || 0)).map(block => block.content || '').join('\n')
    : message.content || ''
  const numbers = new Set()
  protectCodeRegions(text, part => {
    for (const match of part.matchAll(CITATION)) {
      const number = Number(match[1])
      if (number > 0 && number <= evidence.length) numbers.add(number)
    }
    return part
  })
  return {
    numberMap: Object.fromEntries([...numbers].map((number, index) => [number, index + 1])),
    items: numbers.size ? [...numbers].map(number => evidence[number - 1]) : evidence,
  }
}

export function linkKnowledgeCitations(content, citeMap, compact = false) {
  if (typeof content !== 'string') return content
  const map = citeMap || {}
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
