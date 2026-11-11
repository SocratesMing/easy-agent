<template>
  <section class="token-usage" aria-label="Token统计" :aria-busy="loading">
    <header class="usage-header">
      <div><h3>Token统计</h3><p>已记录的模型用量</p></div>
      <button type="button" :disabled="loading" @click="loadUsage">刷新</button>
    </header>
    <div class="usage-range" role="group" aria-label="趋势时间范围">
      <span>每日Token趋势</span>
      <div><button v-for="range in [7, 30]" :key="range" type="button" :aria-pressed="days === range" @click="changeDays(range)">近{{ range }}日</button></div>
    </div>
    <div v-if="loading" class="usage-skeleton" role="status" aria-label="正在加载Token统计">
      <div class="usage-cards"><div v-for="card in cards" :key="card.key" class="skeleton-card"></div></div>
      <div class="skeleton-chart"></div><span>正在加载统计…</span>
    </div>
    <div v-else-if="error" class="usage-error" role="alert"><p>{{ error }}</p><button type="button" @click="loadUsage">重试</button></div>
    <template v-else-if="usage">
      <div class="usage-cards">
        <div v-for="card in cards" :key="card.key" class="usage-card">
          <span>{{ card.label }}</span>
          <strong :title="exact(usage.summary[card.key]) + ' Token'" :aria-label="card.label + ' ' + exact(usage.summary[card.key]) + ' Token'">{{ compact(usage.summary[card.key]) }}</strong>
          <small>{{ card.hint }}</small>
        </div>
      </div>
      <div class="usage-trend">
        <p v-if="!hasWindowUsage" class="usage-empty" role="status">暂无Token用量</p>
        <div v-if="series.length" class="usage-legend" role="group" aria-label="显示或隐藏模型曲线">
          <button v-for="item in series" :key="item.id" type="button" :aria-pressed="!hiddenIds.includes(item.id)" :class="[{ muted: hiddenIds.includes(item.id) }, 'series-' + item.colorIndex]" @click="toggleSeries(item.id)"><span aria-hidden="true">●</span> {{ item.label }}</button>
        </div>
        <svg class="usage-chart" viewBox="0 0 720 288" role="group" :aria-label="'近' + usage.days + '日每日Token趋势，按日期聚焦查看精确用量'">
          <title>每日Token趋势</title>
          <g v-for="tick in chart.ticks" :key="tick.value" class="chart-grid">
            <line :x1="chart.left" :x2="chart.right" :y1="tick.y" :y2="tick.y" />
            <text :x="chart.left - 10" :y="tick.y + 4" text-anchor="end">{{ compact(tick.value) }}</text>
          </g>
          <g v-for="item in chart.series" :key="item.id" :class="'series-' + item.colorIndex">
            <path :d="item.path" :stroke-dasharray="item.dash" class="chart-line" />
            <circle v-for="(point, index) in item.points" :key="index" :cx="point.x" :cy="point.y" r="3" class="chart-point" />
          </g>
          <line v-if="selectedPoint" :x1="selectedPoint.x" :x2="selectedPoint.x" :y1="chart.top" :y2="chart.bottom" class="chart-selection" />
          <text v-for="tick in chart.dateTicks" :key="'label-' + tick.date" :x="tick.x" y="274" text-anchor="middle" class="chart-date">{{ tick.date.slice(5) }}</text>
          <rect v-for="point in chart.dates" :key="'hit-' + point.date" :x="point.x - dateHitWidth / 2" :y="chart.top" :width="dateHitWidth" :height="chart.bottom - chart.top" class="date-hit" tabindex="0" role="button" :aria-label="point.date + '，总Token ' + exact(usage.daily.totals[point.index])" :aria-pressed="selectedIndex === point.index" @mouseenter="selectedIndex = point.index" @focus="selectedIndex = point.index" @click="selectedIndex = point.index" @keydown.enter.prevent="selectedIndex = point.index" @keydown.space.prevent="selectedIndex = point.index" />
        </svg>
        <div v-if="selectedPoint" class="usage-tooltip" aria-live="polite" aria-atomic="true">
          <strong>{{ selectedPoint.date }}</strong><span>总Token：{{ exact(usage.daily.totals[selectedIndex]) }}</span>
          <span v-for="item in series" :key="item.id">{{ item.label }}：{{ exact(item.values[selectedIndex]) }}</span>
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
      </div>
      <p class="usage-as-of">更新于 {{ asOfLabel }}（北京时间）</p>
    </template>
    <p class="usage-note">按北京时间统计，仅包含功能启用后已记录的模型用量。</p>
  </section>
</template>

<script>
import { getTokenUsage, getStoredUsername, getStoredToken } from '../services/tokenUsage.js'
import { buildUsageChart, labelUsageSeries, formatTokens, formatExactTokens, validateUsageDays } from '../../../shared/utils/tokenUsage.js'

export default {
  data() {
    return {
      days: 7, usage: null, loading: false, error: '', selectedIndex: 0, hiddenIds: [],
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
    chart() { return buildUsageChart(this.usage ? this.usage.daily : { dates: [], series: [] }, this.hiddenIds) },
    selectedPoint() { return this.chart.dates[this.selectedIndex] },
    dateHitWidth() { return Math.min(44, (this.chart.right - this.chart.left) / Math.max(this.chart.dates.length - 1, 1)) },
    hasWindowUsage() { return this.usage && this.usage.daily.totals.some(value => value > 0) },
    asOfLabel() {
      return new Date(this.usage.as_of).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
    },
  },
  mounted() {
    window.addEventListener('auth-changed', this.onAccountChanged)
    window.addEventListener('storage', this.onAccountChanged)
    this.stopAuthListener = () => {
      window.removeEventListener('auth-changed', this.onAccountChanged)
      window.removeEventListener('storage', this.onAccountChanged)
    }
    this.loadUsage()
  },
  beforeUnmount() { this.disposeUsage() },
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
      this.usage = null
      const isCurrent = () => !this.disposed && sequence === this.requestSequence && username === getStoredUsername()
      try {
        const usage = await getTokenUsage(days, controller.signal)
        if (!isCurrent()) return
        if (usage.days !== days) throw new Error('统计范围不匹配，请重试')
        this.usage = usage
        this.selectedIndex = usage.daily.dates.length - 1
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
.usage-header, .usage-range { display: flex; align-items: center; justify-content: space-between; gap: 0.75rem; margin-bottom: 1rem; }
.usage-header h3 { margin: 0 0 0.25rem; font-size: 1rem; }
.usage-header p, .usage-note, .usage-as-of { margin: 0; color: var(--text-secondary); font-size: 0.75rem; line-height: 1.6; }
button { font: inherit; color: var(--text-primary); cursor: pointer; background: var(--bg-secondary); border: 1px solid var(--border-color, #e2e8f0); border-radius: 0.375rem; padding: 0.375rem 0.75rem; }
button:disabled { opacity: 0.6; cursor: wait; }
button:focus-visible, summary:focus-visible, .usage-table-scroll:focus-visible { outline: 2px solid var(--accent-color, #0ea5e9); outline-offset: 2px; }
.usage-range > div { display: flex; gap: 0.25rem; flex-shrink: 0; }
.usage-range button[aria-pressed="true"] { color: var(--accent-color, #0ea5e9); border-color: currentColor; }
.usage-cards { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 0.75rem; margin-bottom: 1rem; }
.usage-card { display: flex; flex-direction: column; gap: 0.5rem; padding: 1rem; border: 1px solid var(--border-color, #e2e8f0); background: var(--bg-secondary); border-radius: 0.5rem; }
.usage-card strong { font-size: 1.5rem; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
.usage-card small { color: var(--text-secondary); font-size: 0.75rem; }
.usage-trend { border: 1px solid var(--border-color, #e2e8f0); border-radius: 0.5rem; padding: 0.75rem; background: var(--bg-secondary); }
.usage-legend { display: flex; flex-wrap: wrap; gap: 0.5rem; max-height: 7rem; overflow-y: auto; }
.usage-legend button { border: 0; padding: 0.25rem; max-width: 100%; overflow-wrap: anywhere; text-align: left; }
.usage-legend .muted { opacity: 0.55; text-decoration: line-through; }
.usage-legend span { color: var(--series-color); }
.series-0 { --series-color: var(--accent-color, #0284c7); }
.series-1 { --series-color: #16a34a; }
.series-2 { --series-color: #d97706; }
.series-3 { --series-color: #e05771; }
.series-4 { --series-color: #8b77d9; }
.series-5 { --series-color: #14a8a4; }
.usage-chart { display: block; width: 100%; height: auto; overflow: visible; margin-top: 0.5rem; }
.chart-grid line { stroke: var(--border-color, #e2e8f0); }
.chart-grid text, .chart-date { fill: var(--text-secondary); font-size: 12px; }
.chart-line { fill: none; stroke: var(--series-color); stroke-width: 2; stroke-linejoin: round; }
.chart-point { fill: var(--series-color); }
.chart-selection { stroke: var(--text-secondary); stroke-dasharray: 3 4; pointer-events: none; }
.date-hit { fill: transparent; cursor: pointer; outline: none; }
.date-hit:focus { fill: var(--accent-color, #0ea5e9); fill-opacity: 0.1; stroke: var(--accent-color, #0ea5e9); stroke-width: 1; }
.usage-tooltip { display: flex; flex-wrap: wrap; gap: 0.5rem 1rem; padding: 0.75rem; border-top: 1px solid var(--border-color, #e2e8f0); font-size: 0.75rem; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.usage-tooltip strong { width: 100%; }
.usage-details { margin-top: 0.5rem; }
.usage-details summary { cursor: pointer; padding: 0.5rem 0; }
.usage-table-scroll { overflow-x: auto; max-height: 24rem; }
table { width: 100%; border-collapse: collapse; white-space: nowrap; font-size: 0.75rem; font-variant-numeric: tabular-nums; }
caption { text-align: left; padding: 0.5rem; color: var(--text-secondary); }
th, td { padding: 0.5rem; border-bottom: 1px solid var(--border-color, #e2e8f0); text-align: right; }
th:first-child { text-align: left; }
.usage-empty, .usage-error { text-align: center; padding: 1rem; color: var(--text-secondary); }
.usage-as-of, .usage-note { margin-top: 0.75rem; }
.skeleton-card, .skeleton-chart { background: var(--bg-tertiary, #e2e8f0); border-radius: 0.5rem; }
.skeleton-card { height: 7rem; }
.skeleton-chart { height: 16rem; margin-bottom: 0.75rem; }
.usage-skeleton > span { color: var(--text-secondary); }
@media (max-width: 48rem) {
  .usage-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .usage-card { padding: 0.75rem; }
  .usage-card strong { font-size: 1.25rem; }
  .usage-range { flex-wrap: wrap; }
  .usage-trend { padding: 0.5rem; }
}
</style>
