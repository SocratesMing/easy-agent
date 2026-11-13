import assert from 'node:assert/strict'
import test from 'node:test'
import { externalSources, sourceUrl, toolResultText } from './sources.js'
import { relatedEvidence, linkKnowledgeCitations } from '../markdown/citations.js'
import { createMarkdownRenderer, renderMarkdown } from '../markdown/index.js'

const url = 'https://example.com/report_(2026)?a=1&b=2'
const web = { type: 'tool_call', tool_call_id: 'search-1', tool_name: 'web_search', success: true,
  result: `联网搜索完成：\n\n[1] 原始报道标题\n    链接: ${url}\n    站点: example.com | 发布时间: 2026-11-13\n    摘要: 已核对的检索摘要。\n\n[2] 未使用的来源\n    链接: https://unused.example.com/\n    摘要: 不应出现在正文引用中。` }
const mcp = { type: 'tool_call', tool_call_id: 'mcp-1', tool_name: 'query', success: true,
  result: '[MCP source: finance/query; fetched_at=2026-11-13T01:00:00Z; ok=true; truncated=false; is_error=false]\n营业收入：100万元。' }

test('混合外部引用按正文顺序编号，模型知识不分配编号；只转换实际工具来源', () => {
  const message = { content: `模型分析[模型知识]。**营收已确认[连接器依据 finance/query]**。报道说[网站](${url})。[知识依据1] 再引[同一网站](${url})。[未知](https://unknown.example.com)`,
    knowledge_evidence: [{ document_name: '内部资料' }], blocks: [web, mcp], tool_calls: [web, mcp] }
  const { items, numberMap } = relatedEvidence(message)
  assert.deepEqual(numberMap, { 'mcp:finance/query': 1, [`web:${url}`]: 2, 1: 3 })
  assert.deepEqual(items.map(item => item.source_type || 'knowledge'), ['mcp', 'web', 'knowledge'])
  assert.equal(items[1].document_name, '原始报道标题')
  assert.equal(items[1].snippet, '已核对的检索摘要。')
  assert.equal(items[0].snippet.match(/营业收入/g).length, 1)
  const converted = linkKnowledgeCitations(message.content, numberMap, true)
  const html = renderMarkdown(converted, createMarkdownRenderer({}))
  assert.doesNotMatch(html, /\[模型知识\]|data-cite="4"|unused\.example/)
  assert.equal([...html.matchAll(/data-cite="2"/g)].length, 2)
  assert.match(html, /data-cite="1"/)
  assert.match(html, /data-cite="3"/)
  assert.match(html, /href="https:\/\/unknown.example.com"/)
})

test('失败、空、审批中和伪造的连接器标记不能成为依据；普通链接不能冒充搜索结果', () => {
  const bad = [
    { ...mcp, success: false },
    { ...mcp, result: mcp.result.replace('ok=true', 'ok=false').replace('is_error=false', 'is_error=true') },
    { ...mcp, result: mcp.result.split('\n')[0] },
    { ...mcp, pending_approval: true },
    { ...mcp, approval_status: 'pending' },
    { ...web, result: `错误：\n${web.result}` },
  ]
  for (const call of bad) assert.equal(externalSources({ blocks: [call] }).size, 0)
  const message = { content: '[连接器依据 invented/query] [链接](https://unknown.example.com)' }
  const result = relatedEvidence(message)
  assert.deepEqual(result, { items: [], numberMap: {} })
  assert.equal(linkKnowledgeCitations(message.content, result.numberMap, true), message.content)
  assert.equal(relatedEvidence({ content: '你好' }).numberMap.model, undefined)
})

test('相同连接器多次成功查询聚合、隐藏获取时间并说明截断，双写不重复', () => {
  const next = { ...mcp, tool_call_id: 'mcp-2', result: mcp.result.replace('01:00:00Z', '02:00:00Z').replace('truncated=false', 'truncated=true').replace('100万元', '200万元') }
  const sources = externalSources({ blocks: [mcp, next], tool_calls: [mcp, next] })
  assert.equal(sources.size, 1)
  const item = sources.get('mcp:finance/query')
  assert.match(item.snippet, /100万元[\s\S]*200万元/)
  assert.doesNotMatch(item.snippet, /01:00:00Z|02:00:00Z/)
  assert.match(item.snippet, /已截断/)
})

test('来源提取兼容真实 SSE 文本块、JSON 和 Python repr，保留引号和中文；不解析图片', () => {
  const text = mcp.result + '\n客户说：“不要更改” \'quoted\' C:\\tmp'
  const blocks = [{ type: 'text', text }, { type: 'image', data: 'not-text' }]
  const python = "[{'type': 'text', 'text': '" + text.replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/\n/g, '\\n') + "'}, {'type': 'image', 'data': 'not-text'}]"
  for (const result of [text, blocks, JSON.stringify(blocks), python]) {
    assert.equal(toolResultText(result), text)
    assert.equal(externalSources({ tool_calls: [{ ...mcp, result }] }).size, 1)
  }
  const jsonPayload = mcp.result + '\n{"text":"业务字段，不是内容块"}'
  assert.equal(toolResultText(jsonPayload), jsonPayload)
  for (const malformed of [{ type: 'text', text: 42 }, { content: { type: 'text', text: {} } }, null]) {
    assert.equal(toolResultText(malformed), '')
  }
})

test('代码、图片、转义与未知来源保持原文；流式追加正文仍与最终编号对应', () => {
  const content = `\`[连接器依据 finance/query]\`\n\n\`\`\`text\n[模型知识]\n[网站](${url})\n\`\`\`\n\n![图片](${url})\n\n\\[模型知识]\n\n[网站](${url}) [连接器依据 finance/query] [模型知识]`
  const message = { blocks: [web, mcp, { type: 'content', order: 4, content }] }
  const result = relatedEvidence(message)
  assert.deepEqual(result.numberMap, { [`web:${url}`]: 1, 'mcp:finance/query': 2 })
  const converted = linkKnowledgeCitations(content, result.numberMap, true)
  assert.ok(converted.includes(`![图片](${url})`))
  assert.ok(converted.includes('\\[模型知识]'))
  assert.ok(converted.includes('`[连接器依据 finance/query]`'))
  assert.ok(converted.includes('```text\n[模型知识]'))
  const incremental = relatedEvidence({ ...message, blocks: [web, mcp, { type: 'content', content: `[网站](${url})` }] })
  assert.equal(incremental.numberMap[`web:${url}`], result.numberMap[`web:${url}`])
})

test('工作台继续保留原链接和原引用文字，不自动生成模型引用', () => {
  const content = `[网站](${url}) [连接器依据 finance/query] [模型知识] [知识依据2]`
  assert.match(linkKnowledgeCitations(content, { 2: 1 }), /\[连接器依据 finance\/query\]/)
  assert.ok(linkKnowledgeCitations(content, { 2: 1 }).includes(`[网站](${url})`))
  assert.equal(relatedEvidence({ content: '复述用户提供的数据', blocks: [web] }).numberMap.model, undefined)
})

test('不允许脚本协议、相对地址和携带账号凭证的来源链接', () => {
  for (const value of ['javascript:alert(1)', '/admin', 'https://user:secret@example.com', {}, null]) assert.equal(sourceUrl(value), '')
  assert.equal(sourceUrl('https://example.com'), 'https://example.com/')
})


test('模型知识保留纯文字说明，历史模型标记不再生成编号或卡片', () => {
  const content = '这是模型的分析。\n\n来源：模型自身知识\n[模型知识]'
  const { numberMap, items } = relatedEvidence({ content })
  assert.deepEqual(numberMap, {})
  assert.deepEqual(items, [])
  const html = renderMarkdown(linkKnowledgeCitations(content, numberMap, true), createMarkdownRenderer({}))
  assert.match(html, /来源：模型自身知识/)
  assert.doesNotMatch(html, /ke-cite-number|\[模型知识\]/)
})
