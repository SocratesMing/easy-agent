// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/chat/messageSegments.test.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
import assert from 'node:assert/strict'
import test from 'node:test'
import {
  buildMessageSegments,
  describeProcessGroup,
  isNarrationText,
  processGroupRows,
  processLivePreview,
  processSummaryText,
  summarizeProcessGroup,
  toolActionLabel,
} from './messageSegments.js'

const think = (step, extra = {}) => ({ type: 'thinking', content: `思考${step}`, duration: 2, step, ...extra })
const say = (step, content) => ({ type: 'content', content, step })
const tool = (step, tool_name, args = {}, extra = {}) => ({
  type: 'tool_call', tool_name, arguments: args, result: 'ok', success: true, duration: 0.3, step, ...extra,
})
const shape = (segments) => segments.map((s) => (s.kind === 'text'
  ? `text:${s.block.origIndex}`
  : `process:${s.items.map((i) => (i.narration ? `n${i.origIndex}` : i.origIndex)).join(',')}`))

test('纯正文回复只有一个正文段', () => {
  assert.deepEqual(shape(buildMessageSegments([say(0, '你好')])), ['text:0'])
})

test('思考后直接回答：思考成组，回答在其后', () => {
  assert.deepEqual(shape(buildMessageSegments([think(0), say(0, '答案')])), ['process:0', 'text:1'])
})

test('工具前的短叙述并入过程组，表格这类实质内容打断分组，最终回答保持正文', () => {
  const table = '共 3 个区域：\n\n| 区域 | 金额 |\n| --- | --- |\n| 华东 | 128 |'
  const blocks = [
    think(0), say(0, '我先看一下表格结构。'), tool(0, 'read_file', { file_path: '/w/sales.xlsx' }),
    think(1), say(1, table), tool(1, 'write_file', { file_path: '/w/周报.docx' }),
    think(2), say(2, '周报已生成。'),
  ]
  assert.deepEqual(shape(buildMessageSegments(blocks)), ['process:0,n1,2,3', 'text:4', 'process:5,6', 'text:7'])
})

test('连续多步的短叙述都不打断分组：长任务只占一个过程组', () => {
  const blocks = []
  for (let step = 0; step < 10; step += 1) {
    blocks.push(think(step), say(step, `第 ${step} 步说明。`), tool(step, 'execute', { command: `run ${step}` }))
  }
  blocks.push(say(10, '全部完成。'))
  const segments = buildMessageSegments(blocks)
  assert.equal(segments.length, 2)
  assert.equal(segments[0].kind, 'process')
  assert.equal(segments[0].items.length, 30)
  assert.deepEqual(shape(segments).slice(1), ['text:30'])
})

test('流式中最后一段正文即使很短也按正文显示（它可能就是最终回答）', () => {
  const blocks = [tool(0, 'read_file'), say(1, '好的')]
  assert.deepEqual(shape(buildMessageSegments(blocks)), ['process:0', 'text:1'])
  // 后面出现工具调用后，同一段才被认定为叙述并入组
  assert.deepEqual(shape(buildMessageSegments([...blocks, tool(1, 'execute')])), ['process:0,n1,2'])
})

test('短句后面紧跟正文（不是思考 / 工具）时按正文显示，不会单独成组', () => {
  const blocks = [say(0, '先说结论。'), say(1, '这'.repeat(90)), tool(1, 'read_file')]
  assert.deepEqual(shape(buildMessageSegments(blocks)), ['text:0', 'text:1', 'process:2'])
})

test('超长叙述按正文显示并打断分组', () => {
  const long = '这'.repeat(81)
  assert.deepEqual(shape(buildMessageSegments([tool(0, 'ls'), say(1, long), tool(1, 'ls')])), ['process:0', 'text:1', 'process:2'])
  assert.deepEqual(
    shape(buildMessageSegments([tool(0, 'ls'), say(1, long), tool(1, 'ls')], { narrationMaxChars: 100 })),
    ['process:0,n1,2'],
  )
})

test('隐藏工具与空白正文不展示，也不打断分组', () => {
  const blocks = [
    tool(0, 'read_file'),
    tool(0, 'write_todos'),
    tool(0, 'ask_user'),
    say(1, '\n  '),
    tool(1, 'grep'),
  ]
  assert.deepEqual(shape(buildMessageSegments(blocks)), ['process:0,4'])
})

test('组的 key 取首块下标，流式追加时不变', () => {
  // 开头用长正文：短句会被当成叙述并入后面的组
  const blocks = [say(0, `先说明一下：${'这'.repeat(90)}`), tool(0, 'read_file')]
  const before = buildMessageSegments(blocks)
  const after = buildMessageSegments([...blocks, think(1), tool(1, 'execute'), say(2, '完成')])
  assert.equal(before[1].key, 'g1')
  assert.equal(after[1].key, 'g1')
  assert.equal(after[0].key, 'c0')
})

test('叙述判定：块级 Markdown、图片、多段或多行都算实质内容', () => {
  assert.equal(isNarrationText('我先看一下表格结构。'), true)
  assert.equal(isNarrationText('读取 `config.yaml` 确认 **端口**'), true)
  assert.equal(isNarrationText('第一行\n第二行'), true)
  for (const text of [
    '', '   ', '| a | b |', '```js\nx\n```', '# 标题', '> 引用', '- 列表项', '1. 列表项',
    '![图](a.png)', '第一段\n\n第二段', '一\n二\n三',
  ]) {
    assert.equal(isNarrationText(text), false, JSON.stringify(text))
  }
})

test('摘要按首次出现排序；文件类按涉及的文件计数，其余按调用次数', () => {
  const items = [
    think(0, { duration: 2.4 }),
    tool(0, 'read_file', { file_path: '/w/a.md' }),
    tool(0, 'read_file', { file_path: '/w/a.md' }),
    tool(0, 'read_file', { file_path: '/w/b.md' }),
    tool(1, 'execute', { command: 'ls' }, { success: false }),
    tool(1, 'execute', { command: 'ls -la' }),
    think(1, { duration: 3.3 }),
    tool(2, 'mcp_query', {}),
  ]
  const summary = summarizeProcessGroup(items)
  assert.equal(summary.toolCount, 6)
  assert.equal(summary.failedCount, 1)
  assert.equal(processSummaryText(summary), '已思考 6 秒 · 读取 2 个文件 · 执行 2 条命令 · 调用工具 1 次')
})

test('思考没有用时（老数据）只写「已思考」；不足一秒记为 1 秒', () => {
  assert.equal(processSummaryText(summarizeProcessGroup([think(0, { duration: null })])), '已思考')
  assert.equal(processSummaryText(summarizeProcessGroup([think(0, { duration: 0.2 })])), '已思考 1 秒')
})

test('工具动作描述', () => {
  assert.equal(toolActionLabel(tool(0, 'execute', { command: 'pytest -q\necho done' })), '执行 pytest -q')
  assert.equal(toolActionLabel(tool(0, 'execute', {})), '执行 命令')
  assert.equal(toolActionLabel(tool(0, 'read_file', { file_path: '/w/data/sales.xlsx' })), '读取 sales.xlsx')
  assert.equal(toolActionLabel(tool(0, 'read_uploaded_file', { filename: '合同.pdf' })), '读取 合同.pdf')
  assert.equal(toolActionLabel(tool(0, 'delete', { file_path: '/w/tmp.txt' })), '删除 tmp.txt')
  assert.equal(toolActionLabel(tool(0, 'grep', { pattern: 'TODO' })), '搜索 TODO')
  assert.equal(toolActionLabel(tool(0, 'web_search', { query: '汇率' })), '联网搜索 汇率')
  assert.equal(toolActionLabel(tool(0, 'some_mcp_tool')), '调用 some_mcp_tool')
})

test('执行中的组：标题跟随当前动作，工具执行时带上叙述', () => {
  const running = (items) => describeProcessGroup({ items }, { running: true })
  const narration = { ...say(1, '按区域汇总数据。'), narration: true }
  const exec = tool(1, 'execute', { command: 'python summarize.py' }, { duration: null, result: '' })

  assert.equal(running([think(0, { duration: null })]).title, '正在思考…')
  assert.equal(running([think(1), narration, exec]).title, '按区域汇总数据 · 正在执行 python summarize.py…')
  // 工具已结束、下一步还没开始
  assert.equal(running([narration, tool(1, 'execute')]).title, '按区域汇总数据 · 正在处理…')
  // 叙述只挂在它对应的工具上：下一步的思考开始后不再显示
  assert.equal(running([narration, tool(1, 'execute'), think(2, { duration: null })]).title, '正在思考…')
  // 前一步的叙述不能串到后续工具；只有紧邻当前工具的叙述才作为标题说明
  assert.equal(
    running([narration, tool(1, 'read_file', { file_path: 'sales.xlsx' }), exec]).title,
    '正在执行 python summarize.py…',
  )
})

test('待审批优先于执行中；完成后是摘要；只有思考的组标记 thinkingOnly', () => {
  const pending = tool(0, 'delete', { file_path: '/w/old.txt' }, { duration: null, pending_approval: true })
  const view = describeProcessGroup({ items: [think(0), pending] }, { running: true })
  assert.equal(view.status, 'approval')
  assert.equal(view.title, '等待审批 · 删除 old.txt')
  assert.deepEqual(view.pinned.map((i) => i.tool_name), ['delete'])
  assert.equal(view.preview, '')

  // 作答后残留的 approval_status 只是展示态，没有按钮，不算待审批
  const answered = describeProcessGroup({ items: [tool(0, 'delete', {}, { approval_status: 'pending' })] })
  assert.equal(answered.status, 'done')
  assert.deepEqual(answered.pinned, [])

  const done = describeProcessGroup({ items: [think(0), tool(0, 'read_file', { file_path: 'a' })] })
  assert.equal(done.status, 'done')
  assert.equal(done.title, '已思考 2 秒 · 读取 1 个文件')
  assert.equal(done.thinkingOnly, false)

  const thinkingOnly = describeProcessGroup({ items: [think(0)] })
  assert.equal(thinkingOnly.thinkingOnly, true)
  assert.equal(thinkingOnly.steps, 0)
})

test('只调了 write_todos 的一步：组里有叙述、没有可见工具，不按「只有思考」渲染', () => {
  const blocks = [think(1), say(1, '我先列一下计划。'), tool(1, 'write_todos'), think(2), say(2, '答案是 42。')]
  const [group, answer] = buildMessageSegments(blocks)
  assert.deepEqual(group.items.map((i) => (i.narration ? 'narration' : i.type)), ['thinking', 'narration', 'thinking'])
  const view = describeProcessGroup(group)
  assert.equal(view.thinkingOnly, false)
  assert.deepEqual(view.rows.map((r) => r.type), ['thinking', 'narration', 'thinking'])
  assert.equal(view.title, '已思考 4 秒')
  assert.equal(answer.block.content, '答案是 42。')
})

test('结果里带图表的数据工具调用放进 visuals：组折叠时也要露出来，不算待审批', () => {
  const chart = JSON.stringify({ title: '存款', chart: { series: [{ type: 'bar', data: [1] }] } })
  const items = [think(0), tool(0, 'query_branch_deposit', {}, { result: chart }), tool(0, 'read_file', { file_path: 'a' })]
  const view = describeProcessGroup({ items })
  assert.equal(view.status, 'done')
  assert.deepEqual(view.visuals.map((i) => i.tool_name), ['query_branch_deposit'])
  assert.deepEqual(view.pinned, [])
})

test('实时预览取最后一段有内容的文字的末尾，两步之间往前找', () => {
  const writing = tool(1, 'write_file', { file_path: 'a.md', content: 'x'.repeat(500) + '结尾' }, { duration: null, result: '' })
  assert.equal(processLivePreview([writing], 10), `${'x'.repeat(8)}结尾`)
  // 新工具刚开始、还没有参数：沿用上一步的思考内容，不闪空
  const fresh = tool(2, 'read_file', {}, { duration: null, result: '' })
  assert.equal(processLivePreview([think(1, { content: '上一步的思考' }), fresh]), '上一步的思考')
  assert.equal(processLivePreview([tool(0, 'execute', { command: 'make' }, { duration: null, result: '' })]), '$ make')
  assert.equal(processLivePreview([]), '')
})

test('展开列表合并连续的已完成读取 / 搜索，执行中、失败、待审批与叙述打断合并', () => {
  const reads = [1, 2, 3].map((n) => ({ ...tool(0, 'read_file', { file_path: `/w/${n}.md` }), origIndex: n }))
  const rows = processGroupRows([
    ...reads,
    { ...tool(0, 'read_file', { file_path: '/w/4.md' }, { success: false }), origIndex: 4 },
    { ...say(1, '再搜一下。'), narration: true, origIndex: 5 },
    { ...tool(1, 'grep', { pattern: 'a' }), origIndex: 6 },
    { ...tool(1, 'glob', { pattern: '*.md' }), origIndex: 7 },
    { ...tool(1, 'grep', { pattern: 'b' }, { duration: null }), origIndex: 8 },
    { ...tool(2, 'execute'), origIndex: 9 },
    { ...tool(2, 'execute'), origIndex: 10 },
  ])
  assert.deepEqual(rows.map((r) => (r.type === 'run' ? `run:${r.label}` : `${r.type}:${r.item.origIndex}`)), [
    'run:读取 3 个文件', 'tool:4', 'narration:5', 'run:搜索 2 次', 'tool:8', 'tool:9', 'tool:10',
  ])
  assert.equal(rows[0].key, 'r1')
})
