// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/visual/envelope.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
/**
 * 数据类工具的图表信封（Vue 2 / Vue 3 共用的纯函数，不依赖 DOM）。
 *
 * 数据类 MCP 工具（如 mcp/jmfx_mcp_server.py）返回统一信封：
 *   { shape, title, summary, unit, stat_date, table: {columns, rows}, chart, notes }
 * 其中 chart 是现成的 ECharts option。工具卡片据此画图（交给沙箱 iframe，见 ./mount.js），
 * 不再只显示原始文本。
 *
 * 工具结果的形态有四种，都要能还原：已解析的对象、JSON 文本、MCP content 数组
 * （[{type:'text', text:'{...}'}]），以及后端 str(list) 落库的 Python repr（单引号、True/None）。
 */

const MAX_RESULT_LENGTH = 2_000_000
const CACHE_LIMIT = 64
const cache = new Map()

function isPlainObject(value) {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

/** Python repr → JSON：逐字符转换字符串定界符，保留标准转义（正文里的撇号不会把 JSON 改坏） */
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
          if (next === quote) out += next === '"' ? '\\"' : "'"
          else if (next === '"' && quote === "'") out += '\\"'
          else if ('"\\/bfnrtu'.indexOf(next) !== -1) out += `\\${next}`
          else out += next
          i += 2
          continue
        }
        if (c === quote) {
          out += '"'
          i += 1
          break
        }
        out += c === '"' ? '\\"' : c
        i += 1
      }
      continue
    }
    if (source.startsWith('True', i)) {
      out += 'true'
      i += 4
      continue
    }
    if (source.startsWith('False', i)) {
      out += 'false'
      i += 5
      continue
    }
    if (source.startsWith('None', i)) {
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
  return Array.isArray(value)
    ? value.filter((item) => isPlainObject(item) && item.type === 'text' && typeof item.text === 'string').map((item) => item.text)
    : []
}

function parseText(text, depth) {
  if (depth > 4 || typeof text !== 'string') return null
  const trimmed = text.trim()
  if (!trimmed || trimmed.length > MAX_RESULT_LENGTH || (trimmed[0] !== '{' && trimmed[0] !== '[')) return null
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
  return unwrap(parsed, depth + 1)
}

function unwrap(value, depth) {
  if (isPlainObject(value)) return value
  if (!Array.isArray(value)) return typeof value === 'string' ? parseText(value, depth) : null
  // MCP content 数组：优先取带 chart 的文本块
  for (const text of textBlocks(value)) {
    const found = parseText(text, depth)
    if (found && found.chart) return found
  }
  return value.find((item) => isPlainObject(item) && item.chart) || null
}

function validChart(chart) {
  return isPlainObject(chart) && (Array.isArray(chart.series) || isPlainObject(chart.series)) ? chart : null
}

function validTable(table) {
  if (!isPlainObject(table) || !Array.isArray(table.columns) || !Array.isArray(table.rows)) return null
  return {
    columns: table.columns.map((c) => String(c == null ? '' : c)),
    rows: table.rows.filter(Array.isArray).map((row) => row.map((cell) => (cell == null ? '' : String(cell)))),
  }
}

function normalize(envelope) {
  const chart = envelope ? validChart(envelope.chart) : null
  if (!chart) return null
  return {
    title: typeof envelope.title === 'string' ? envelope.title : '',
    summary: typeof envelope.summary === 'string' ? envelope.summary : '',
    unit: typeof envelope.unit === 'string' ? envelope.unit : '',
    statDate: envelope.stat_date == null ? '' : String(envelope.stat_date),
    table: validTable(envelope.table),
    notes: Array.isArray(envelope.notes) ? envelope.notes.filter((n) => typeof n === 'string') : [],
    chart,
  }
}

/**
 * 把工具结果还原成图表信封；不是带 chart 的信封时返回 null。
 * 字符串结果按原文缓存（流式期间同一结果会被反复问到）。
 */
export function parseChartEnvelope(result) {
  if (typeof result !== 'string') return normalize(unwrap(result, 0))
  // 大多数工具结果和图表无关：先用子串排除，避免每次重渲都尝试解析
  if (result.indexOf('chart') === -1 || result.length > MAX_RESULT_LENGTH) return null
  if (cache.has(result)) return cache.get(result)
  const envelope = normalize(parseText(result, 0))
  if (cache.size >= CACHE_LIMIT) cache.delete(cache.keys().next().value)
  cache.set(result, envelope)
  return envelope
}

/** 工具结果里是否带可画的图表 */
export function hasChartEnvelope(result) {
  return !!parseChartEnvelope(result)
}
