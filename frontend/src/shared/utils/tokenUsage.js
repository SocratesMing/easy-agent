// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/utils/tokenUsage.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
/** Token statistics presentation only; counts always come from the usage API. */
export function validateUsageDays(days) {
  if (days !== 7 && days !== 30) throw new RangeError('统计范围仅支持7日或30日')
  return days
}

export function formatExactTokens(value) {
  return Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 0 })
}

export function formatTokens(value) {
  if (value >= 1e8) return `${Number((value / 1e8).toFixed(2))}亿`
  if (value >= 1e4) return `${Number((value / 1e4).toFixed(2))}万`
  return formatExactTokens(value)
}

// JSON tuples preserve case and delimiters in both identity fields.
export function usageSeriesId(series) {
  return JSON.stringify([series.model_key, series.model_name])
}

export function labelUsageSeries(series) {
  const names = new Map()
  for (const item of series) names.set(item.model_name, (names.get(item.model_name) || 0) + 1)
  return series.map((item, index) => ({
    ...item,
    id: usageSeriesId(item),
    colorIndex: index % 6,
    dash: ['', '7 3', '2 3', '9 3 2 3', '5 2', '1 3'][Math.floor(index / 6) % 6],
    label: names.get(item.model_name) > 1 ? `${item.model_name} (${item.model_key})` : item.model_name,
  }))
}

export function usageDateTicks(dates) {
  return dates.map((date, index) => ({ date, index }))
    .filter(({ index }) => dates.length <= 7 || index % 5 === 0 || index === dates.length - 1)
}

/** Match the SVG viewport; each date owns one slot, and model bars share that slot. */
export function buildUsageChart(daily, hiddenIds = [], width = 720, height = 288) {
  const left = 72, right = Math.max(left + 1, width - 24), top = 20, bottom = height - 40
  const series = labelUsageSeries(daily.series)
  const visible = series.filter(item => !hiddenIds.includes(item.id))
  const peak = visible.reduce((max, item) => item.values.reduce((m, value) => Math.max(m, value), max), 0)
  const rawStep = Math.max(1, (peak || 4) / 4)
  const magnitude = Math.pow(10, Math.floor(Math.log10(rawStep)))
  const step = Math.ceil([1, 2, 2.5, 5, 10].find(value => value * magnitude >= rawStep) * magnitude)
  const ceiling = step * 4
  const count = Math.max(daily.dates.length, 1)
  const slot = (right - left) / count
  const x = index => left + slot * (index + 0.5)
  const y = value => bottom - value / ceiling * (bottom - top)
  const groupSize = Math.max(visible.length, 1)
  const pad = slot * 0.14
  const gap = groupSize > 1 ? Math.min(slot * 0.06, 4) : 0
  const barWidth = Math.max(0, (slot - pad * 2 - gap * (groupSize - 1)) / groupSize)
  return {
    left, right, top, bottom, slot,
    ticks: Array.from({ length: 5 }, (_, index) => ({ value: index * step, y: y(index * step) })),
    dates: daily.dates.map((date, index) => ({ date, x: x(index), index })),
    dateTicks: usageDateTicks(daily.dates).map(tick => ({ ...tick, x: x(tick.index) })),
    series: visible.map((item, seriesIndex) => ({
      ...item,
      barWidth,
      points: item.values.map((value, index) => ({
        x: x(index),
        y: y(value),
        value,
        barX: left + slot * index + pad + seriesIndex * (barWidth + gap),
        barWidth,
        height: value / ceiling * (bottom - top),
      })),
    })),
  }
}
