import assert from 'node:assert/strict'
import test from 'node:test'
import { validateUsageDays, formatTokens, formatExactTokens, usageSeriesId, labelUsageSeries, usageDateTicks, buildUsageChart } from './tokenUsage.js'

test('only seven/thirty days are allowed; formatting retains precise tooltips', () => {
  for (const days of [7, 30]) assert.equal(validateUsageDays(days), days)
  for (const days of [0, 1, '7', 31, null]) assert.throws(() => validateUsageDays(days), RangeError)
  assert.equal(formatTokens(12345), '1.23万')
  assert.equal(formatTokens(123456789), '1.23亿')
  assert.equal(formatExactTokens(123456789), '123,456,789')
  assert.equal(formatExactTokens(0), '0')
})

test('structured model identities distinguish delimiters, case and same names', () => {
  const series = [
    { model_key: 'A', model_name: 'same', values: [1] },
    { model_key: 'a', model_name: 'same', values: [2] },
    { model_key: 'a/b', model_name: 'c', values: [3] },
    { model_key: 'a', model_name: 'b/c', values: [4] },
  ]
  const result = labelUsageSeries(series)
  assert.equal(new Set(result.map(usageSeriesId)).size, 4)
  assert.equal(result[0].label, 'same (A)')
  assert.equal(result[1].label, 'same (a)')
  assert.equal(result[2].label, 'c')
})

test('zero and isolated peaks yield finite coordinates, zero origin and visible endpoints', () => {
  const daily = { dates: ['01', '02', '03'], series: [{ model_key: 'a', model_name: 'A', values: [0, 0, 0] }] }
  const zero = buildUsageChart(daily)
  assert.equal(zero.ticks[0].value, 0)
  assert.equal(zero.series[0].points[0].y, zero.bottom)
  assert.ok(zero.ticks.at(-1).value > 0)
  assert.ok(!/NaN|Infinity/.test(zero.series[0].path))
  daily.series[0].values = [0, 100, 0]
  const snapshot = JSON.stringify(daily)
  const chart = buildUsageChart(daily)
  assert.ok(chart.series[0].points[1].y < chart.bottom)
  assert.equal(chart.series[0].points[2].x, chart.right)
  assert.equal(JSON.stringify(daily), snapshot)
  const hidden = buildUsageChart(daily, [usageSeriesId(daily.series[0])])
  assert.equal(hidden.series.length, 0)
  assert.equal(JSON.stringify(daily), snapshot)
})

test('date ticks show all seven dates and thirty-day endpoints', () => {
  assert.equal(usageDateTicks(Array.from({ length: 7 }, (_, i) => i)).length, 7)
  assert.deepEqual(usageDateTicks(Array.from({ length: 30 }, (_, i) => i)).map(t => t.index), [0, 5, 10, 15, 20, 25, 29])
})


test('resizing the viewport expands dates horizontally while retaining the value scale and axis insets', () => {
  const daily = { dates: ['01', '02', '03'], series: [{ model_key: 'a', model_name: 'A', values: [0, 100, 50] }] }
  const narrow = buildUsageChart(daily, [], 320, 220)
  const wide = buildUsageChart(daily, [], 1000, 220)
  assert.equal(wide.right - narrow.right, 680)
  assert.equal(narrow.dates[0].x, wide.dates[0].x)
  assert.equal(wide.dates.at(-1).x, 976)
  assert.equal(narrow.dates.at(-1).x, 296)
  assert.deepEqual(wide.ticks, narrow.ticks)
  assert.deepEqual(wide.series[0].points.map(point => point.y), narrow.series[0].points.map(point => point.y))
  assert.equal(wide.bottom, 180)
  assert.equal(buildUsageChart(daily).bottom, 248)
})
