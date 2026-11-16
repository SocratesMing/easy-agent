// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/visual/envelope.test.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
import assert from 'node:assert/strict'
import test from 'node:test'
import { hasChartEnvelope, parseChartEnvelope } from './envelope.js'

const envelope = {
  shape: 'table+chart',
  title: '各分行存款余额',
  summary: '共 2 家分行',
  unit: '万元',
  stat_date: '2026-10-30',
  table: { columns: ['分行', '余额'], rows: [['A', 10], ['B', null]] },
  chart: { xAxis: { type: 'category', data: ['A', 'B'] }, yAxis: { type: 'value' }, series: [{ type: 'bar', data: [10, 20] }] },
  notes: ['金额已折算为万元。', 3],
}

test('四种结果形态都能还原成信封：对象、JSON 文本、MCP content 数组、Python repr', () => {
  const json = JSON.stringify(envelope)
  const forms = [
    envelope,
    json,
    [{ type: 'text', text: json }],
    `[{'type': 'text', 'text': '${json.replace(/'/g, "\\'")}'}]`,
  ]
  for (const form of forms) {
    const parsed = parseChartEnvelope(form)
    assert.ok(parsed, JSON.stringify(form).slice(0, 60))
    assert.equal(parsed.title, '各分行存款余额')
    assert.deepEqual(parsed.chart.series, envelope.chart.series)
  }
})

test('Python repr 里的 True / None 和正文撇号都能正确转换', () => {
  const repr = "{'title': \"Tom's 报表\", 'chart': {'series': [{'type': 'line', 'data': [1, None], 'smooth': True}]}}"
  const parsed = parseChartEnvelope(repr)
  assert.equal(parsed.title, "Tom's 报表")
  assert.deepEqual(parsed.chart.series[0].data, [1, null])
  assert.equal(parsed.chart.series[0].smooth, true)
})

test('表格与备注做归一化：单元格转文字、空值变空串、非文字备注丢弃', () => {
  const parsed = parseChartEnvelope(envelope)
  assert.deepEqual(parsed.table, { columns: ['分行', '余额'], rows: [['A', '10'], ['B', '']] })
  assert.deepEqual(parsed.notes, ['金额已折算为万元。'])
  assert.equal(parsed.statDate, '2026-10-30')
})

test('不带可画图表的结果返回 null', () => {
  for (const result of [
    '',
    'chart 这个词出现在普通文本里',
    JSON.stringify({ shape: 'table', title: 'x', chart: null }),
    JSON.stringify({ chart: { title: { text: '没有 series' } } }),
    '[1, 2, 3]',
    null,
    undefined,
    42,
  ]) {
    assert.equal(parseChartEnvelope(result), null, String(result))
    assert.equal(hasChartEnvelope(result), false)
  }
})

test('同一字符串结果重复解析返回同一对象（流式重渲时走缓存）', () => {
  const json = JSON.stringify(envelope)
  assert.equal(parseChartEnvelope(json), parseChartEnvelope(json))
})
