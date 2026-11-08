// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/compaction/mixin.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
import { compactContext, getContextStatus } from '../../api/chat.js'
import { stateFor, subscribe, notify, mergeStatus, recordsAt, estimatedUsage } from './state.js'

function savedRequest(sid) {
  try {
    const raw = sessionStorage.getItem(`compact:${sid}`)
    if (!raw) return null
    return raw.startsWith('{') ? JSON.parse(raw) : { id: raw }
  } catch { return null }
}
function saveRequest(sid, record) {
  try {
    if (record) sessionStorage.setItem(`compact:${sid}`, JSON.stringify(record))
    else sessionStorage.removeItem(`compact:${sid}`)
  } catch { /* 存储被禁用时，进程内依然复用原请求 ID */ }
}
function pending(messages) {
  return messages.some(m => m.pending_approval || [...(m.tool_calls || []), ...(m.blocks || [])].some(t => t.approval_status === 'pending'))
}
export default {
  data() { return { compactionRevision: 0 } },
  computed: {
    contextState() { void this.compactionRevision; return { ...stateFor(this.currentSessionId) } },
    compactBusy() { return this.contextState.running || this.contextState.busy },
    compactReason() {
      if (!this.currentSessionId || !this.messages.length) return '请先开始对话'
      if (this.isStreaming || this.compactBusy) return '会话正在生成或压缩'
      if (pending(this.messages)) return '请先处理待审批操作'
      return this.contextState.reason || (this.contextState.supported ? '' : '当前模式不支持手动压缩')
    },
    compactUsage() { return estimatedUsage(this.contextState.records, this.messages, this.sessionUsage, this.isStreaming) },
  },
  watch: {
    currentSessionId: { immediate: true, handler(sid) { if (sid) this.refreshCompaction(sid) } },
    isStreaming(value) { if (!value && this.currentSessionId) this.refreshCompaction(this.currentSessionId) },
  },
  created() {
    this._unsubscribeCompaction = subscribe(() => { this.compactionRevision++ })
    this._compactionPoll = setInterval(() => {
      const sid = this.currentSessionId
      if (sid && (this.compactBusy || savedRequest(sid) || this.contextState.queryFailed)) this.refreshCompaction(sid)
    }, 3000)
  },
  beforeDestroy() { this.disposeCompaction() },
  methods: {
    compactionNoticeText(record) {
      if (record.status === 'compacted') return 'compact · 已压缩'
      if (record.status === 'running') return 'compact · 正在压缩...'
      if (record.status === 'skipped') return 'compact · 无需压缩'
      return record.message
    },
    disposeCompaction() {
      if (this._unsubscribeCompaction) this._unsubscribeCompaction()
      clearInterval(this._compactionPoll)
    },
    compactionsAt(index) { return recordsAt(this.contextState.records, this.messages, index) },
    async refreshCompaction(sid) {
      const request = savedRequest(sid)
      const previous = stateFor(sid)
      if (request && !previous.records.some(r => r.id === request.id)) {
        previous.records.push({ ...request, status: 'unknown', message: 'compact · 正在查询压缩结果...' })
        previous.busy = true
        notify()
      }
      try {
        const status = await getContextStatus(sid)
        const state = mergeStatus(sid, status)
        state.queryFailed = false
        if (request && !state.running && !state.busy) {
          if (!state.records.some(r => r.id === request.id && ['compacted', 'skipped'].includes(r.status))) {
            const record = state.records.find(r => r.id === request.id)
            if (record) Object.assign(record, { status: 'failed', message: '压缩未提交，可重新选择 compact 重试' })
          }
          saveRequest(sid, null)
          notify()
        }
      } catch {
        const state = stateFor(sid)
        state.queryFailed = true
        state.reason = '无法查询压缩状态，请稍后重试'
        if (!state.running && !savedRequest(sid)) state.supported = false
        notify()
      }
    },
    async runCompact() {
      if (this.compactReason) return
      const sid = this.currentSessionId
      const state = stateFor(sid)
      if (state.running || state.busy) return
      const id = savedRequest(sid)?.id || (globalThis.crypto && globalThis.crypto.randomUUID
        ? globalThis.crypto.randomUUID() : `compact-${Date.now()}-${Math.random().toString(16).slice(2)}`)
      const record = { id, status: 'running', message: 'compact · 正在压缩...',
        anchor_turn_id: [...this.messages].reverse().find(m => m.role === 'user')?.turn_id,
        anchor_message_count: this.messages.length }
      saveRequest(sid, record)
      state.records = [...state.records.filter(r => r.id !== id), record]
      state.running = true
      notify()
      try {
        const result = await compactContext(sid, this.selectedModel, id)
        state.records = [...state.records.filter(r => r.id !== id), result.compaction]
        saveRequest(sid, null)
      } catch (error) {
        const status = error.status || error.response?.status
        if (status >= 400 && status < 500 && status !== 408) {
          Object.assign(record, { status: 'failed', message: error.message || '压缩请求被拒绝，请稍后重试' })
          saveRequest(sid, null)
        } else {
          Object.assign(record, { status: 'unknown', message: `${error.message || '请求中断'}；正在查询原请求结果` })
          state.busy = true // 查询成功前，不能把断线当作未提交。
        }
      } finally {
        state.running = false
        notify()
        await this.refreshCompaction(sid)
      }
    },
  },
}
