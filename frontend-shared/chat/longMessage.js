// 单轮输入本身就超过模型窗口时，「压缩上下文」帮不上忙（它只压缩历史消息）。
// 这里把超长正文另存为会话工作区的 txt，消息里只留开头一小段 + 文件引用，
// 由 agent 用 read_file(limit/offset) / grep 分块读取，避免一次性把整段塞进 prompt。

// 后端未拿到模型配置时的窗口默认值（easy_agent get_model_context_limit 同为 131072）
export const CONTEXT_WINDOW_FALLBACK = 131072
// 给模型输出留的余量
const OUTPUT_RESERVE_TOKENS = 8192
// 新会话还没用量时，按 system prompt + 技能/工具定义 + 会话元信息的固定开销估
const BASE_OVERHEAD_TOKENS = 8192
// 估算误差、工具返回回灌再留 15%
const SAFETY_RATIO = 0.15
// 本轮输入吃掉可用预算的一半就提示：再往上，加上技能与历史的实际占用很可能溢出
const WARN_RATIO = 0.5
// 太短的一轮输入不打扰（约 2000 个汉字）
const MIN_WARN_TOKENS = 2000

// 摘要里保留的开头长度：够用户认出自己发的是什么，也不至于把窗口再撑起来
const HEAD_KEEP_CHARS = 200
// 超长单行的切分宽度：read_file 按行分页（默认 100 行），一整段没有换行的输入
// 会被一口气读回来，等于把刚降下来的输入又顶回窗口
const LONG_LINE_CHARS = 500

/** token 估算：与 compaction/state.js 同口径（UTF-8 字节数 / 4，一个汉字约 1 token） */
export function estimateTokens(text) {
  const source = String(text || '')
  let nonAscii = 0
  for (const char of source) if (char.charCodeAt(0) > 0x7f) nonAscii += 1
  return Math.ceil((source.length + nonAscii * 3) / 4)
}

/**
 * 本轮还能塞进多少 token：窗口 - 已用 - 输出预留 - 安全余量。
 * 自动压缩阈值比窗口小时以它为上限（压缩后仍要留出本轮输入的位置）。
 */
export function contextBudget({ contextLength, usedTokens, autoCompressTokens } = {}) {
  const window = Number(contextLength) > 0 ? Number(contextLength) : CONTEXT_WINDOW_FALLBACK
  const compressAt = Number(autoCompressTokens)
  const cap = compressAt > 0 ? Math.min(window, compressAt) : window
  const used = Number(usedTokens) > 0 ? Number(usedTokens) : BASE_OVERHEAD_TOKENS
  const reserve = OUTPUT_RESERVE_TOKENS + Math.round(cap * SAFETY_RATIO)
  return Math.max(0, cap - used - reserve)
}

/** 是否建议另存为附件：按本轮输入估算的 token 与剩余预算比较（纯前端，不发请求） */
export function needsOffload(text, usage = {}) {
  const tokens = estimateTokens(text)
  if (tokens < MIN_WARN_TOKENS) return false
  return tokens >= contextBudget(usage) * WARN_RATIO
}

/** 窗口短标签：128k，避免在窄条里写 131072 */
export function formatContextWindow(tokens) {
  const total = Number(tokens) > 0 ? Number(tokens) : CONTEXT_WINDOW_FALLBACK
  return total >= 1000 ? `${Math.round(total / 1000)}k` : String(total)
}

/** 已用百分比：会话还没用量时返回 0，供提示里决定要不要展示 */
export function usagePercent({ contextLength, usedTokens } = {}) {
  const window = Number(contextLength) > 0 ? Number(contextLength) : CONTEXT_WINDOW_FALLBACK
  const used = Math.max(0, Number(usedTokens) || 0)
  return Math.min(100, Math.round((used / window) * 100))
}

/** 字数展示：中文习惯用「万」，避免 217387 这种一长串数字难读 */
export function formatCharCount(count) {
  const total = Number(count) || 0
  if (total < 10000) return `${total} 字`
  const wan = total / 10000
  return `${wan >= 100 ? Math.round(wan) : Number(wan.toFixed(1))} 万字`
}

/** 落盘正文：只把超长单行切成 ~500 字一段，已有的换行原样保留（段级切分不影响语义） */
export function longMessageFileContent(text, width = LONG_LINE_CHARS) {
  return String(text || '')
    .split('\n')
    .map(line => {
      if (line.length <= width) return line
      const parts = []
      for (let i = 0; i < line.length; i += width) parts.push(line.slice(i, i + width))
      return parts.join('\n')
    })
    .join('\n')
}

/** 落盘名带时间戳：工作区按文件名去重，同名会互相覆盖（见 uploads._safe_stem） */
export function longMessageFileName(now = new Date()) {
  const pad = value => String(value).padStart(2, '0')
  const date = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}`
  const time = `${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
  return `长文本-${date}-${time}.txt`
}

/** 替换后的正文：保留开头一小段，并说明全文在附件里，避免用户以为内容丢了 */
export function longMessageNotice(text, fileName) {
  const trimmed = String(text || '').trim()
  const head = trimmed.slice(0, HEAD_KEEP_CHARS)
  const ellipsis = trimmed.length > HEAD_KEEP_CHARS ? '……' : ''
  return `${head}${ellipsis}\n\n（以上内容较长，已存为附件 ${fileName}，请阅读该文件后再回答。）`
}
