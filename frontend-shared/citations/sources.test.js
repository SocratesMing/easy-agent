import assert from 'node:assert/strict'
import test from 'node:test'
import { citationPreviewText, externalSources, sourceUrl, toolResultText } from './sources.js'
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

test('旧式外部连接器来源说明可按唯一工具名匹配 MCP 数字引用', () => {
  const content = '贵金属K线表字典如下（来源：外部连接器 (precious_metal_qa)）：字段说明。'
  const call = { ...mcp, tool_name: 'precious_metal_qa',
    result: mcp.result.replace('finance/query', 'market/precious_metal_qa') }
  const { items, numberMap } = relatedEvidence({ content, blocks: [call] })
  assert.deepEqual(numberMap, { 'mcp:market/precious_metal_qa': 1 })
  assert.equal(items[0].source_type, 'mcp')
  const html = renderMarkdown(linkKnowledgeCitations(content, numberMap, true), createMarkdownRenderer({}))
  assert.match(html, /data-cite="1"/)
  assert.doesNotMatch(html, /来源：外部连接器|precious_metal_qa/)
})

test('同名 MCP 工具无法唯一匹配时保留旧来源文字，不误连引用', () => {
  const content = '来源：外部连接器（precious_metal_qa）'
  const first = { ...mcp, tool_call_id: 'mcp-1', result: mcp.result.replace('finance/query', 'market-a/precious_metal_qa') }
  const second = { ...mcp, tool_call_id: 'mcp-2', result: mcp.result.replace('finance/query', 'market-b/precious_metal_qa') }
  const result = relatedEvidence({ content, blocks: [first, second] })
  assert.deepEqual(result.numberMap, {})
  assert.equal(linkKnowledgeCitations(content, result.numberMap, true), content)
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
  const content = `\`示例：[连接器依据 finance/query]\`\n\n\`\`\`text\n[模型知识]\n[网站](${url})\n\`\`\`\n\n![图片](${url})\n\n\\[模型知识]\n\n[网站](${url}) [连接器依据 finance/query] [模型知识]`
  const message = { blocks: [web, mcp, { type: 'content', order: 4, content }] }
  const result = relatedEvidence(message)
  assert.deepEqual(result.numberMap, { [`web:${url}`]: 1, 'mcp:finance/query': 2 })
  const converted = linkKnowledgeCitations(content, result.numberMap, true)
  assert.ok(converted.includes(`![图片](${url})`))
  assert.ok(converted.includes('\\[模型知识]'))
  assert.ok(converted.includes('`示例：[连接器依据 finance/query]`'))
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

test('MCP 行内引用兼容完整标记和路径的反引号，按出现顺序编号', () => {
  const cases = [
    '`[连接器依据 finance/query]`',
    '``[连接器依据 finance/query]``',
    '[连接器依据 `finance/query`]',
    '**[连接器依据 `finance/query`]**',
    '[连接器依据\nfinance/query]',
  ]
  for (const marker of cases) {
    const content = `正文${marker}。[网站](${url})`
    const { numberMap, items } = relatedEvidence({ content, blocks: [mcp, web] })
    assert.equal(numberMap['mcp:finance/query'], 1, marker)
    assert.equal(numberMap[`web:${url}`], 2, marker)
    assert.equal(items[0].snippet, '营业收入：100万元。')
    const html = renderMarkdown(linkKnowledgeCitations(content, numberMap, true), createMarkdownRenderer({}))
    assert.match(html, /data-cite="1"/)
    assert.doesNotMatch(html, /连接器依据|<code>/)
  }
})

test('MCP 来源文字兼容无内层括号、代码工具名及业务口径说明', () => {
  const call = { ...mcp, result: mcp.result.replace('finance/query', 'fx/forex_kline_qa') }
  const cases = [
    '行情要点（来源：外部连接器 forex_kline_qa，FXALL 渠道 MID 价，北京时间）',
    '行情要点(来源: 外部连接器 forex_kline_qa, FXALL 渠道 MID 价，北京时间)',
    '来源：外部连接器 `forex_kline_qa`，FXALL 渠道 MID 价，北京时间',
    '行情要点（来源：外部连接器（forex_kline_qa，FXALL 渠道 MID 价，北京时间））',
    '来源：外部连接器（`forex_kline_qa`，FXALL 渠道 MID 价，北京时间）',
  ]
  for (const content of cases) {
    const { numberMap, items } = relatedEvidence({ content, blocks: [call] })
    assert.deepEqual(numberMap, { 'mcp:fx/forex_kline_qa': 1 }, content)
    assert.match(items[0].citation_notes.join('\n'), /FXALL 渠道 MID 价，北京时间/)
    assert.match(items[0].snippet, /营业收入/)
    const html = renderMarkdown(linkKnowledgeCitations(content, numberMap, true), createMarkdownRenderer({}))
    assert.match(html, /data-cite="1"/)
    assert.doesNotMatch(html, /来源[:：]|forex_kline_qa|FXALL|北京时间|[（）()]/)
  }
})

test('MCP 兼容范围限定在有效来源，代码块、普通代码、转义和链接中的示例保留', () => {
  const examples = [
    '```text\n[连接器依据 finance/query]\n```',
    '    `[连接器依据 finance/query]`',
    '`示例：[连接器依据 finance/query]`',
    '`finance/query`',
    '`未闭合代码：[连接器依据 finance/query]',
    '``示例：[连接器依据 finance/query]``',
    '![示例 `[连接器依据 finance/query]`](https://unknown.example.com/image.png)',
    '\\[连接器依据 `finance/query`]',
    '[示例 `[连接器依据 finance/query]`](https://unknown.example.com)',
  ]
  for (const content of examples) {
    const { numberMap } = relatedEvidence({ content, blocks: [mcp] })
    assert.deepEqual(numberMap, {}, content)
    assert.equal(linkKnowledgeCitations(content, numberMap, true), content)
  }
  for (const content of ['`[连接器依据 finance/query]`', '[连接器依据 `finance/query`]',
    '来源：外部连接器 query，业务口径']) {
    for (const calls of [[], [{ ...mcp, success: false }], [mcp, { ...mcp, tool_call_id: 'other', result: mcp.result.replace('finance/query', 'other/query') }]]) {
      // 完整 server/tool 在两个服务同名时仍可精确引用；短工具名才需要唯一性。
      if (calls.length === 2 && !content.includes('外部连接器')) continue
      const { numberMap } = relatedEvidence({ content, blocks: calls })
      assert.deepEqual(numberMap, {})
      assert.equal(linkKnowledgeCitations(content, numberMap, true), content)
    }
  }
})

test('知识库正文中的书名来源转为数字引用，末尾来源说明和文档描述保留', () => {
  const content = '已根据知识库文档《DeepAgents_Guide.md》生成 Word 文档。\n\n**文档结构**（来源：知识库《DeepAgents_Guide.md》）：章节列表。\n\n来源：知识库（测试/DeepAgents_Guide.md）'
  const evidence = [{ document_id: 'guide', document_name: 'DeepAgents_Guide.md', snippet: 'DeepAgents 指南内容', metadata: { base_name: '测试' } }]
  const { items, numberMap } = relatedEvidence({ content, knowledge_evidence: evidence })
  assert.equal(numberMap['knowledge:DeepAgents_Guide.md'], 1)
  assert.deepEqual(items, [{ ...evidence[0], citation_notes: ['来源：知识库《DeepAgents_Guide.md》'] }])
  const html = renderMarkdown(linkKnowledgeCitations(content, numberMap, true), createMarkdownRenderer({}))
  assert.equal([...html.matchAll(/data-cite="1"/g)].length, 1)
  assert.doesNotMatch(html, /来源：知识库《/)
  assert.match(html, /已根据知识库文档《DeepAgents_Guide.md》/)
  assert.match(html, /来源：知识库（测试\/DeepAgents_Guide.md）/)
  assert.equal(linkKnowledgeCitations(content, numberMap), content)
})

test('知识库文档名与现有编号共用来源，混合 MCP 按正文顺序编号', () => {
  const content = '收入[连接器依据 finance/query]。文档结构（来源：知识库《Guide.md》）：说明。[知识依据2]'
  const evidence = [{ document_name: 'other.md' }, { document_id: 'guide', document_name: 'Guide.md', snippet: '指南' }]
  const { items, numberMap } = relatedEvidence({ content, blocks: [mcp], knowledge_evidence: evidence })
  assert.equal(numberMap['mcp:finance/query'], 1)
  assert.equal(numberMap['knowledge:Guide.md'], 2)
  assert.equal(numberMap[2], 2)
  assert.equal(items.length, 2)
  const html = renderMarkdown(linkKnowledgeCitations(content, numberMap, true), createMarkdownRenderer({}))
  assert.equal([...html.matchAll(/data-cite="2"/g)].length, 2)
})

test('知识库缺失或同名不同文档时不转换，带库名时须精确匹配', () => {
  const a = { document_id: 'a', document_name: 'Guide.md', snippet: '甲', metadata: { base_name: '知识库甲' } }
  const b = { document_id: 'b', document_name: 'Guide.md', snippet: '乙', metadata: { base_name: '知识库乙' } }
  for (const evidence of [[], [a, b]]) {
    const content = '文档结构（来源：知识库《Guide.md》）：章节'
    const { numberMap } = relatedEvidence({ content, knowledge_evidence: evidence })
    assert.deepEqual(numberMap, {})
    assert.equal(linkKnowledgeCitations(content, numberMap, true), content)
  }
  const content = '文档结构（来源：知识库《知识库甲/Guide.md》）'
  const { items, numberMap } = relatedEvidence({ content, knowledge_evidence: [a, b] })
  assert.equal(numberMap['knowledge:知识库甲/Guide.md'], 1)
  assert.deepEqual(items, [{ ...a, citation_notes: ['来源：知识库《知识库甲/Guide.md》'] }])
  const wrongBase = relatedEvidence({ content, knowledge_evidence: [b] })
  assert.deepEqual(wrongBase.numberMap, {})
  const chunks = relatedEvidence({ content, knowledge_evidence: [a, { ...a, snippet: '另一片段' }] })
  assert.equal(chunks.items.length, 1)
  assert.equal(chunks.numberMap['knowledge:知识库甲/Guide.md'], 1)
})

test('知识库末尾来源文字、代码与链接中的来源示例保持原文', () => {
  for (const content of [
    '来源：知识库《Guide.md》',
    '来源：知识库（测试/Guide.md）',
    '`（来源：知识库《Guide.md》）`',
    '```text\n正文（来源：知识库《Guide.md》）\n```',
    '[（来源：知识库《Guide.md》）](https://example.com)',
  ]) {
    const { numberMap } = relatedEvidence({ content, knowledge_evidence: [{ document_name: 'Guide.md' }] })
    assert.deepEqual(numberMap, {}, content)
    assert.equal(linkKnowledgeCitations(content, numberMap, true), content)
  }
})


test('完整来源括注收进浮层：中英文嵌套括号、反引号和知识库口径不残留在正文', () => {
  const call = { ...mcp, result: mcp.result.replace('finance/query', 'fx/forex_kline_qa') }
  const evidence = [{ document_id: 'guide', document_name: 'Guide.md', snippet: '指南正文' }]
  const cases = [
    ['行情要点（来源：外部连接器 `forex_kline_qa`，FXALL 渠道 MID 价（北京时间））：后续正文', '来源：外部连接器 forex_kline_qa，FXALL 渠道 MID 价（北京时间）'],
    ['行情要点(来源: 外部连接器 (forex_kline_qa), FXALL MID (UTC+8))：后续正文', '来源: 外部连接器 (forex_kline_qa), FXALL MID (UTC+8)'],
    ['**行情要点（来源：外部连接器 forex_kline_qa，FXALL MID，北京时间）**：后续正文', '来源：外部连接器 forex_kline_qa，FXALL MID，北京时间'],
    ['文档结构（来源：知识库《Guide.md》，第二章（修订版））：后续正文', '来源：知识库《Guide.md》，第二章（修订版）'],
  ]
  for (const [content, note] of cases) {
    const message = { content, blocks: [call], knowledge_evidence: evidence }
    const { numberMap, items } = relatedEvidence(message)
    assert.deepEqual(items[0].citation_notes, [note], content)
    const html = renderMarkdown(linkKnowledgeCitations(content, numberMap, true), createMarkdownRenderer({}))
    assert.match(html, /data-cite="1"/)
    assert.match(html, /：后续正文/)
    assert.doesNotMatch(html, /来源[:：]|FXALL|北京时间|Guide\.md|修订版|[（）()]/)
    assert.equal(message.knowledge_evidence[0].citation_notes, undefined)
  }
})

test('重复来源说明去重，流式闭合后收起完整括注并保持编号和工具结果', () => {
  const note = '来源：外部连接器 finance/query，统计口径（北京时间）'
  const first = { type: 'content', order: 1, content: `收入（${note}）` }
  const second = { type: 'content', order: 2, content: `利润（${note}）及成本（来源：外部连接器 finance/query，另一口径）` }
  const result = relatedEvidence({ blocks: [mcp, second, first] })
  assert.deepEqual(result.numberMap, { 'mcp:finance/query': 1 })
  assert.deepEqual(result.items[0].citation_notes, [note, '来源：外部连接器 finance/query，另一口径'])
  assert.equal(result.items[0].snippet, '营业收入：100万元。')
  const partial = '收入（来源：外部连接器 finance/query，统计口径（北京时间）'
  const pending = relatedEvidence({ content: partial, blocks: [mcp] })
  assert.deepEqual(pending.numberMap, {})
  assert.equal(linkKnowledgeCitations(partial, pending.numberMap, true), partial)
  const complete = relatedEvidence({ content: partial + '）', blocks: [mcp] })
  assert.equal(complete.numberMap['mcp:finance/query'], 1)
})

test('未知来源、普通业务括号与未闭合来源括注逐字保留', () => {
  for (const content of [
    '行情（北京时间）[连接器依据 finance/query]（非来源备注）',
    '行情（来源：外部连接器 unknown/query，FXALL MID（北京时间））',
    '结构（来源：知识库《Missing.md》，修订版）',
    '行情（来源：外部连接器 finance/query，尚未闭合',
  ]) {
    const result = relatedEvidence({ content, blocks: [mcp] })
    const converted = linkKnowledgeCitations(content, result.numberMap, true)
    if (content.includes('[连接器依据')) {
      assert.ok(converted.startsWith('行情（北京时间）'))
      assert.ok(converted.endsWith('（非来源备注）'))
    } else assert.equal(converted, content)
  }
})


test('浮层内容包含完整来源说明与原始证据，且不修改已保存的消息', () => {
  const content = '行情要点（来源：外部连接器 finance/query，FXALL MID，北京时间）'
  const message = { blocks: [mcp, { type: 'content', content }], content }
  const saved = JSON.stringify(message)
  const result = relatedEvidence(message)
  assert.equal(citationPreviewText(result.items[0]), '来源：外部连接器 finance/query，FXALL MID，北京时间\n\n营业收入：100万元。')
  assert.equal(citationPreviewText({ snippet: '只有证据' }), '只有证据')
  assert.equal(citationPreviewText({ citation_notes: ['只有说明'] }), '只有说明')
  assert.equal(JSON.stringify(message), saved)
})
