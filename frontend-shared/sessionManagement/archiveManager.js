import { listSessions, countSessions, setSessionArchived, deleteSession } from '../../api/chat.js'
import { chatStore, chatBus } from '../../features/chat/store/chatStore.js'
import { archiveChanged, loadDailySessions, sessionGuard, subscribeSessions, isStaleSessionError } from './state.js'
import { createAutoDismiss } from '../../utils/autoDismiss.js'

const busEmit = (event, payload) => (chatBus.$emit || chatBus.emit).call(chatBus, event, payload)
export default {
  props: { open: Boolean },
  data() { return { rows: [], total: 0, query: '', page: 1, loading: false, readError: '', notice: '', busy: {}, confirmMessage: '', confirmTitle: '' } },
  computed: { pages() { return Math.max(1, Math.ceil(this.total / 20)) } },
  watch: {
    open(value) { if (value) { this.page = 1; this.query = ''; this.showNotice(''); this.reload() } else this.invalidate() },
    query() { this.page = 1; this.reload() },
  },
  created() {
    this._requestVersion = 0
    this._archiveDisposed = false
    this._dismissNotice = createAutoDismiss({ read: () => this.notice, clear: () => { this.notice = '' }, ttlMs: 3000 })
    this._offSessions = subscribeSessions(event => {
      if (event.type === 'reset') {
        this.invalidate(); this.rows = []; this.total = 0; this.busy = {}; this.showNotice('')
        this.$emit('close')
      }
    })
  },
  beforeDestroy() { this.disposeArchive() },
  methods: {
    invalidate() { this._requestVersion++; this.loading = false; this.readError = '' },
    disposeArchive() {
      this._archiveDisposed = true
      this._dismissNotice(''); this.notice = ''
      this.invalidate(); this._offSessions?.()
    },
    showNotice(message) {
      if (this._archiveDisposed) return
      this.notice = message
      this._dismissNotice(message)
    },
    async reload() {
      const version = ++this._requestVersion, valid = sessionGuard()
      const filters = { archived: true, q: this.query.trim() }
      this.loading = true; this.readError = ''
      try {
        const [rows, count] = await Promise.all([listSessions(null, { ...filters, limit: 20, offset: (this.page - 1) * 20 }), countSessions(filters)])
        if (!valid() || version !== this._requestVersion || !this.open) return
        this.total = count.total_sessions
        if (this.page > this.pages) { this.page = this.pages; return this.reload() }
        this.rows = rows
      } catch (error) {
        if (valid() && version === this._requestVersion && this.open) { this.rows = []; this.readError = error.message || '读取归档失败' }
      } finally { if (version === this._requestVersion) this.loading = false }
    },
    goPage(page) { this.page = page; this.reload() },
    async confirmAction(title, message) {
      this.confirmTitle = title; this.confirmMessage = message
      await this.$nextTick()
      return this.$refs.confirm.show()
    },
    async archive(session) {
      const sid = session.session_id
      if (this.busy[sid]) return
      this.busy = { ...this.busy, [sid]: true }
      const valid = sessionGuard(sid)
      try {
        if (!await this.confirmAction('归档会话', `将「${session.title || '未命名会话'}」移至归档？消息和文件会保留，可在归档管理中取消归档。`) || !valid()) return
        await this.changeArchive(sid, true, valid)
      } finally { this.busy = { ...this.busy, [sid]: false } }
    },
    async operate(session, remove = false) {
      const sid = session.session_id // bind the confirmation to this exact ID
      if (this.busy[sid]) return
      this.busy = { ...this.busy, [sid]: true }
      const valid = sessionGuard(sid), sameUser = sessionGuard()
      try {
        if (remove) {
          if (!await this.confirmAction('删除会话', `确定删除「${session.title || '未命名会话'}」吗？该会话的消息和文件将被删除，删除后无法恢复。`) || !valid()) return
          await deleteSession(sid)
          // deleteSession installs a tombstone, so use the auth-only guard here.
          if (!sameUser()) return
          chatStore.pendingApprovalSessions = chatStore.pendingApprovalSessions.filter(id => id !== sid)
          chatStore.streamingSessions = chatStore.streamingSessions.filter(id => id !== sid)
          chatStore.sessions = chatStore.sessions.filter(s => s.session_id !== sid)
          busEmit('session-removed', { sessionId: sid, origin: 'archive-manager' })
          this.showNotice('已删除')
          await this.refreshAfterWrite()
        } else await this.changeArchive(sid, false, valid)
      } catch (error) { if (sameUser() && !isStaleSessionError(error)) this.showNotice(error.message || '操作失败') }
      finally { this.busy = { ...this.busy, [sid]: false } }
    },
    async changeArchive(sid, archived, valid) {
      try {
        await setSessionArchived(sid, archived)
        if (!valid()) return
      } catch (error) { if (valid()) this.showNotice(error.message || '归档操作失败'); return }
      archiveChanged(sid, archived)
      chatStore.sessions = chatStore.sessions.filter(s => s.session_id !== sid)
      this.showNotice(archived ? '已归档' : '已取消归档')
      await this.refreshAfterWrite()
    },
    async refreshAfterWrite() {
      // Report refresh failures separately: the write has already succeeded.
      const valid = sessionGuard()
      const results = await Promise.allSettled([
        loadDailySessions(() => listSessions(null, { archived: false }), rows => { chatStore.sessions = rows }),
        this.open ? this.reload() : Promise.resolve(),
      ])
      if (valid() && results.some(r => r.status === 'rejected')) this.showNotice([this.notice, '日常列表刷新失败，请重试'].filter(Boolean).join('；'))
    },
  },
}
