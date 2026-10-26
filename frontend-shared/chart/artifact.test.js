import assert from 'node:assert/strict'
import test from 'node:test'

import {
  applyChartTheme,
  estimateChartHeight,
  extractChartArtifact,
  formatArtifactMeta,
  normalizeChartOption,
} from './artifact.js'

const OPTION = {
  title: {
    text: '各中心收入总览（亿元）',
    left: 'center',
    textStyle: { fontSize: 14, color: '#2C2C2A' },
  },
  tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
  legend: { bottom: 0, textStyle: { color: '#5F5E5A', fontSize: 11 } },
  grid: { left: '3%', right: '4%', bottom: 34, top: 46, containLabel: true },
  color: ['#C0392B', '#2E86C1'],
  xAxis: { type: 'value', name: '亿元' },
  yAxis: { type: 'category', data: ['金融市场中心', '资金营运中心'], inverse: true },
  series: [
    { name: '营业净收入', type: 'bar', data: [1.2, 2.3], itemStyle: { color: '#C0392B' } },
    { name: '非息收入', type: 'bar', data: [0.4, 0.9], itemStyle: { color: '#2E86C1' } },
  ],
}

function envelope(chart = OPTION, extra = {}) {
  return {
    shape: chart ? 'table+chart' : 'table',
    title: '各中心收入总览（亿元）',
    summary: '2026-09 全部门营业净收入 3.5 亿元。',
    unit: '亿元',
    stat_date: '2026-09',
    table: { columns: ['中心', '收入'], rows: [['金融市场中心', '1.2'], ['资金营运中心', '2.3']] },
    chart,
    notes: ['口径：金融市场部全部中心。'],
    result_state: 'OK',
    ...extra,
  }
}

test('JSON 信封字符串可提取图表产物', () => {
  const artifact = extractChartArtifact(JSON.stringify(envelope()))
  assert.ok(artifact)
  assert.equal(artifact.kind, 'chart')
  assert.equal(artifact.title, '各中心收入总览（亿元）')
  assert.equal(artifact.unit, '亿元')
  assert.equal(artifact.statDate, '2026-09')
  assert.equal(artifact.option.series.length, 2)
  assert.equal(artifact.table.rows.length, 2)
  assert.ok(artifact.height >= 240)
})

test('MCP content JSON 数组（live 形态）可提取', () => {
  const raw = JSON.stringify([{ type: 'text', text: JSON.stringify(envelope()) }])
  const artifact = extractChartArtifact(raw)
  assert.ok(artifact)
  assert.equal(artifact.option.series[0].type, 'bar')
})

test('Python repr（历史形态、含撇号）可提取', () => {
  const payload = envelope(OPTION, { summary: "it's fine 的摘要" })
  const jsonText = JSON.stringify(payload)
  // 模拟 Python str(list) 的 repr：单引号定界，内部撇号转义
  const pyText = jsonText.replace(/\\/g, '\\\\').replace(/'/g, "\\'")
  const repr = `[{'type': 'text', 'text': '${pyText}'}]`
  const artifact = extractChartArtifact(repr)
  assert.ok(artifact, 'Python repr 应能解析出图表')
  assert.equal(artifact.title, '各中心收入总览（亿元）')
})

test('多文本块时优先选择带 chart 的块', () => {
  const raw = JSON.stringify([
    { type: 'text', text: '先给一段人读的说明' },
    { type: 'text', text: JSON.stringify(envelope()) },
  ])
  const artifact = extractChartArtifact(raw)
  assert.ok(artifact)
})

test('直接传入已解析对象 / MCP content 数组都能提取', () => {
  assert.ok(extractChartArtifact(envelope()))
  const artifact = extractChartArtifact([
    { type: 'text', text: '先给一段人读的说明' },
    { type: 'text', text: JSON.stringify(envelope()) },
  ])
  assert.ok(artifact)
  assert.equal(artifact.option.series[0].type, 'bar')
})

test('table-only 信封不产出图表', () => {
  assert.equal(extractChartArtifact(JSON.stringify(envelope(null))), null)
})

test('普通文本 / 畸形 JSON 不产出图表且不抛错', () => {
  assert.equal(extractChartArtifact('工具执行完成'), null)
  assert.equal(extractChartArtifact('{"shape": "table+chart", '), null)
  assert.equal(extractChartArtifact(''), null)
  assert.equal(extractChartArtifact(null), null)
})

test('超长结果直接跳过', () => {
  assert.equal(extractChartArtifact('x'.repeat(2_000_001)), null)
})

test('option 白名单：未知顶层键丢弃、非法 series 丢弃', () => {
  const option = normalizeChartOption({
    graphic: { elements: [] },
    series: [
      { type: 'scatter', data: [1, 2] },
      { type: 'bar', data: [3, 4] },
    ],
  })
  assert.ok(option)
  assert.equal(option.graphic, undefined)
  assert.equal(option.series.length, 1)
  assert.equal(option.series[0].type, 'bar')
  assert.equal(normalizeChartOption({ series: [{ type: 'scatter', data: [1] }] }), null)
})

test('tooltip 强制 richText，业务字符原样保留，标题链接被移除', () => {
  const option = normalizeChartOption({
    ...OPTION,
    title: { text: '收入<同比>', link: 'javascript:alert(1)' },
    yAxis: { type: 'category', data: ['<b>中心A</b>', '中心B'] },
  })
  assert.ok(option)
  // richText 走画布渲染，标签里的 < > 不需要删除，避免把「>1000万」这类业务文案改坏
  assert.equal(option.yAxis.data[0], '<b>中心A</b>')
  assert.equal(option.title.text, '收入<同比>')
  assert.equal(option.title.link, undefined)
  assert.equal(option.tooltip.renderMode, 'richText')
  assert.equal(option.tooltip.confine, true)
})

test('暗色主题只改文字颜色，不改业务配色，且不修改入参', () => {
  const artifact = extractChartArtifact(JSON.stringify(envelope()))
  const before = artifact.option.title.textStyle.color
  const themed = applyChartTheme(artifact.option, true)
  assert.notEqual(themed.title.textStyle.color, before)
  assert.equal(themed.series[0].itemStyle.color, '#C0392B')
  assert.equal(artifact.option.title.textStyle.color, before)
})

test('高度估算：横向排名按类目数增高，饼图固定 320', () => {
  const many = {
    ...OPTION,
    yAxis: { type: 'category', data: Array.from({ length: 12 }, (_, i) => `中心${i}`) },
  }
  assert.ok(estimateChartHeight(many) > 400)
  assert.equal(estimateChartHeight({ series: [{ type: 'pie', data: [] }] }), 320)
  assert.equal(estimateChartHeight(OPTION), 240)
})

test('卡片副信息格式', () => {
  assert.equal(formatArtifactMeta({ statDate: '2026-09', unit: '亿元' }), '2026-09 · 亿元')
  assert.equal(formatArtifactMeta(null), '')
})
