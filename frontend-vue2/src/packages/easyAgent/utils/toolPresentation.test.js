import assert from 'node:assert/strict'
import test from 'node:test'

import { toolCard } from './toolPresentation.js'

// 数据类 MCP 工具的结果信封（chart 是现成的 ECharts option）：画成图表卡片，图交给沙箱 iframe
const envelope = JSON.stringify({
  shape: 'table+chart',
  title: '各分行存款余额',
  summary: '共 2 家分行',
  unit: '万元',
  stat_date: '2026-10-30',
  table: { columns: ['分行', '余额'], rows: [['A', '10'], ['B', '20']] },
  chart: { xAxis: { type: 'category', data: ['A', 'B'] }, yAxis: { type: 'value' }, series: [{ type: 'bar', data: [10, 20] }] },
})

test('带 chart 的数据类 MCP 结果画成图表卡片，标题取信封标题，原始结果保留在 body', () => {
  const card = toolCard('query_branch_deposit', { stat_date: '2026-10-30' }, envelope, true)
  assert.equal(card.card, 'visual')
  assert.equal(card.kind, 'other')
  assert.equal(card.title, '各分行存款余额')
  assert.equal(card.meta, '2026-10-30 · 万元')
  assert.equal(card.body, envelope)
  assert.equal(card.error, false)
  assert.deepEqual(card.envelope.chart.series, [{ type: 'bar', data: [10, 20] }])
  assert.deepEqual(card.envelope.table.columns, ['分行', '余额'])
})

test('MCP content 数组形式的图表结果同样画图，失败状态照常标记', () => {
  const result = [{ type: 'text', text: envelope }]
  const card = toolCard('query_branch_deposit', {}, result, false)
  assert.equal(card.card, 'visual')
  assert.equal(card.body, JSON.stringify(result))
  assert.equal(card.error, true)
})

test('没有可画图表的信封仍走通用卡片', () => {
  const tableOnly = JSON.stringify({ shape: 'table', title: '明细', table: { columns: ['a'], rows: [['1']] }, chart: null })
  const card = toolCard('query_branch_deposit', {}, tableOnly, true)
  assert.equal(card.card, 'generic')
  assert.equal(card.title, 'query_branch_deposit')
})

test('内建工具的卡片类型不受影响', () => {
  assert.equal(toolCard('execute', { command: 'ls' }, 'a\n[Command succeeded with exit code 0]').card, 'terminal')
  assert.equal(toolCard('read_file', { file_path: '/workspace/a.md' }, 'x').card, 'read')
  assert.equal(toolCard('write_file', { file_path: '/workspace/a.md', content: 'x' }, 'ok').card, 'diff')
  assert.equal(toolCard('glob', { pattern: '*.md' }, "['/a.md']").shape, 'paths')
})
