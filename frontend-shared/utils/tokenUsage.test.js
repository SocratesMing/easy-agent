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

test('zero and isolated peaks yield finite bars inside each date slot', () => {
  const daily = { dates: ['01', '02', '03'], series: [{ model_key: 'a', model_name: 'A', values: [0, 0, 0] }] }
  const zero = buildUsageChart(daily)
  assert.equal(zero.ticks[0].value, 0)
  assert.equal(zero.series[0].points[0].y, zero.bottom)
  assert.equal(zero.series[0].points[0].height, 0)
  assert.ok(zero.ticks.at(-1).value > 0)
  assert.ok(zero.series[0].points.every(point => [point.x, point.y, point.barX, point.barWidth, point.height].every(Number.isFinite)))
  daily.series[0].values = [0, 100, 0]
  const snapshot = JSON.stringify(daily)
  const chart = buildUsageChart(daily)
  const slot = (chart.right - chart.left) / 3
  const peak = chart.series[0].points[1]
  assert.ok(peak.y < chart.bottom)
  assert.equal(peak.height, chart.bottom - peak.y)
  assert.equal(chart.dates[2].x, chart.left + slot * 2.5)
  assert.ok(peak.barX >= chart.left + slot)
  assert.ok(peak.barX + peak.barWidth <= chart.left + slot * 2)
  assert.equal(JSON.stringify(daily), snapshot)
  const hidden = buildUsageChart(daily, [usageSeriesId(daily.series[0])])
  assert.equal(hidden.series.length, 0)
  assert.equal(JSON.stringify(daily), snapshot)
})

test('model bars share a date slot without overlapping', () => {
  const daily = {
    dates: ['01', '02'],
    series: [
      { model_key: 'a', model_name: 'A', values: [10, 0] },
      { model_key: 'b', model_name: 'B', values: [0, 40] },
    ],
  }
  const chart = buildUsageChart(daily, [], 720, 220)
  const [first, second] = chart.series
  assert.equal(first.points[0].barWidth, second.points[0].barWidth)
  assert.ok(first.points[0].barX + first.points[0].barWidth <= second.points[0].barX)
  assert.ok(first.points[0].barX >= chart.left)
  assert.ok(second.points[1].barX + second.points[1].barWidth <= chart.right)
  assert.ok(second.points[1].height > first.points[1].height)
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
  assert.equal(wide.dates[0].x, wide.left + wide.slot / 2)
  assert.equal(narrow.dates.at(-1).x, narrow.right - narrow.slot / 2)
  assert.ok(wide.slot > narrow.slot)
  assert.deepEqual(wide.ticks, narrow.ticks)
  assert.deepEqual(wide.series[0].points.map(point => point.y), narrow.series[0].points.map(point => point.y))
  assert.ok(wide.series[0].points[0].barWidth > narrow.series[0].points[0].barWidth)
  assert.equal(wide.bottom, 180)
  assert.equal(buildUsageChart(daily).bottom, 248)
})
