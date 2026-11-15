// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/citations/sources.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
/** 从已保存的工具结果恢复来源；不把工具调用本身当作正文已引用的证据。 */
export function sourceUrl(value) {
  if (typeof value !== 'string') return ''
  try {
    const url = new URL(value)
    return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url.href : ''
  } catch { return '' }
}

function decodeText(value) {
  return value.replace(/\\(u[\da-f]{4}|x[\da-f]{2}|[nrt\\'"])/gi, (_, code) => {
    if (/^[ux]/i.test(code)) return String.fromCharCode(parseInt(code.slice(1), 16))
    return ({ n: '\n', r: '\r', t: '\t' })[code] || code
  })
}

/** SSE/历史数据可能是文本、内容块、JSON，或 Python 内容块列表的 repr；绝不 eval。 */
export function toolResultText(result) {
  if (Array.isArray(result)) return result.filter(part => typeof part === 'string' || (part?.type === 'text' && typeof part.text === 'string')).map(part => typeof part === 'string' ? part : part.text).join('\n')
  if (result && typeof result === 'object') return result.type === 'text' ? (typeof result.text === 'string' ? result.text : '') : toolResultText(result.content || [])
  if (typeof result !== 'string') return ''
  if (/^\s*(?:\[\s*\{|\{)/.test(result)) {
    try {
      const parsed = JSON.parse(result)
      if (parsed && typeof parsed === 'object') return toolResultText(parsed)
    } catch { /* 非 JSON：兼容后端 str(ToolMessage.content) 的文本块格式。 */ }
    const texts = [...result.matchAll(/(['"])text\1\s*:\s*(['"])((?:\\.|(?!\2)[^\\])*)\2/g)]
    if (texts.length) return texts.map(match => decodeText(match[3])).join('\n')
  }
  return result
}

function webSources(text) {
  const items = []
  for (const section of text.split(/(?=^\[\d+\] )/m)) {
    const title = section.match(/^\[\d+\] (.+)/)?.[1]
    const url = sourceUrl(section.match(/^\s+链接:\s*(\S+)/m)?.[1])
    if (!title || !url || section.includes('该页是列表/频道/关键词聚合页')) continue
    const published = section.match(/发布时间:\s*([^\n|]+)/)?.[1]?.trim() || ''
    items.push({ key: `web:${url}`, source_type: 'web', url, document_name: title,
      snippet: section.match(/^\s+摘要:\s*([^\n]+)/m)?.[1] || '',
      metadata: { base_name: new URL(url).hostname, publish_date: published } })
  }
  return items
}

export function externalSources(message) {
  const sources = new Map()
  // blocks 是流式与历史的共同载体；tool_calls 为旧数据和部分恢复场景兜底。
  const blocks = Array.isArray(message.blocks) ? message.blocks : []
  const calls = [...blocks.filter(block => block?.type === 'tool_call'), ...(Array.isArray(message.tool_calls) ? message.tool_calls : [])]
  const seen = new Set()
  for (const call of calls) {
    if (!call || typeof call !== 'object') continue
    if (call.success === false || call.streaming || call.pending_approval || call.approval_status === 'pending') continue
    const text = toolResultText(call.result)
    if (!text.trim()) continue
    const name = call.tool_name || call.name || ''
    const identity = call.tool_call_id || call.id || `${name}:${text}`
    if (seen.has(identity)) continue
    seen.add(identity)
    const header = text.match(/^\[MCP source: ([^;\]\n]+); fetched_at=([^;\]\n]+); ok=true; truncated=(true|false); is_error=false\]\s*/)
    if (header) {
      const key = `mcp:${header[1]}`
      const snippet = text.slice(header[0].length).trim()
      if (!snippet) continue
      const previous = sources.get(key)
      const part = `${snippet}${header[3] === 'true' ? '\n（工具返回内容已截断）' : ''}`
      sources.set(key, { key, source_type: 'mcp', document_name: header[1],
        snippet: previous ? `${previous.snippet}\n\n${part}` : part,
        metadata: { base_name: header[1].split('/')[0], fetched_at: header[2] } })
    } else if (name === 'web_search' && !/^(?:错误[:：]|Error\b)/i.test(text.trim())) {
      for (const item of webSources(text)) if (!sources.has(item.key)) sources.set(item.key, item)
    }
  }
  return sources
}

export function sourceLabel(item) {
  return ({ web: '互联网', mcp: 'MCP' })[item.source_type] || '知识库'
}
