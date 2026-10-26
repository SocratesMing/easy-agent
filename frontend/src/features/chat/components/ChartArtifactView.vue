<template>
  <!-- MCP 图表产物：标题/摘要来自信封，主体为 ECharts 画布，数据表可选折叠 -->
  <div class="chart-artifact" role="img" :aria-label="artifact.title || '图表'">
    <div ref="canvas" class="chart-artifact-canvas" :style="{ height: height + 'px' }"></div>
    <div v-if="error" class="chart-artifact-error">{{ error }}</div>
    <p v-if="artifact.summary" class="chart-artifact-summary">{{ artifact.summary }}</p>
    <ul v-if="artifact.notes && artifact.notes.length" class="chart-artifact-notes">
      <li v-for="(note, i) in artifact.notes" :key="i">{{ note }}</li>
    </ul>
    <details v-if="artifact.table" class="chart-artifact-table">
      <summary>查看数据表（{{ artifact.table.rows.length }} 行）</summary>
      <div class="chart-artifact-table-scroll">
        <table>
          <thead>
            <tr>
              <th v-for="(col, i) in artifact.table.columns" :key="i">{{ col }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, ri) in artifact.table.rows" :key="ri">
              <td v-for="(cell, ci) in row" :key="ci">{{ cell }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </details>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { isDarkTheme } from '../../../shared/chart/artifact.js'

const props = defineProps({
  artifact: { type: Object, required: true },
})

const canvas = ref(null)
const error = ref('')
// 高度在解析阶段按类目数算好；缺省时退回 320，避免历史数据没有 height 字段时高度塌陷
const height = computed(() => (props.artifact && props.artifact.height) || 320)

// 非响应式实例状态：token 防止异步 import 晚到后挂到已销毁实例
let chartHandle = null
let token = 0
let themeObserver = null

async function renderChart() {
  disposeChart()
  const myToken = token
  error.value = ''
  if (!props.artifact || !props.artifact.option) {
    error.value = '图表数据缺失'
    return
  }
  await nextTick()
  const el = canvas.value
  if (!el || myToken !== token) return
  try {
    const { mountChart } = await import('../../../shared/chart/echarts.js')
    if (myToken !== token) return
    const handle = await mountChart(el, props.artifact.option, { dark: isDarkTheme() })
    if (myToken !== token) {
      handle.dispose()
      return
    }
    chartHandle = handle
  } catch (e) {
    console.error('[ChartArtifactView] 图表渲染失败:', e)
    error.value = '图表渲染失败，详细信息见浏览器控制台'
  }
}

function disposeChart() {
  token += 1
  if (chartHandle) {
    chartHandle.dispose()
    chartHandle = null
  }
}

function observeTheme() {
  if (typeof MutationObserver === 'undefined') return
  themeObserver = new MutationObserver(() => {
    if (chartHandle) chartHandle.setTheme(isDarkTheme())
  })
  themeObserver.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['data-theme'],
  })
}

onMounted(() => {
  renderChart()
  observeTheme()
})

onBeforeUnmount(() => {
  disposeChart()
  if (themeObserver) {
    themeObserver.disconnect()
    themeObserver = null
  }
})

watch(() => props.artifact, () => renderChart())
</script>

<style scoped>
.chart-artifact { margin: 2px 0 4px; }
.chart-artifact-canvas {
  width: 100%;
  min-height: 240px;
  box-sizing: border-box;
  border: 1px solid var(--border-color, #e2e8f0);
  border-radius: 6px;
  background: var(--bg-secondary, #fff);
}
.chart-artifact-error { margin-top: 6px; font-size: 12px; color: #dc2626; }
.chart-artifact-summary {
  margin: 8px 2px 0;
  font-size: 12px;
  line-height: 1.7;
  color: var(--text-secondary, #64748b);
  white-space: pre-wrap;
}
.chart-artifact-notes {
  margin: 6px 0 0;
  padding-left: 18px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-secondary, #64748b);
}
.chart-artifact-table { margin-top: 8px; font-size: 12px; color: var(--text-secondary, #64748b); }
.chart-artifact-table summary { cursor: pointer; user-select: none; }
.chart-artifact-table-scroll {
  margin-top: 6px;
  max-height: 260px;
  overflow: auto;
  border: 1px solid var(--border-color, #e2e8f0);
  border-radius: 6px;
}
.chart-artifact-table table { width: 100%; border-collapse: collapse; }
.chart-artifact-table th,
.chart-artifact-table td {
  padding: 5px 8px;
  border-bottom: 1px solid var(--border-color, #e2e8f0);
  text-align: left;
  white-space: nowrap;
}
.chart-artifact-table th {
  position: sticky;
  top: 0;
  background: var(--bg-tertiary, #f1f5f9);
  font-weight: 500;
}
.chart-artifact-table tr:last-child td { border-bottom: none; }
</style>
