// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/compaction/state.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
import { subscribeSessions } from '../sessionManagement/state.js'
// 所有 Chat 实例（主页面和侧边栏）共享会话操作，异步结果始终写回发起会话。
const sessions = new Map()
const listeners = new Set()
export function stateFor(id) {
  if (!sessions.has(id)) sessions.set(id, { records: [], busy: false, running: false, supported: false, reason: '正在检查压缩支持情况' })
  return sessions.get(id)
}
export function notify() { listeners.forEach(fn => fn()) }
export function subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn) }
export function mergeStatus(id, status) {
  const state = stateFor(id)
  const saved = status.compactions || []
  state.records = [...saved, ...state.records.filter(r => !saved.some(s => s.id === r.id))]
  Object.assign(state, { supported: status.supported, busy: status.busy, reason: status.reason })
  notify()
  return state
}
export function commandItems(query, reason, busy) {
  const q = query.trim().toLowerCase()
  return (!q || 'compact 压缩'.includes(q)) ? [{
    dir_name: 'compact', command: true, name: busy ? 'compact · 正在压缩...' : '压缩 compact',
    description: reason || '压缩较早上下文，保留近期内容与原始记录', disabled: !!reason,
  }] : []
}
export function recordsAt(records, messages, index) {
  return records.filter(record => {
    const user = messages.findIndex(m => (m.turn_id || m.id) === record.anchor_turn_id)
    if (user >= 0) {
      const next = messages.findIndex((m, i) => i > user && m.role === 'user')
      return index === (next < 0 ? messages.length - 1 : next - 1)
    }
    // 旧历史由服务端生成稳定 turn ID，UI 尚无该字段时使用保存的历史边界。
    return index === Math.min(messages.length, record.anchor_message_count) - 1
  })
}
export function estimatedUsage(records, messages, usage, streaming) {
  const record = [...records].reverse().find(r => ['compacted', 'skipped'].includes(r.status) && Number.isFinite(r.after_tokens))
  if (!record || messages.length < record.anchor_message_count) return usage
  const added = messages.slice(record.anchor_message_count)
  // 范围预检等本地回复会明确记录零 usage，不能把压缩前最后一次模型用量当新用量。
  // 等待中的 assistant 占位消息尚无 usage，继续估算；收到本轮用量后再恢复供应商统计。
  // 没有 usage 的旧消息无法判断，仍走原路径。
  if (added.some(m => !['user', 'assistant'].includes(m.role) || typeof m.content !== 'string'
    || (m.role === 'assistant' && ((!m.usage && !(streaming && m.loading))
      || ['input_tokens', 'output_tokens', 'reasoning_tokens', 'context_tokens'].some(k => m.usage?.[k] > 0)))
    || (m.tool_calls?.length && !(streaming && m.loading)))) return usage
  const extraTokens = added.reduce((total, m) => {
    const nonAscii = m.content.replace(/[\x00-\x7f]/g, '').length
    return total + Math.ceil((m.content.length + nonAscii * 3) / 4) + 4
  }, 0)
  return { ...usage, context_tokens: record.after_tokens + extraTokens, context_length: record.context_length,
    context_estimated: true, estimate_basis: record.estimate_basis }
}

subscribeSessions(event => { if (event.type === 'reset') { sessions.clear(); notify() } })
