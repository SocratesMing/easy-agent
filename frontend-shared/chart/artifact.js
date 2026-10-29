/**
 * MCP 图表产物解析（Vue2 / Vue3 共用，纯函数、不依赖 DOM）。
 *
 * 数据类 MCP 工具返回统一信封：
 *   { shape, title, summary, unit, stat_date, table, chart, notes, result_state }
 * 其中 chart 是现成的 ECharts option（业务方定制，前端不再重绘）。
 *
 * 本模块只做四件事：
 *   1. 兼容四种输入：已对象 / JSON 文本 / MCP content 数组 / Python repr（历史数据）
 *   2. 只保留白名单内的 option 结构，做基础清洗与规模上限
 *   3. 暗色主题 patch（只改文字/轴线/分割线，业务配色不动）
 *   4. 卡片高度估算
 *
 * 约束：不得 import echarts；除 isDarkTheme 的浏览器探测外不访问 DOM，
 * 保证 node --test 可直接运行（Node 下 isDarkTheme 返回 false）。
 */

const MAX_RESULT_LENGTH = 2_000_000
const MAX_SERIES_COUNT = 24
const MAX_DATA_POINTS = 1000
const ALLOWED_SERIES_TYPES = new Set(['bar', 'line', 'pie'])
const TOP_LEVEL_KEYS = [
  'title', 'tooltip', 'grid', 'legend', 'color', 'series',
  'xAxis', 'yAxis', 'dataset', 'animation', 'textStyle',
]

const DARK_TEXT = '#E6E4DC'
const DARK_SUB_TEXT = '#B8B6AE'
const DARK_LINE = '#4A4842'
const DARK_SPLIT = '#33322E'
const DARK_PIE_BORDER = '#1F1E1C'

function isPlainObject(value) {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

/** 文本清洗：去控制字符并限长。保留 `<`/`>` 等业务字符（tooltip 已强制 richText，画布不会解析 HTML） */
function cleanString(value, maxLength = 200) {
  if (value === null || value === undefined) return ''
  const text = String(value).replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, '')
  const clipped = text.length > maxLength ? `${text.slice(0, maxLength)}…` : text
  return clipped.trim()
}

function cloneJson(value) {
  try {
    return JSON.parse(JSON.stringify(value))
  } catch (e) {
    return null
  }
}

/**
 * Python repr → JSON。
 * 历史消息里的 MCP 结果是 `str(m.content)`，形如：
 *   [{'type': 'text', 'text': '{"shape": "table+chart", ...}'}]
 * Python 用单引号包字符串、内部双引号不转义，直接 JSON.parse 会失败。
 * 这里用字符扫描转换字符串定界符，保留标准转义；不做正则全局替换，
 * 避免正文里的撇号（'）把 JSON 改坏。
 */
function pythonLiteralToJson(source) {
  let out = ''
  let i = 0
  while (i < source.length) {
    const ch = source[i]
    if (ch === "'" || ch === '"') {
      const quote = ch
      i += 1
      out += '"'
      while (i < source.length) {
        const c = source[i]
        if (c === '\\') {
          const next = source[i + 1]
          if (next === undefined) {
            i += 1
            break
          }
          if (next === quote) {
            out += next === '"' ? '\\"' : "'"
          } else if (next === '"' && quote === "'") {
            out += '\\"'
          } else if ('"\\/bfnrtu'.indexOf(next) !== -1) {
            out += `\\${next}`
          } else {
            // 非 JSON 支持的转义（如 \'）：去掉反斜杠，保留字符
            out += next
          }
          i += 2
          continue
        }
        if (c === quote) {
          out += '"'
          i += 1
          break
        }
        if (c === '"') {
          out += '\\"'
          i += 1
          continue
        }
        out += c
        i += 1
      }
      continue
    }
    if (ch === 'T' && source.startsWith('True', i)) {
      out += 'true'
      i += 4
      continue
    }
    if (ch === 'F' && source.startsWith('False', i)) {
      out += 'false'
      i += 5
      continue
    }
    if (ch === 'N' && source.startsWith('None', i)) {
      out += 'null'
      i += 4
      continue
    }
    out += ch
    i += 1
  }
  return out
}

function textBlocks(value) {
  if (!Array.isArray(value)) return []
  return value
    .filter((item) => isPlainObject(item) && item.type === 'text' && typeof item.text === 'string')
    .map((item) => item.text)
}

function parseCandidate(text, depth) {
  if (depth > 4 || typeof text !== 'string') return null
  const trimmed = text.trim()
  if (!trimmed || trimmed.length > MAX_RESULT_LENGTH) return null
  if (trimmed[0] !== '[' && trimmed[0] !== '{') return null

  let parsed
  try {
    parsed = JSON.parse(trimmed)
  } catch (e) {
    try {
      parsed = JSON.parse(pythonLiteralToJson(trimmed))
    } catch (e2) {
      return null
    }
  }

  if (Array.isArray(parsed)) {
    // MCP content 数组：优先找带 chart 的文本块，找不到再退回第一个可解析对象
    let fallback = null
    for (const block of textBlocks(parsed)) {
      const found = parseCandidate(block, depth + 1)
      if (!found) continue
      if (found.chart) return found
      if (!fallback) fallback = found
    }
    if (fallback) return fallback
    const direct = parsed.find((item) => isPlainObject(item) && item.chart)
    return direct || null
  }
  return isPlainObject(parsed) ? parsed : null
}

/**
 * 把任意工具结果还原成信封对象。
 * @returns {object|null}
 */
function unwrapMcpPayload(raw) {
  if (isPlainObject(raw)) return raw
  if (Array.isArray(raw)) {
    let fallback = null
    for (const block of textBlocks(raw)) {
      const found = parseCandidate(block, 0)
      if (!found) continue
      if (found.chart) return found
      if (!fallback) fallback = found
    }
    if (fallback) return fallback
    return raw.find((item) => isPlainObject(item) && item.chart) || null
  }
  if (typeof raw !== 'string') return null
  if (raw.length > MAX_RESULT_LENGTH) return null
  return parseCandidate(raw, 0)
}

function sanitizeAxis(axis) {
  const list = Array.isArray(axis) ? axis : [axis]
  for (const item of list) {
    if (!isPlainObject(item)) continue
    if (typeof item.name === 'string') item.name = cleanString(item.name, 40)
    if (Array.isArray(item.data)) {
      item.data = item.data.map((entry) => {
        if (typeof entry === 'string') return cleanString(entry, 120)
        if (isPlainObject(entry) && typeof entry.value === 'string') {
          return { ...entry, value: cleanString(entry.value, 120) }
        }
        return entry
      })
    }
  }
}

function sanitizeSeries(series) {
  for (const item of series) {
    if (!isPlainObject(item)) continue
    if (typeof item.name === 'string') item.name = cleanString(item.name, 60)
    if (!Array.isArray(item.data)) continue
    item.data = item.data.map((entry) => {
      if (typeof entry === 'string') return cleanString(entry, 120)
      if (isPlainObject(entry)) {
        const next = { ...entry }
        if (typeof next.name === 'string') next.name = cleanString(next.name, 60)
        if (typeof next.value === 'string') next.value = cleanString(next.value, 120)
        return next
      }
      return entry
    })
  }
}

function sanitizeTitle(title) {
  if (isPlainObject(title)) {
    if (typeof title.text === 'string') title.text = cleanString(title.text, 120)
    if (typeof title.subtext === 'string') title.subtext = cleanString(title.subtext, 120)
    // 标题跳转链接不是图表展示所需，移除以避免 javascript: 等外链
    delete title.link
    delete title.sublink
    return
  }
  if (Array.isArray(title)) title.forEach(sanitizeTitle)
}

function forceSafeTooltip(tooltip) {
  const patch = (item) => (
    isPlainObject(item)
      ? { ...item, renderMode: 'richText', confine: true, appendToBody: false }
      : item
  )
  if (Array.isArray(tooltip)) return tooltip.map(patch)
  return patch(tooltip || { trigger: 'item' })
}

/**
 * 把 MCP 的 chart 字段归一化成可安全渲染的 ECharts option。
 * @returns {object|null} 无合法 series 时返回 null
 */
export function normalizeChartOption(chart) {
  if (!isPlainObject(chart)) return null
  const cloned = cloneJson(chart)
  if (!isPlainObject(cloned)) return null

  const option = {}
  for (const key of TOP_LEVEL_KEYS) {
    if (cloned[key] !== undefined) option[key] = cloned[key]
  }

  const source = Array.isArray(option.series) ? option.series : []
  const series = source
    .filter((item) => isPlainObject(item) && ALLOWED_SERIES_TYPES.has(item.type))
    .slice(0, MAX_SERIES_COUNT)
  if (series.length === 0) return null
  for (const item of series) {
    if (Array.isArray(item.data) && item.data.length > MAX_DATA_POINTS) {
      item.data = item.data.slice(0, MAX_DATA_POINTS)
    }
  }
  option.series = series

  sanitizeTitle(option.title)
  sanitizeAxis(option.xAxis)
  sanitizeAxis(option.yAxis)
  sanitizeSeries(series)
  option.tooltip = forceSafeTooltip(option.tooltip)

  return option
}

function patchTitle(title) {
  if (!isPlainObject(title)) return
  title.textStyle = { ...(title.textStyle || {}), color: DARK_TEXT }
  if (isPlainObject(title.subtextStyle)) {
    title.subtextStyle = { ...title.subtextStyle, color: DARK_SUB_TEXT }
  }
}

/**
 * 暗色主题 patch：只调整文字/轴线/分割线颜色，系列配色保持业务原样。
 * 不修改入参（每次返回新对象），保证明暗切换可逆。
 */
export function applyChartTheme(option, dark) {
  const next = cloneJson(option)
  if (!isPlainObject(next)) return option
  if (!dark) return next

  if (Array.isArray(next.title)) next.title.forEach(patchTitle)
  else patchTitle(next.title)

  if (isPlainObject(next.legend)) {
    next.legend.textStyle = { ...(next.legend.textStyle || {}), color: DARK_SUB_TEXT }
  }

  const tooltips = Array.isArray(next.tooltip) ? next.tooltip : [next.tooltip]
  for (const tooltip of tooltips) {
    if (!isPlainObject(tooltip)) continue
    tooltip.backgroundColor = 'rgba(31,30,28,0.96)'
    tooltip.borderColor = DARK_LINE
    tooltip.textStyle = { ...(tooltip.textStyle || {}), color: DARK_TEXT }
  }

  const axes = []
  for (const key of ['xAxis', 'yAxis']) {
    const value = next[key]
    if (Array.isArray(value)) axes.push(...value)
    else if (isPlainObject(value)) axes.push(value)
  }
  for (const axis of axes) {
    if (!isPlainObject(axis)) continue
    if (isPlainObject(axis.axisLabel)) {
      axis.axisLabel = { ...axis.axisLabel, color: DARK_SUB_TEXT }
    }
    if (isPlainObject(axis.nameTextStyle)) {
      axis.nameTextStyle = { ...axis.nameTextStyle, color: DARK_SUB_TEXT }
    }
    if (isPlainObject(axis.axisLine) && isPlainObject(axis.axisLine.lineStyle)) {
      axis.axisLine.lineStyle = { ...axis.axisLine.lineStyle, color: DARK_LINE }
    }
    if (isPlainObject(axis.splitLine) && isPlainObject(axis.splitLine.lineStyle)) {
      axis.splitLine.lineStyle = { ...axis.splitLine.lineStyle, color: DARK_SPLIT }
    }
  }

  for (const item of Array.isArray(next.series) ? next.series : []) {
    if (!isPlainObject(item)) continue
    if (isPlainObject(item.label)) {
      item.label = { ...item.label, color: DARK_SUB_TEXT }
    }
    if (isPlainObject(item.labelLine) && isPlainObject(item.labelLine.lineStyle)) {
      item.labelLine = { ...item.labelLine, lineStyle: { ...item.labelLine.lineStyle, color: DARK_SUB_TEXT } }
    }
    if (item.type === 'pie' && isPlainObject(item.itemStyle)) {
      item.itemStyle = { ...item.itemStyle, borderColor: DARK_PIE_BORDER }
    }
  }

  if (isPlainObject(next.textStyle)) {
    next.textStyle = { ...next.textStyle, color: DARK_TEXT }
  }
  return next
}

/** 按类目数估算图表高度：横向排名条图需要更高，其余固定 320px */
export function estimateChartHeight(option) {
  if (!isPlainObject(option)) return 320
  const yAxis = Array.isArray(option.yAxis) ? option.yAxis[0] : option.yAxis
  const categories = isPlainObject(yAxis) && yAxis.type === 'category' && Array.isArray(yAxis.data)
    ? yAxis.data.length
    : 0
  const hasBar = (Array.isArray(option.series) ? option.series : [])
    .some((item) => isPlainObject(item) && item.type === 'bar')
  if (categories > 0 && hasBar) {
    return Math.max(240, Math.min(560, 96 + categories * 34))
  }
  return 320
}

/** 当前页面是否暗色主题（两套前端都通过 :root[data-theme="dark"] 标记） */
export function isDarkTheme() {
  if (typeof document === 'undefined' || !document.documentElement) return false
  const root = document.documentElement
  return root.getAttribute('data-theme') === 'dark' || root.classList.contains('dark')
}

function normalizeTable(table) {
  if (!isPlainObject(table)) return null
  const columns = Array.isArray(table.columns)
    ? table.columns.slice(0, 30).map((item) => cleanString(item, 60))
    : []
  const rows = Array.isArray(table.rows)
    ? table.rows.slice(0, 500).map((row) => (
      Array.isArray(row) ? row.slice(0, 30).map((cell) => cleanString(cell, 120)) : []
    ))
    : []
  if (columns.length === 0 && rows.length === 0) return null
  return { columns, rows }
}

/**
 * 从工具结果里提取图表产物。无 chart / 结构不合法时返回 null（调用方保持文本卡片）。
 *
 * @param {string|object|Array} rawResult ToolCallCard 的 result 或 SSE 原始结果
 * @returns {{
 *   kind: 'chart', title: string, summary: string, unit: string,
 *   statDate: string, notes: string[], table: {columns: string[], rows: string[][]}|null,
 *   option: object, height: number
 * }|null}
 */
export function extractChartArtifact(rawResult) {
  if (typeof rawResult === 'string') {
    // 性能闸门：非图表结果（命令输出、搜索文本等）直接跳过，避免对大段文本做 JSON 解析
    if (rawResult.length > MAX_RESULT_LENGTH || rawResult.indexOf('chart') === -1) return null
  }
  const payload = unwrapMcpPayload(rawResult)
  if (!isPlainObject(payload)) return null
  const option = normalizeChartOption(payload.chart)
  if (!option) return null

  const notes = Array.isArray(payload.notes)
    ? payload.notes.slice(0, 6).map((item) => cleanString(item, 240)).filter(Boolean)
    : []

  return {
    kind: 'chart',
    title: cleanString(payload.title, 120),
    summary: cleanString(payload.summary, 600),
    unit: cleanString(payload.unit, 24),
    statDate: cleanString(payload.stat_date, 40),
    notes,
    table: normalizeTable(payload.table),
    option,
    height: estimateChartHeight(option),
  }
}

/** 卡片头部副信息：日期 · 单位 */
export function formatArtifactMeta(artifact) {
  if (!artifact) return ''
  return [artifact.statDate, artifact.unit].filter(Boolean).join(' · ')
}
