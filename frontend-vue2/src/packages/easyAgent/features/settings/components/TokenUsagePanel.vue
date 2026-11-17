<template>
  <section ref="usageRoot" class="token-usage" aria-label="Token统计" :aria-busy="loading">
    <header class="usage-header">
      <div class="usage-heading"><h3>Token统计</h3><p>已记录的模型用量 <span class="usage-unit">单位：Token</span></p></div>
      <button type="button" :disabled="loading" @click="loadUsage">刷新</button>
    </header>
    <div v-if="loading && !usage" class="usage-skeleton" role="status" aria-label="正在加载Token统计">
      <div class="usage-cards"><div v-for="card in cards" :key="card.key" class="skeleton-card"></div></div>
      <div class="skeleton-chart"></div><span>正在加载统计…</span>
    </div>
    <div v-else-if="error && !usage" class="usage-error" role="alert"><p>{{ error }}</p><button type="button" @click="loadUsage">重试</button></div>
    <template v-else-if="usage">
      <div class="usage-cards">
        <div v-for="card in cards" :key="card.key" class="usage-card">
          <span>{{ card.label }}</span>
          <strong :title="exact(usage.summary[card.key]) + ' Token'" :aria-label="card.label + ' ' + exact(usage.summary[card.key]) + ' Token'">{{ exact(usage.summary[card.key]) }}</strong>
          <small>{{ card.hint }}</small>
        </div>
      </div>
      <div class="usage-trend">
        <div class="usage-chart-header">
          <div class="usage-chart-heading">
            <h4>每日Token趋势</h4>
            <div v-if="series.length" class="usage-legend" role="group" aria-label="显示或隐藏模型柱">
              <button v-for="item in series" :key="item.id" type="button" :aria-pressed="!hiddenIds.includes(item.id)" :class="[{ muted: hiddenIds.includes(item.id) }, 'series-' + item.colorIndex]" @click="toggleSeries(item.id)"><span aria-hidden="true">●</span> {{ item.label }}</button>
            </div>
          </div>
          <div class="usage-range" role="group" aria-label="趋势时间范围">
            <button v-for="range in [7, 30]" :key="range" type="button" :aria-pressed="days === range" @click="changeDays(range)">近{{ range }}日</button>
          </div>
        </div>
        <div v-if="loading || error" class="usage-chart-loading" role="status">
          <div class="skeleton-chart"></div>
          <span>{{ error || '正在更新趋势…' }}</span>
          <button v-if="error" type="button" @click="loadUsage">重试</button>
        </div>
        <template v-else>
        <p v-if="!hasWindowUsage" class="usage-empty" role="status">暂无Token用量</p>
        <svg class="usage-chart" :viewBox="'0 0 ' + chartWidth + ' 220'" role="group" :aria-label="'近' + usage.days + '日每日Token柱状图，按日期聚焦查看精确用量'">
          <title>每日Token趋势</title>
          <g v-for="tick in chart.ticks" :key="tick.value" class="chart-grid">
            <line :x1="chart.left" :x2="chart.right" :y1="tick.y" :y2="tick.y" />
            <text :x="chart.left - 10" :y="tick.y + 4" text-anchor="end">{{ compact(tick.value) }}</text>
          </g>
          <g v-for="item in chart.series" :key="item.id" :class="'series-' + item.colorIndex">
            <rect v-for="(point, index) in item.points" :key="index" :x="point.barX" :y="point.y" :width="point.barWidth" :height="point.height" rx="2" class="chart-bar" />
          </g>
          <line v-if="selectedPoint" :x1="selectedPoint.x" :x2="selectedPoint.x" :y1="chart.top" :y2="chart.bottom" class="chart-selection" />
          <text v-for="tick in chart.dateTicks" :key="'label-' + tick.date" :x="tick.x" :y="chart.bottom + 26" text-anchor="middle" class="chart-date">{{ tick.date.slice(5) }}</text>
          <rect v-for="point in chart.dates" :key="'hit-' + point.date" :x="point.x - dateHitWidth / 2" :y="chart.top" :width="dateHitWidth" :height="chart.bottom - chart.top" class="date-hit" tabindex="0" role="button" :aria-label="point.date + '，总Token ' + exact(usage.daily.totals[point.index])" :aria-pressed="selectedIndex === point.index" @mouseenter="selectedIndex = point.index" @focus="selectedIndex = point.index" @click="selectedIndex = point.index" @keydown.enter.prevent="selectedIndex = point.index" @keydown.space.prevent="selectedIndex = point.index" />
        </svg>
        <div v-if="selectedPoint" class="usage-tooltip" aria-live="polite" aria-atomic="true">
          <div class="usage-readout-total"><strong>{{ selectedPoint.date }}</strong><span>总Token：<b>{{ exact(usage.daily.totals[selectedIndex]) }}</b></span></div>
          <div v-if="series.length" class="usage-readout-models">
            <div v-for="item in series" :key="item.id" :class="'series-' + item.colorIndex"><span><i aria-hidden="true"></i>{{ item.label }}：</span><b>{{ exact(item.values[selectedIndex]) }}</b></div>
          </div>
        </div>
        <details class="usage-details">
          <summary>逐日精确明细（{{ usage.days }}日）</summary>
          <div class="usage-table-scroll" tabindex="0" role="region" aria-label="逐日Token明细，可横向滚动">
            <table><caption>每日已记录Token，包含隐藏的模型</caption>
              <thead><tr><th scope="col">日期</th><th scope="col">总Token</th><th v-for="item in series" :key="item.id" scope="col">{{ item.label }}</th></tr></thead>
              <tbody><tr v-for="(date, index) in usage.daily.dates" :key="date"><th scope="row">{{ date }}</th><td>{{ exact(usage.daily.totals[index]) }}</td><td v-for="item in series" :key="item.id">{{ exact(item.values[index]) }}</td></tr></tbody>
            </table>
          </div>
        </details>
        </template>
      </div>
      <p class="usage-as-of">更新于 {{ asOfLabel }}（北京时间）</p>
    </template>
    <p class="usage-note">按北京时间统计，仅包含功能启用后已记录的模型用量。</p>
  </section>
</template>

<script>
import { getTokenUsage, getStoredUsername, getStoredToken } from '../services/tokenUsage.js'
import { onRuntimeEvent } from '../../../runtime/events.js'
import { buildUsageChart, labelUsageSeries, formatTokens, formatExactTokens, validateUsageDays } from '../../../shared/utils/tokenUsage.js'

export default {
  data() {
    return {
      days: 7, usage: null, loading: false, error: '', selectedIndex: 0, hiddenIds: [], chartWidth: 720,
      requestSequence: 0, disposed: false,
      cards: [
        { key: 'today', label: '今天', hint: '今日零点起' },
        { key: 'last_7_days', label: '七天', hint: '近7日' },
        { key: 'last_30_days', label: '一个月', hint: '近30日' },
        { key: 'all_time', label: '总共', hint: '启用以来' },
      ],
    }
  },
  computed: {
    series() { return this.usage ? labelUsageSeries(this.usage.daily.series) : [] },
    chart() { return buildUsageChart(this.usage ? this.usage.daily : { dates: [], series: [] }, this.hiddenIds, this.chartWidth, 220) },
    selectedPoint() { return this.chart.dates[this.selectedIndex] },
    dateHitWidth() { return this.chart.slot },
    hasWindowUsage() { return this.usage && this.usage.daily.totals.some(value => value > 0) },
    asOfLabel() {
      return new Date(this.usage.as_of).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
    },
  },
  mounted() {
    if (this.$refs && this.$refs.usageRoot) {
      this.updateChartWidth = () => {
        const svg = this.$refs.usageRoot.querySelector('.usage-chart')
        if (svg) this.chartWidth = Math.max(1, Math.round(svg.getBoundingClientRect().width))
      }
      this.updateChartWidth()
      if (typeof ResizeObserver !== 'undefined') {
        this.usageResizeObserver = new ResizeObserver(this.updateChartWidth)
        this.usageResizeObserver.observe(this.$refs.usageRoot)
      } else window.addEventListener('resize', this.updateChartWidth)
    }
    this.stopAuthListener = onRuntimeEvent('auth-changed', this.onAccountChanged)
    this.loadUsage()
  },
  beforeDestroy() { this.disposeUsage() },
  methods: {
    compact: formatTokens,
    exact: formatExactTokens,
    toggleSeries(id) {
      this.hiddenIds = this.hiddenIds.includes(id) ? this.hiddenIds.filter(value => value !== id) : [...this.hiddenIds, id]
    },
    changeDays(days) {
      validateUsageDays(days)
      if (this.days === days) return
      this.days = days
      this.loadUsage()
    },
    invalidateRequest() {
      this.requestSequence += 1
      if (this.usageController) this.usageController.abort()
      this.usageController = null
    },
    onAccountChanged() {
      this.invalidateRequest()
      this.usage = null
      this.error = ''
      this.loading = false
      this.hiddenIds = []
      // Existing auth helpers emit before updating storage; read the new identity next tick.
      this.$nextTick(() => {
        if (this.disposed) return
        if (getStoredToken()) this.loadUsage()
        else this.error = '请登录后查看Token统计'
      })
    },
    disposeUsage() {
      this.disposed = true
      if (this.usageResizeObserver) this.usageResizeObserver.disconnect()
      else if (this.updateChartWidth) window.removeEventListener('resize', this.updateChartWidth)
      this.invalidateRequest()
      if (this.stopAuthListener) this.stopAuthListener()
    },
    async loadUsage() {
      if (this.disposed) return
      this.invalidateRequest()
      const sequence = this.requestSequence
      const username = getStoredUsername()
      const days = this.days
      const controller = new AbortController()
      this.usageController = controller
      this.loading = true
      this.error = ''
      const isCurrent = () => !this.disposed && sequence === this.requestSequence && username === getStoredUsername()
      try {
        const usage = await getTokenUsage(days, controller.signal)
        if (!isCurrent()) return
        if (usage.days !== days) throw new Error('统计范围不匹配，请重试')
        this.usage = usage
        this.selectedIndex = usage.daily.dates.length - 1
        this.$nextTick(() => {
          if (!this.disposed && this.updateChartWidth) this.updateChartWidth()
        })
      } catch (error) {
        if (isCurrent() && !controller.signal.aborted) this.error = error.message || 'Token统计暂不可用，请重试'
      } finally {
        if (isCurrent()) {
          this.loading = false
          this.usageController = null
        }
      }
    },
  },
}
</script>

<style scoped>
.token-usage { color: var(--text-primary); font-size: 0.875rem; min-width: 0; }
.usage-header { display: flex; align-items: center; justify-content: space-between; gap: 1rem; margin-bottom: 1rem; }
.usage-heading { display: flex; align-items: baseline; flex-wrap: wrap; gap: 0.25rem 0.875rem; }
.usage-unit { display: inline-block; margin-left: 0.75rem; padding-left: 0.75rem; border-left: 1px solid var(--border-color, #e2e8f0); }
.usage-header > button { flex-shrink: 0; white-space: nowrap; }
.usage-header h3 { margin: 0; font-size: 1.125rem; line-height: 1.3; }
.usage-header p, .usage-note, .usage-as-of { margin: 0; color: var(--text-secondary); font-size: 0.75rem; line-height: 1.6; }
button { font: inherit; color: var(--text-primary); cursor: pointer; background: var(--bg-secondary); border: 1px solid var(--border-color, #e2e8f0); border-radius: 0.5rem; padding: 0.5rem 0.75rem; }
button:disabled { opacity: 0.6; cursor: wait; }
button:focus-visible, summary:focus-visible, .usage-table-scroll:focus-visible { outline: 2px solid var(--accent-color, #1677ee); outline-offset: 2px; }
.usage-cards { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 0.75rem; margin-bottom: 1rem; }
.usage-card { display: flex; flex-direction: column; gap: 0.375rem; min-width: 0; min-height: 6.5rem; padding: 0.875rem 1rem; border: 1px solid transparent; background: var(--bg-tertiary, #f4f7fb); border-radius: 0.625rem; }
.usage-card:first-child { background: color-mix(in srgb, var(--accent-color, #1677ee) 9%, var(--bg-secondary, #fff)); border-color: color-mix(in srgb, var(--accent-color, #1677ee) 22%, transparent); }
.usage-card > span { color: var(--text-secondary); font-size: 0.75rem; }
.usage-card strong { font-size: 1.8rem; line-height: 1.1; letter-spacing: -0.03em; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
.usage-card:first-child strong { color: var(--accent-color, #1677ee); }
.usage-card small { color: var(--text-secondary); font-size: 0.6875rem; }
.usage-trend { min-width: 0; border: 1px solid var(--border-color, #e5ebf3); border-radius: 0.875rem; padding: 0.875rem 1.125rem 0; background: var(--bg-secondary); }
.usage-chart-header { display: flex; align-items: center; justify-content: space-between; gap: 1rem; }
.usage-chart-heading { display: flex; align-items: center; flex-wrap: wrap; gap: 0.25rem 1rem; min-width: 0; }
.usage-chart-heading h4 { margin: 0; font-size: 0.9375rem; font-weight: 600; }
.usage-range { display: flex; gap: 0.125rem; flex-shrink: 0; padding: 0.25rem; border-radius: 0.5rem; background: var(--bg-tertiary, #f0f3f8); }
.usage-range button { border: 0; background: transparent; color: var(--text-secondary); padding: 0.375rem 0.6875rem; font-size: 0.75rem; }
.usage-range button[aria-pressed="true"] { color: #fff; background: var(--accent-color, #1677ee); }
.usage-legend { display: flex; flex-wrap: wrap; gap: 0.5rem 1rem; max-height: 7rem; overflow-y: auto; }
.usage-legend button { border: 0; background: transparent; padding: 0.25rem 0; max-width: 100%; overflow-wrap: anywhere; text-align: left; color: var(--text-secondary); font-size: 0.75rem; }
.usage-legend .muted { opacity: 0.55; text-decoration: line-through; }
.usage-legend span { color: var(--series-color); }
.series-0 { --series-color: var(--accent-color, #1677ee); }
.series-1 { --series-color: #16a34a; }
.series-2 { --series-color: #d97706; }
.series-3 { --series-color: #e05771; }
.series-4 { --series-color: #8b77d9; }
.series-5 { --series-color: #14a8a4; }
.usage-chart { display: block; width: 100%; height: auto; aspect-ratio: auto; overflow: visible; margin-top: 0.5rem; }
.chart-grid line { stroke: var(--border-color, #e2e8f0); stroke-dasharray: 3 4; }
.chart-grid text, .chart-date { fill: var(--text-secondary); font-size: 12px; }
.chart-bar { fill: var(--series-color); }
.chart-selection { stroke: var(--accent-color, #1677ee); stroke-dasharray: 3 4; pointer-events: none; }
.date-hit { fill: transparent; cursor: pointer; outline: none; }
.date-hit:focus { fill: var(--accent-color, #1677ee); fill-opacity: 0.1; stroke: var(--accent-color, #1677ee); stroke-width: 1; }
.usage-tooltip { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); align-items: center; gap: 1.375rem; padding: 0.75rem 1rem; background: var(--bg-tertiary, #f3f7fd); border-radius: 0.625rem; font-size: 0.75rem; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.usage-readout-total { display: flex; flex-direction: column; gap: 0.5rem; }
.usage-readout-total > span { color: var(--text-secondary); }
.usage-readout-total b { color: var(--text-primary); font-size: 1.375rem; margin-left: 0.5rem; }
.usage-readout-models { display: grid; gap: 0.5rem; max-height: 6rem; overflow-y: auto; border-left: 1px solid var(--border-color, #dce6f3); padding-left: 1.375rem; }
.usage-readout-models > div, .usage-readout-models span { display: flex; align-items: center; gap: 0.5rem; min-width: 0; }
.usage-readout-models > div { justify-content: space-between; }
.usage-readout-models span { color: var(--text-secondary); overflow-wrap: anywhere; }
.usage-readout-models i { width: 0.4375rem; height: 0.4375rem; flex: none; border-radius: 50%; background: var(--series-color); }
.usage-readout-models b { flex: none; }
.usage-details { margin-top: 0.25rem; }
.usage-details summary { cursor: pointer; padding: 0.875rem 0; font-size: 0.75rem; }
.usage-table-scroll { overflow-x: auto; padding-bottom: 0.875rem; }
table { width: 100%; border-collapse: collapse; white-space: nowrap; font-size: 0.75rem; font-variant-numeric: tabular-nums; }
caption { text-align: left; padding: 0.75rem 0; color: var(--text-secondary); }
thead { background: var(--bg-tertiary, #f5f8fc); color: var(--text-secondary); }
th, td { padding: 0.75rem 0.875rem; border-bottom: 1px solid var(--border-color, #e2e8f0); text-align: right; }
th:first-child { text-align: left; }
.usage-empty, .usage-error { text-align: center; padding: 1rem; color: var(--text-secondary); }
.usage-as-of, .usage-note { margin-top: 0.5rem; }
.usage-chart-loading { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 0.5rem 0.75rem; margin-top: 0.5rem; color: var(--text-secondary); font-size: 0.75rem; }
.usage-chart-loading .skeleton-chart { grid-column: 1 / -1; height: 13.75rem; margin: 0; }
.skeleton-card, .skeleton-chart { background: var(--bg-tertiary, #e2e8f0); border-radius: 0.625rem; }
.skeleton-card { height: 6.8rem; }
.skeleton-chart { height: 16rem; margin-bottom: 0.75rem; }
.usage-skeleton > span { color: var(--text-secondary); }
@media (max-width: 48rem) {
  .usage-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .usage-card { padding: 0.75rem; min-height: 5.5rem; }
  .usage-card strong { font-size: 1.375rem; }
  .usage-header h3 { font-size: 1.125rem; }
  .usage-chart-header { align-items: flex-start; }
  .usage-chart-heading { flex-direction: column; align-items: flex-start; }
  .usage-tooltip { grid-template-columns: 1fr; gap: 0.75rem; }
  .usage-readout-models { border-left: 0; border-top: 1px solid var(--border-color, #dce6f3); padding: 0.75rem 0 0; }
  .usage-trend { padding: 0.75rem 0.75rem 0; }
}
</style>
