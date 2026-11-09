import assert from 'node:assert/strict'
import test from 'node:test'

import { toolCard } from './toolPresentation.js'

// 数据类 MCP 工具的结果信封（带 ECharts option）：前端已不再渲染图表
const envelope = JSON.stringify({
  shape: 'table+chart',
  title: '各分行存款余额',
  summary: '共 2 家分行',
  table: { columns: ['分行', '余额'], rows: [['A', '10'], ['B', '20']] },
  chart: { xAxis: { type: 'category', data: ['A', 'B'] }, yAxis: { type: 'value' }, series: [{ type: 'bar', data: [10, 20] }] },
})

test('带 chart 的数据类 MCP 结果按通用卡片原文展示，不再生成图表卡片', () => {
  const card = toolCard('query_branch_deposit', { stat_date: '2026-10-30' }, envelope, true)
  assert.equal(card.card, 'generic')
  assert.equal(card.kind, 'other')
  assert.equal(card.title, 'query_branch_deposit')
  assert.equal(card.body, envelope)
  assert.equal(card.error, false)
  assert.equal('artifact' in card, false)
})

test('MCP content 数组形式的图表结果同样走通用卡片，失败状态照常标记', () => {
  const result = [{ type: 'text', text: envelope }]
  const card = toolCard('query_branch_deposit', {}, result, false)
  assert.equal(card.card, 'generic')
  assert.equal(card.body, JSON.stringify(result))
  assert.equal(card.error, true)
})

test('内建工具的卡片类型不受影响', () => {
  assert.equal(toolCard('execute', { command: 'ls' }, 'a\n[Command succeeded with exit code 0]').card, 'terminal')
  assert.equal(toolCard('read_file', { file_path: '/workspace/a.md' }, 'x').card, 'read')
  assert.equal(toolCard('write_file', { file_path: '/workspace/a.md', content: 'x' }, 'ok').card, 'diff')
  assert.equal(toolCard('glob', { pattern: '*.md' }, "['/a.md']").shape, 'paths')
})
