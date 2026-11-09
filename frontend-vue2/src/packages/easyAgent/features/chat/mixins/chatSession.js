import { connectSessionView } from '../../../shared/sessionManagement/viewLifecycle.js'
import { loadDailySessions, sessionGuard, isSessionDeleted, isStaleSessionError } from '../../../shared/sessionManagement/state.js'
import { setReactive } from '../../../utils/reactiveSet.js'
import { parseMCPResult } from '../../../utils/mcpResult.js'
import { createSession, listSessions, getChatHistory, deleteSession, sendMessage, resumeStream, renameSession, togglePinSession, attachStream, getStreamStatus } from '../../../api/chat.js'
import { deleteFile, getSessionGeneratedFiles } from '../../../api/files.js'
import { authFetch } from '../../../api/auth.js'
import { chatBus, chatStore, syncPendingApprovalFlag as setPendingApprovalFlag } from '../store/chatStore.js'
import { createStreamChunkHandler as createChunkHandler, resumeUsageBase } from '../services/chunkHandler.js'
import { hasAssistantReply } from '../services/streamResume.js'
import streamLifecycle from './streamLifecycle.js'
import { applyApprovalRequests, buildResumeDecisions, derivePendingApproval, markApprovalDecision } from '../../../utils/hitlApproval.js'

// 会话级界面字段 → sessionStates 缓存字段，后台流事件期间换入（runInSession）；含 pendingApproval：多会话同时待审批时各自持有
const SESSION_FIELDS = [['messages', 'messages'], ['sessionUsage', 'sessionUsage'], ['sessionDuration', 'sessionDuration'], ['iterationCount', 'iterationCount'], ['currentTodos', 'todos'], ['streamingAssistantId', 'streamingAssistantId'], ['pendingApproval', 'pendingApproval'], ['error', 'error']]

// MCP 工具结果解析：正常流与 HITL 恢复流共用（App.vue 经本模块引用）
export { parseMCPResult }

/**
 * Vue 2 无法检测「数组索引赋值」（arr[i] = x）与「对象属性新增」（obj.k = x），
 * 只有 push/splice 等变异方法和 Vue.set 才会触发更新。Vue 3 基于 Proxy 没有这个限制，
 * 因此迁移后必须统一走这里写入，否则流式内容不会逐步渲染。
 */

/**
 * 对话引擎：单个"展示中会话"的消息、流式事件处理、HITL 审批、会话 CRUD。
 *
 * 主页面（App.vue）与 AI 侧边栏（AiSidebar.vue）各混入一份，彼此独立展示/流式，
 * 会话列表与"进行中"集合通过 chatStore 共享。宿主组件可提供以下钩子：
 * - beforeSessionChange(kind)：新建（'create'）/切换（'select'）会话前调用
 * - getPageContextForSend()：返回随消息发送给模型的页面上下文（字符串或 null）
 * - selectedModel：发送时使用的模型
 */
export default {
  mixins: [streamLifecycle],
  data() {
    return {
      currentSessionId: null,
      currentSessionHasFiles: false,
      // 切换会话正在拉取历史：期间不渲染空欢迎页，避免"先闪空会话页再出历史"
      sessionLoading: false,
      // 会话状态缓存：为每个会话保存独立的流式状态
      sessionStates: {},
      // 当前界面上展示的数据（messages/sessionUsage 等）实际所属的会话 ID。
      // 与 currentSessionId 的区别：切换会话后、历史数据加载完成前，currentSessionId 已指向新会话，
      // 但界面数据仍属于旧会话。保存缓存必须以 loadedSessionId 为 key，否则会把旧数据/清零的用量
      // 错误地存到新会话名下，导致来回切换时 token 用量显示为 0。
      loadedSessionId: null,
      messages: [],
      // 当前正在流式输出的 assistant 消息 id（组件级，供 handleStop 等跨函数使用；
      // 之前误引用 handleSendMessage 的局部变量 assistantMsgId 导致停止按钮抛 ReferenceError）
      streamingAssistantId: null,
      error: null,
      // 联网开关镜像（真身在 ChatInput）：重试等不经过输入框的发送路径从这里取状态，
      // 否则 handleSendMessage 的 enableWebSearch 会落回默认值 false。
      webSearchEnabled: false,
      currentAbortController: null,
      // HITL: 审批待处理状态，存储 { threadId, assistantMsgId }
      pendingApproval: null,
      sessionUsage: { input_tokens: 0, output_tokens: 0, reasoning_tokens: 0, context_length: chatStore.contextLength, auto_compress_tokens: null, context_tokens: 0 },
      // 当前会话累计耗时（秒），每次 AI 回复完成后累加
      sessionDuration: 0,
      // 当前会话累计迭代次数（step 数），每次 AI 回复完成后累加
      iterationCount: 0,
      currentTodos: [],
      // 本实例已建立实时挂载的会话及模式（避免重复挂载/重复回放）：
      // { [sessionId]: 'displayed' | 'background' }
      attachedStreamingSessions: {},
      scrollTrigger: 0,
      // 非响应式定时器句柄
      filesCheckTimer: null,
    }
  },
  computed: {
    sessions: {
      get() { return chatStore.sessions },
      set(v) { chatStore.sessions = v },
    },
    streamingSessions: {
      get() { return chatStore.streamingSessions },
      set(v) { chatStore.streamingSessions = v },
    },
    pendingApprovalSessions: {
      get() { return chatStore.pendingApprovalSessions },
      set(v) { chatStore.pendingApprovalSessions = v },
    },
    lastStreamingSession: {
      get() { return chatStore.lastStreamingSession },
      set(v) { chatStore.lastStreamingSession = v },
    },
    currentSessionCreatedAt() {
      if (!this.currentSessionId) return null
      const session = this.sessions.find(s => s.session_id === this.currentSessionId)
      return session && session.created_at ? session.created_at : null
    },
    // 输入框状态：当前展示的会话正在流式时才显示停止按钮与 token 用量
    isStreaming() {
      return this.isSessionStreaming(this.currentSessionId)
    },
  },
  watch: {
    // HITL 历史恢复：切换/重载会话后重建待审批状态；若这一轮停在「只有提问、
    // 没有答复」（刷新落在半途），顺便把兜底盯梢也挂上——会话被重新加载时
    // 只有这条路径一定会执行
    loadedSessionId() {
      this.reconstructPendingApproval()
      this.resumeInterruptedTurn()
    },
  },
  created() {
    chatBus.$on('stream-started', this.onPeerStreamStarted)
    this._offSessionState = connectSessionView(this)
    chatBus.$on('session-removed', this.onPeerSessionRemoved)
  },
  beforeDestroy() {
    this._offSessionState?.()
    chatBus.$off('stream-started', this.onPeerStreamStarted)
    chatBus.$off('session-removed', this.onPeerSessionRemoved)
  },
  methods: {
    beforeSessionChange() {},
    getPageContextForSend() {
      return null
    },
    // 其他实例（主页面/侧边栏）在本实例正展示的会话里开始了流式：挂载实时流同步展示
    onPeerStreamStarted({ sessionId, origin }) {
      if (origin === this._uid || !sessionId) return
      if (this.loadedSessionId !== sessionId) return
      if (this.attachedStreamingSessions[sessionId] === 'displayed') return
      this.attachToStreamingSession(sessionId, { switchTo: false, displayed: true })
    },
    onPeerSessionRemoved({ sessionId, origin }) {
      if (origin === this._uid) return
      delete this.sessionStates[sessionId]
      if (this.currentSessionId === sessionId) this.fallbackAfterSessionRemoved()
    },
    async fallbackAfterSessionRemoved() {
      // 先清空归属（避免把已删除会话的界面数据再存回缓存），再决定回退到哪个会话
      this.currentSessionId = null
      this.loadedSessionId = null
      this.messages = []
      const nextId = this.sessions[0] ? this.sessions[0].session_id : null
      if (nextId) {
        // 复用切会话逻辑：加载其历史（含 sessionLoading 骨架屏），而不是留一个空欢迎页
        await this.handleSelectSession(nextId)
      } else {
        // 没有剩余会话：回到新建会话首页
        await this.handleCreateSession()
      }
    },
    async refreshSessionFiles(sessionId = null, delayMs = 0) {
      const targetId = sessionId || this.currentSessionId
      if (!targetId) {
        this.currentSessionHasFiles = false
        return
      }
      if (this.filesCheckTimer) {
        clearTimeout(this.filesCheckTimer)
        this.filesCheckTimer = null
      }
      const doCheck = async () => {
        try {
          const files = await getSessionGeneratedFiles(targetId)
          if (this.currentSessionId !== targetId) return
          this.currentSessionHasFiles = Array.isArray(files) && files.length > 0
          console.log('[Files] Session', targetId, 'has files:', this.currentSessionHasFiles, 'count:', files?.length)
        } catch (e) {
          console.error('[Files] 检查会话文件失败:', e)
          this.currentSessionHasFiles = false
        }
      }
      if (delayMs > 0) {
        this.filesCheckTimer = setTimeout(doCheck, delayMs)
      } else {
        await doCheck()
      }
    },
    markStreaming(sid) {
      if (!sid) return
      if (!this.streamingSessions.includes(sid)) {
        this.streamingSessions.push(sid)
      }
      this.lastStreamingSession = sid
    },
    unmarkStreaming(sid) {
      if (!sid) return
      const idx = this.streamingSessions.indexOf(sid)
      if (idx !== -1) this.streamingSessions.splice(idx, 1)
    },
    isSessionStreaming(sid) {
      return !!sid && this.streamingSessions.includes(sid)
    },
    // ---- 会话列表"待审批"标识 ----
    // 口径收敛到 derivePendingApproval：只看最后一条 assistant 消息的消息级载荷，
    // 不再用工具块的 approval_status 判（那是展示态，会留下"有徽标、没按钮"的死状态）。
    hasPendingApprovalInMessages(msgs) {
      return derivePendingApproval(msgs)
    },
    // 待审批产生/消失时增删列表徽标标记（store 共享，主面板与侧边栏同时可见）
    syncPendingApprovalFlag(sessionId, hasPending) {
      setPendingApprovalFlag(sessionId, hasPending)
    },
    // 以本地消息缓存（当前界面或会话级缓存）为准重算某会话的待审批状态。
    // 审批决策后、服务端历史回填后调用，保证徽标随状态流转及时消失。
    recomputePendingApprovalFlag(sessionId) {
      if (!sessionId) return
      const cur = this.currentSessionId
      const msgs = cur === sessionId
        ? this.messages
        : ((this.sessionStates[sessionId] || {}).messages)
      this.syncPendingApprovalFlag(sessionId, this.hasPendingApprovalInMessages(msgs))
    },
    // 挂载到进行中的流式任务：回放该流已产生的事件并持续接收后续事件。
    // displayed=true：重建界面消息并持续更新（当前查看的会话）；
    // displayed=false：仅后台同步 token 用量、流结束时移除"进行中"标记，不触碰界面。
    async attachToStreamingSession(sessionId, opts = {}) {
      const { switchTo = false, displayed = true } = opts
      if (!sessionId) return
      const mode = displayed ? 'displayed' : 'background'
      // 已展示挂载的会话无需重复挂载；后台挂载可被升级为展示挂载
      if (this.attachedStreamingSessions[sessionId] === 'displayed') return
      this.attachedStreamingSessions[sessionId] = mode

      if (switchTo) {
        this.currentSessionId = sessionId
      }
      // 展示挂载（含切换懒挂载）时，界面消息/用量归属于该会话；
      // 否则 loadedSessionId 停留在旧会话，缓存写入错位，来回切换会显示空会话主页。
      if (displayed) {
        this.loadedSessionId = sessionId
      }
      this.markStreaming(sessionId)

      if (displayed) {
        this.sessionLoading = true
        try {
          const history = await getChatHistory(sessionId)
          if (this.currentSessionId !== sessionId) {
            // 加载期间用户已切换到其他会话：放弃本次挂载，避免覆盖当前展示
            if (this.attachedStreamingSessions[sessionId] === mode) {
              delete this.attachedStreamingSessions[sessionId]
            }
            return
          }
          // 服务端历史可能还没落本轮提问（刷新落在准备期）：此时整表替换会把刚发的
          // 提问抹掉，界面只剩回答。本地最后一条是本轮提问、服务端最后一条不是，则
          // 以本地为准，随后占位照常由回放重建。
          const serverMsgs = history.messages || []
          const localMsgs = this.messages || []
          const localLast = localMsgs[localMsgs.length - 1]
          const serverLast = serverMsgs[serverMsgs.length - 1]
          const keepLocalTail = localLast && localLast.role === 'user'
            && (!serverLast || serverLast.role !== 'user')
          this.messages = keepLocalTail ? localMsgs : serverMsgs
          this.currentTodos = history.todos || []
          this.syncExpertChipFromMessages()
          if (history.usage) {
            this.sessionUsage.input_tokens = history.usage.input_tokens || 0
            this.sessionUsage.output_tokens = history.usage.output_tokens || 0
            this.sessionUsage.reasoning_tokens = history.usage.reasoning_tokens || 0
            this.sessionUsage.context_tokens = history.usage.context_tokens || 0
            this.sessionDuration = history.usage.elapsed_time || 0
            this.iterationCount = history.usage.step_count || 0
          }
          if (history.context_length) {
            this.sessionUsage.context_length = history.context_length
          }
        } catch (e) {
          console.error('加载聊天历史失败:', e)
        } finally {
          this.sessionLoading = false
        }
      }

      // 正在生成中的回复应是消息列表的最后一条：最后一条是 assistant 则原位替换为
      // 空占位由回放重建（避免重复）；最后一条仍是用户消息则末尾追加占位。
      // 此前按「倒数第一条 assistant」查找，会把上一轮历史 assistant 误替换，导致
      // 正文渲染在最后一次用户输入的上方。
      const attachId = `assistant-attach-${Date.now()}-${String(sessionId).slice(-5)}`
      if (displayed) {
        const lastMsg = this.messages[this.messages.length - 1]
        const lastAssistantIdx = lastMsg && lastMsg.role === 'assistant'
          ? this.messages.length - 1
          : -1
        const placeholder = {
          id: attachId,
          role: 'assistant',
          content: '',
          created_at: null,
          thinking: '',
          tool_calls: [],
          blocks: [],
          loading: true,
        }
        if (lastAssistantIdx === -1) {
          this.messages.push(placeholder)
        } else {
          setReactive(this.messages, lastAssistantIdx, placeholder)
        }
        this.streamingAssistantId = attachId
      }

      const { onChunk } = this.createStreamChunkHandler({
        isResume: true,
        attachMode: true,
        get assistantMsgId() { return attachId },
        set assistantMsgId(v) {},
        streamSessionId: sessionId,
        preStreamUsage: { ...this.sessionUsage },
        preStreamIterationCount: this.iterationCount,
        preStreamDuration: this.sessionDuration,
        initialBlockOrder: 0,
        onApprovalRequired: (data) => {
          // 会话列表立即亮起"待审批"标识：后台挂载（displayed=false）也必须亮，
          // 否则 HITL 中断停流期间没人点亮徽标，要等用户点进会话才补上
          this.syncPendingApprovalFlag(sessionId, true)
          if (!displayed) return
          this.pendingApproval = {
            threadId: data.thread_id,
            assistantMsgId: attachId,
          }
          if (attachId && data.action_requests) {
            const idx = this.messages.findIndex(m => m.id === attachId)
            if (idx !== -1) {
              this.messages[idx].pending_approval = { thread_id: data.thread_id, action_requests: data.action_requests }
              if (Array.isArray(data.blocks) && data.blocks.length > 0) {
                this.messages[idx].blocks = data.blocks.map(b => ({ ...b }))
              }
              // 挂不上工具卡片的（子代理内部审批）由 ChatMessage 的独立审批面板展示
              applyApprovalRequests(this.messages[idx].blocks, data.action_requests)
              setReactive(this.messages, idx, { ...this.messages[idx], blocks: [...this.messages[idx].blocks] })
            }
          }
        },
      })

      const controller = this.trackStream(new AbortController())
      if (displayed) this.currentAbortController = controller
      if (!displayed && !this.sessionStates[sessionId]) {
        // 后台挂载：确保会话缓存存在，供 token 用量回放写入
        this.sessionStates[sessionId] = {
          messages: [],
          isStreaming: true,
          sessionUsage: {
            input_tokens: 0,
            output_tokens: 0,
            context_length: this.sessionUsage.context_length,
            auto_compress_tokens: null,
            context_tokens: 0,
          },
          sessionDuration: 0,
          iterationCount: 0,
          abortController: null,
          todos: [],
        }
      }
      try {
        await attachStream(sessionId, onChunk, controller.signal)
      } catch (e) {
        if (e.name !== 'AbortError') {
          console.error('挂载流式输出失败:', e)
          if (displayed && this.currentSessionId === sessionId) this.error = e.message || '挂载流式输出失败'
        }
      } finally {
        this.untrackStream(controller)
        if (this.attachedStreamingSessions[sessionId] === mode) {
          delete this.attachedStreamingSessions[sessionId]
        }
        if (!this._chatDisposed && (!displayed || !this.pendingApprovalOf(sessionId))) {
          if (displayed && this.currentSessionId === sessionId) this.currentAbortController = null
          this.unmarkStreaming(sessionId)
          if (this.sessionStates[sessionId]) {
            this.sessionStates[sessionId].isStreaming = false
            this.sessionStates[sessionId].abortController = null
          }
          // 列表"进行中"标记此时移除（显示已完成）：以服务端历史为准回填完整内容
          await this.syncSessionFromServer(sessionId)
          // 流结束回填后重算"待审批"标识（审批已被处理/失效则熄灭，仍 pending 则保持）
          this.recomputePendingApprovalFlag(sessionId)
          if (displayed && this.currentSessionId === sessionId) {
            // 同上兜底：挂载流结束时确保占位消息不再标记 loading，避免页面残留"执行中"
            let idx = this.messages.findIndex(m => m.id === attachId)
            if (idx === -1) {
              for (let i = this.messages.length - 1; i >= 0; i--) {
                if (this.messages[i].role === 'assistant' && this.messages[i].loading) { idx = i; break }
              }
            }
            if (idx !== -1 && this.messages[idx].loading) {
              setReactive(this.messages, idx, { ...this.messages[idx], loading: false })
            }
            this.streamingAssistantId = null
            this.saveCurrentSessionState()
          }
          await this.loadSessions()
        }
      }
    },
    /**
     * 界面是不是仍停在「只有提问、还没答复」的状态。
     *
     * 带 fallback_note 的 assistant 不算：那是后端在「这一轮没跑完」时落的中性说明
     * （超时 / 上游断连等），有它就说明这一轮已经收尾，再盯下去只是空转。
     */
    awaitingAnswer() {
      const last = this.messages[this.messages.length - 1]
      if (!last) return false
      if (last.role === 'user') return true
      return last.role === 'assistant'
        && !last.content && !last.thinking
        && !last.pending_approval
        && !last.fallback_note
        && !(Array.isArray(last.blocks) && last.blocks.length)
    },
    /**
     * 刷新后兜底：这一轮若停在半途（末尾是用户提问、或只剩一个空 assistant 占位），
     * 就在后台一直盯着，直到界面上真的出现这一轮的答复为止。
     *
     * 2026-11-07 加强（第二次）：旧版按 1s→2s→4s→8s→15s→25s 退避 6 轮，并且
     * 「一挂上实时流就收手」。但挂上 ≠ 有内容：attach 成功而事件没渲染出来（或中间层
     * 把 SSE 掐了）时，页面就永久停在「只有提问、下面一片空白」，只能再刷新一次——
     * 实测这一轮 18:55:03 提问、18:55:16 答复就落库了，界面却一直空着。
     * 现在两条腿并行、谁先到算谁的，且不会因为「已挂载」而停止：
     *   1) 每轮先问服务端要历史：答复一落库立刻回填，完全不依赖 SSE；
     *   2) 顺手探一次 /stream/status，还在跑就挂一次实时流接回增量；
     *   3) 直到 awaitingAnswer() 为假为止，最长盯 5 分钟，单轮异常不影响下一轮。
     */
    async resumeInterruptedTurn() {
      const sid = this.loadedSessionId || this.currentSessionId
      if (!sid || this._unansweredWatchSid === sid) return
      if (!this.awaitingAnswer()) return
      // 页面自己就在跑这一轮（不是刷新回来的）：交给正常的流式收尾
      if (this.isSessionStreaming(sid) || this.pendingApprovalOf(sid)) return
      // 只盯「刚发出没多久」的提问：历史里早就中断的旧提问不该触发轮询
      const asked = this.messages[this.messages.length - 1]
      const askedAt = Date.parse((asked && (asked.timestamp || asked.created_at)) || '')
      if (!isNaN(askedAt) && Date.now() - askedAt > 10 * 60 * 1000) return
      this._unansweredWatchSid = sid
      const startedAt = Date.now()
      console.info(`[刷新续接] 会话 ${String(sid).slice(-5)} 停在未答复状态，开始后台兜底（最长 5 分钟）`)
      let attached = false
      let round = 0
      try {
        while (Date.now() - startedAt < 5 * 60 * 1000) {
          // 前 18 秒密一点（答复常在这段时间落库），之后放慢到 3s / 8s
          const wait = round < 12 ? 1500 : (round < 30 ? 3000 : 8000)
          await new Promise(resolve => setTimeout(resolve, wait))
          round += 1
          if (this._chatDisposed) return
          // 用户切走了会话 / 界面已经有答复了：收手
          if (this.currentSessionId !== sid || this.loadedSessionId !== sid) return
          if (!this.awaitingAnswer()) return
          if (this.pendingApprovalOf(sid)) return
          try {
            // 服务端确实产出了本轮答复才回填；没产出就保留本地提问，继续下一轮
            if (await this.syncIfServerAnswered(sid)) {
              // 回填不改变 loadedSessionId，审批面板与徽标平时靠 watcher 触发，这里自己补
              console.info('[刷新续接] 已从历史补回这一轮的答复')
              this.reconstructPendingApproval()
              this.recomputePendingApprovalFlag(sid)
              return
            }
          } catch (e) { /* 单轮失败不影响下一轮 */ }
          if (attached || this.currentSessionId !== sid) continue
          // 别的路径（tryAttachLiveStream）已经挂上了：不重复挂，避免同一会话两条 SSE
          if (this.isSessionStreaming(sid)) { attached = true; continue }
          let active = false
          try {
            const status = await getStreamStatus(sid)
            active = !!(status && status.active)
          } catch (e) { active = false }
          if (!active) continue
          // 这一轮还在跑：挂一次实时流接回增量（挂上不等于有内容，历史兜底继续跑）
          this.markStreaming(sid)
          this.attachToStreamingSession(sid, { switchTo: false, displayed: true })
          attached = true
        }
      } finally {
        if (this._unansweredWatchSid === sid) this._unansweredWatchSid = null
      }
    },
    // 保存当前会话状态到缓存（以界面数据实际所属的会话为 key，避免异步加载期间保存错位）
    saveCurrentSessionState() {
      if (!this.loadedSessionId) return
      this.sessionStates[this.loadedSessionId] = {
        messages: this.messages.slice(),
        isStreaming: this.isStreaming,
        sessionUsage: { ...this.sessionUsage },
        sessionDuration: this.sessionDuration,
        iterationCount: this.iterationCount,
        abortController: this.currentAbortController,
        streamingAssistantId: this.isSessionStreaming(this.loadedSessionId) ? this.streamingAssistantId : null,
        pendingApproval: this.pendingApproval,
        todos: [...this.currentTodos]
      }
    },
    // 从缓存恢复会话状态
    restoreSessionState(sessionId) {
      const state = this.sessionStates[sessionId]
      if (state) {
        this.messages = state.messages
        // 输入框是否显示停止按钮由 isStreaming（当前会话是否在流式会话集合中）决定
        this.sessionUsage = { ...state.sessionUsage }
        this.sessionDuration = state.sessionDuration || 0
        this.iterationCount = state.iterationCount || 0
        this.currentAbortController = state.abortController
        this.streamingAssistantId = this.isSessionStreaming(sessionId) ? (state.streamingAssistantId || null) : null
        this.pendingApproval = state.pendingApproval || null
        this.currentTodos = state.todos || []
        this.loadedSessionId = sessionId
        this.syncExpertChipFromMessages()
        return true
      }
      return false
    },
    /** 按最后一轮对话恢复输入框右下角的专家芯片：
        最后一条用户消息带 expert（{id,name}，随消息落库）则恢复召唤态，
        没有则清空。messages 每次整体刷新（缓存恢复/历史加载/服务端回填）后调用，
        保证芯片始终与「该会话最后一轮实际使用的专家」一致。 */
    syncExpertChipFromMessages() {
      const msgs = this.messages || []
      for (let i = msgs.length - 1; i >= 0; i--) {
        const msg = msgs[i]
        if (!msg || msg.role !== 'user') continue
        const ex = msg.expert
        this.selectedExpert = ex && ex.id
          ? { export_agent_id: ex.id, export_name_cn: ex.name || '' }
          : null
        return
      }
      // 空会话（含「选了专家但没发消息就切走」的新窗口）：不恢复任何专家
      this.selectedExpert = null
    },
    // ── 会话级流式隔离 ──────────────────────────────────────────────────
    // 流事件只写入「所属会话」的数据，避免多会话并行流式（A 流式中新建/切换到 B）串台。
    // 展示中的会话（sid === loadedSessionId）直接用组件数据；后台会话临时把组件数据
    // 指向其 sessionStates 缓冲，处理完立即还原（全程同步，不会触发中途渲染）。
    ensureSessionState(sid) {
      let st = this.sessionStates[sid]
      if (!st) {
        st = {
          messages: [],
          isStreaming: true,
          sessionUsage: { input_tokens: 0, output_tokens: 0, reasoning_tokens: 0, context_length: this.sessionUsage.context_length, auto_compress_tokens: null, context_tokens: 0 },
          sessionDuration: 0,
          iterationCount: 0,
          abortController: null,
          todos: [],
          streamingAssistantId: null,
          pendingApproval: null,
        }
      }
      this.$set(this.sessionStates, sid, st)
      return st
    },
    // 某会话的待审批（展示中读组件字段，后台读缓存）；流收尾按「流所属会话」判断，不能看当前展示的
    pendingApprovalOf(sid) {
      if (!sid || sid === this.loadedSessionId) return this.pendingApproval
      return (this.sessionStates[sid] && this.sessionStates[sid].pendingApproval) || null
    },
    clearPendingApprovalOf(sid) {
      if (!sid || sid === this.loadedSessionId) this.pendingApproval = null
      else if (this.sessionStates[sid]) this.sessionStates[sid].pendingApproval = null
    },
    runInSession(sid, ctx, fn) {
      if (ctx?._sessionValid && !ctx._sessionValid()) return
      if (!sid || sid === this.loadedSessionId) return fn() // 展示中：直接用组件数据
      const st = this.ensureSessionState(sid)
      const saved = SESSION_FIELDS.map(([vmKey, stKey]) => {
        const value = this[vmKey]
        this[vmKey] = st[stKey]
        return value
      })
      // 不用缓存覆盖 ctx.assistantMsgId：ctx 属于这条流，覆盖会让后台事件丢失（切回无实时过程）
      if (ctx) ctx.stateSwapped = true
      const restore = () => {
        SESSION_FIELDS.forEach(([vmKey, stKey], i) => {
          st[stKey] = this[vmKey]
          this[vmKey] = saved[i]
        })
        if (ctx) ctx.stateSwapped = false
        this.$set(this.sessionStates, sid, st)
      }
      let result
      try {
        result = fn()
      } catch (e) {
        restore()
        throw e
      }
      // fn 可能返回 Promise（流结束后的异步收尾）：等它 settle 后再还原
      if (result && typeof result.then === 'function') return result.finally(restore)
      restore()
      return result
    },
    // 以服务端历史为准回填某会话：流式结束（列表"进行中"标记移除）后调用。
    // 本地增量流状态在后台收尾/事件未命中时可能不完整，history 接口返回的是后端
    // 完整落库消息，用它回填可保证"列表已完成"时页面渲染完整内容（无需刷新）。
    async syncSessionFromServer(sessionId, preloaded, opts) {
      if (!sessionId || isSessionDeleted(sessionId)) return
      const valid = sessionGuard(sessionId)
      this._historyVersions ||= {}
      const version = this._historyVersions[sessionId] = (this._historyVersions[sessionId] || 0) + 1
      let history = preloaded
      if (!history) {
        try {
          history = await getChatHistory(sessionId)
        } catch (e) {
          console.warn('回填会话历史失败:', e)
          return
        }
      }
      if (!valid() || version !== this._historyVersions[sessionId]) return
      // force 只用于已确认服务端有本轮答复、但挂载后的界面仍为空的情况。
      if (!(opts && opts.force) && this.isSessionStreaming(sessionId)) return

      const usage = history.usage || null
      if (this.currentSessionId === sessionId && this.loadedSessionId === sessionId) {
        this.messages = history.messages || []
        this.currentTodos = history.todos || []
        this.syncExpertChipFromMessages()
        if (usage) {
          this.sessionUsage.input_tokens = usage.input_tokens || 0
          this.sessionUsage.output_tokens = usage.output_tokens || 0
          this.sessionUsage.reasoning_tokens = usage.reasoning_tokens || 0
          this.sessionUsage.context_tokens = usage.context_tokens || 0
          this.sessionDuration = usage.elapsed_time || 0
          this.iterationCount = usage.step_count || 0
        }
        if (history.context_length) this.sessionUsage.context_length = history.context_length
        this.scrollTrigger++
      } else {
        const st = this.ensureSessionState(sessionId)
        st.messages = history.messages || []
        st.todos = history.todos || []
        if (usage) {
          st.sessionUsage = {
            ...st.sessionUsage,
            input_tokens: usage.input_tokens || 0,
            output_tokens: usage.output_tokens || 0,
            reasoning_tokens: usage.reasoning_tokens || 0,
            context_tokens: usage.context_tokens || 0,
            context_length: history.context_length || st.sessionUsage.context_length,
          }
          st.sessionDuration = usage.elapsed_time || 0
          st.iterationCount = usage.step_count || 0
        }
        this.$set(this.sessionStates, sessionId, st)
      }
    },
    // 只有服务端已有本轮答复才回填，避免用旧答复覆盖刚发出、尚未落库的提问。
    async syncIfServerAnswered(sessionId) {
      if (!sessionId || isSessionDeleted(sessionId)) return false
      const valid = sessionGuard(sessionId)
      const version = this._historyVersions?.[sessionId] || 0
      let history
      try {
        history = await getChatHistory(sessionId)
      } catch (e) {
        return false
      }
      if (!valid() || version !== (this._historyVersions?.[sessionId] || 0)) return false
      // 流式正文已经渲染时不覆盖；挂载后仍为空时允许历史补回答复。
      if (this.isSessionStreaming(sessionId)
        && !(sessionId === this.loadedSessionId && this.awaitingAnswer())) return false
      const serverMsgs = (history && history.messages) || []
      const last = serverMsgs[serverMsgs.length - 1]
      if (!hasAssistantReply(last)) return false
      // 服务端这条答复比本地最后一次提问还早：说明本轮提问尚未落库，别覆盖
      const localMsgs = this.messages || []
      let localAskedAt = NaN
      for (let i = localMsgs.length - 1; i >= 0; i--) {
        if (localMsgs[i] && localMsgs[i].role === 'user') {
          localAskedAt = Date.parse(localMsgs[i].created_at || '')
          break
        }
      }
      const repliedAt = Date.parse(last.timestamp || last.created_at || '')
      if (!isNaN(localAskedAt) && !isNaN(repliedAt) && repliedAt < localAskedAt) return false
      // 已确认本轮答复存在，允许 force 越过流式保护补回空白界面。
      await this.syncSessionFromServer(sessionId, history, { force: true })
      // 只有界面真的不像「等答复」了才算成功，避免调用方误以为已经补上而提前收手
      return !(sessionId === this.loadedSessionId && this.awaitingAnswer())
    },
    // HITL 历史恢复：从消息中重建待审批状态，使切换/重载会话后仍能显示审批按钮并继续执行。
    // 历史消息无前端 id，按 thread_id（= `${session_id}-${message_id}`）还原后端 message_id
    // 作为消息 id，保证恢复执行时记录使用一致的 message_id。监听 loadedSessionId 变化即可
    // 覆盖所有历史加载路径（初始加载、切换会话、欢迎流程后加载、删除会话回退等）。
    reconstructPendingApproval() {
      // 流式中（含停在审批）不从历史重建，但换成该会话自己的审批，不能残留上一个展示会话的
      if (this.isStreaming) return void (this.pendingApproval = (this.sessionStates[this.loadedSessionId] || {}).pendingApproval || null)
      this.pendingApproval = null
      if (!this.messages || this.messages.length === 0) return
      const sid = this.currentSessionId || ''
      for (let i = this.messages.length - 1; i >= 0; i--) {
        const msg = this.messages[i]
        // 末尾出现用户消息说明已发起新一轮对话，旧的待审批不再阻塞，不重建
        if (msg.role === 'user') return
        if (msg.role === 'assistant' && msg.pending_approval && msg.pending_approval.thread_id) {
          const threadId = msg.pending_approval.thread_id
          const backendMsgId = sid && threadId.startsWith(sid + '-')
            ? threadId.slice(sid.length + 1)
            : (msg.id || `hist-${i}-${Date.now()}`)
          const blocks = (msg.blocks || []).map(b => {
            if (b.type === 'tool_call' && b.approval_status === 'pending') {
              return { ...b, pending_approval: true }
            }
            return b
          })
          // 判据与列表徽标同源：消息级载荷 + 还有可作答对象（卡片或消息级面板）
          const hasPending = derivePendingApproval([{ ...msg, blocks }])
          setReactive(this.messages, i, { ...msg, id: backendMsgId, blocks })
          if (hasPending) {
            this.pendingApproval = { threadId, assistantMsgId: backendMsgId }
          }
          break
        }
      }
      // 以重建结果为准刷新会话列表"待审批"标识（无待审批/已被新一轮对话打断时熄灭）
      this.recomputePendingApprovalFlag(this.currentSessionId)
    },
    async loadSessions() {
      try {
        await loadDailySessions(
          () => listSessions(chatStore.username || null, { archived: false }),
          rows => {
            this.sessions = rows
            this.hydratePendingApprovalFromServer()
            if (this.error === '加载会话列表失败') this.error = null
          },
        )
      } catch (e) {
        if (!isStaleSessionError(e)) this.error = '加载会话列表失败'
      }
    },
    // 服务端列表已带 pending_approval（后端按「最后一条消息」判定）：刷新页面后
    // 一次列表请求就能点亮全部待审批会话的徽标，不必逐个会话拉历史重建。
    // 流式中的会话以本地实时事件为准（列表可能是旧快照），不覆盖；界面仍显示审批
    // 面板的会话也不熄灭，避免出现"有按钮、没徽标"的错位。
    hydratePendingApprovalFromServer() {
      const list = this.sessions || []
      for (let i = 0; i < list.length; i++) {
        const s = list[i]
        if (!s || !s.session_id) continue
        if (this.isSessionStreaming(s.session_id)) continue
        if (s.pending_approval) this.syncPendingApprovalFlag(s.session_id, true)
        else if (!this.pendingApprovalOf(s.session_id)) this.syncPendingApprovalFlag(s.session_id, false)
      }
    },
    async ensureCurrentSession(initialTitle = '') {
      if (!this.currentSessionId) {
        const newSession = await createSession(initialTitle || '新会话', chatStore.username || null)
        this.currentSessionId = newSession.session_id
        // 预创建的会话就是展示中会话：不同步 loadedSessionId，流式事件会被 runInSession 当成后台会话只写缓存（一直"正在思考"）
        this.loadedSessionId = newSession.session_id
        const existingIndex = this.sessions.findIndex(s => s.session_id === newSession.session_id)
        if (existingIndex === -1) {
          const session = {
            session_id: newSession.session_id,
            title: newSession.title || initialTitle || '新会话',
            created_at: newSession.created_at || new Date().toISOString()
          }
          this.sessions = [session, ...this.sessions]
        }
      }
      return this.currentSessionId
    },
    async handleCreateSession() {
      this.saveCurrentSessionState()
      this.beforeSessionChange('create')
      this.currentSessionId = null
      this.loadedSessionId = null
      this.messages = []
      this.currentTodos = []
      // 注意：保留 context_length（全局上下文窗口，对所有会话通用），不重置为 null，
      // 否则 contextPercent 分母为 null 时会强制显示为 0%
      this.sessionUsage = { input_tokens: 0, output_tokens: 0, reasoning_tokens: 0, context_length: this.sessionUsage.context_length, auto_compress_tokens: null, context_tokens: 0 }
      this.sessionDuration = 0
      this.iterationCount = 0
      this.refreshSessionFiles(null)
    },
    async handleSelectSession(sessionId) {
      // 保存当前会话状态
      this.saveCurrentSessionState()
      this.beforeSessionChange('select')
      // 切换会话不沿用上一个会话的专家选择（选择是当前工作台的临时状态）。
      // 主窗口不使用本方法（App.vue 同名方法优先），此处置空对 AiSidebar
      // 这类没有专家芯片的宿主是无害的空操作。
      this.selectedExpert = null
      this.currentSessionId = sessionId
      // 切换后按当前会话是否正在流式输出决定输入框状态：
      // 历史会话通常不是当前流式会话，应显示发送按钮而非停止按钮
      // 输入框状态由 isStreaming（当前会话是否在流式会话集合中）自动决定

      // 切换到进行中的会话：若本页尚未挂载其流式任务，懒挂载以继续接收事件
      // （刷新后恢复出的多个进行中会话，除当前展示外都走这里）
      if (this.isSessionStreaming(sessionId) && this.attachedStreamingSessions[sessionId] !== 'displayed') {
        this.attachToStreamingSession(sessionId, { switchTo: false, displayed: true })
        this.scrollTrigger++
        await this.refreshSessionFiles(sessionId)
        return
      }

      // 尝试从缓存恢复会话状态
      if (this.restoreSessionState(sessionId)) {
        this.scrollTrigger++
        await this.refreshSessionFiles(sessionId)
        return
      }

      // 缓存中没有，从服务器加载历史
      // 保留 context_length（全局上下文窗口），仅清空用量计数；
      // 若服务器返回了 context_length 则以其为准（见下方恢复逻辑）
      // sessionLoading 门控：加载期间不渲染空欢迎页，避免"先闪空会话页再出历史"
      this.sessionLoading = true
      this.sessionUsage = { input_tokens: 0, output_tokens: 0, reasoning_tokens: 0, context_length: this.sessionUsage.context_length, auto_compress_tokens: null, context_tokens: 0 }
      this.sessionDuration = 0
      this.iterationCount = 0

      try {
        const history = await getChatHistory(sessionId)
        // 加载期间用户可能又切换到了其他会话：丢弃过期响应，
        // 避免覆盖当前显示的数据（token 用量、消息等）
        if (this.currentSessionId !== sessionId) return
        this.messages = history.messages || []
        this.currentTodos = history.todos || []
        this.syncExpertChipFromMessages()
        // 从服务器返回的 usage 数据恢复 token 用量、会话耗时和迭代次数
        if (history.usage) {
          this.sessionUsage.input_tokens = history.usage.input_tokens || 0
          this.sessionUsage.output_tokens = history.usage.output_tokens || 0
          this.sessionUsage.reasoning_tokens = history.usage.reasoning_tokens || 0
          this.sessionUsage.context_tokens = history.usage.context_tokens || 0
          this.sessionDuration = history.usage.elapsed_time || 0
          this.iterationCount = history.usage.step_count || 0
        }
        if (history.context_length) {
          this.sessionUsage.context_length = history.context_length
        }
        // 数据加载完成，界面数据现在归属于该会话
        this.loadedSessionId = sessionId
        this.scrollTrigger++

        await this.refreshSessionFiles(sessionId)
      } catch (e) {
        console.error('加载聊天历史失败:', e)
        if (this.currentSessionId !== sessionId) return
        this.error = '加载聊天历史失败'
        // 加载失败时界面数据处于不一致状态（消息还是旧会话的、用量已清零），
        // 置空 loadedSessionId 防止后续把错误数据写入缓存
        this.loadedSessionId = null
        this.refreshSessionFiles(sessionId)
      } finally {
        this.sessionLoading = false
      }
    },
    async handleDeleteSession(sessionId) {
      try {
        await deleteSession(sessionId)
        // 会话删除后其"待审批/进行中"标识同步移除
        this.syncPendingApprovalFlag(sessionId, false)
        this.unmarkStreaming(sessionId)
        this.sessions = this.sessions.filter(s => s.session_id !== sessionId)

        // 清除会话缓存
        delete this.sessionStates[sessionId]

        chatBus.$emit('session-removed', { sessionId, origin: this._uid })

        if (this.currentSessionId === sessionId) {
          await this.fallbackAfterSessionRemoved()
        } else if (this.loadedSessionId === sessionId) {
          this.loadedSessionId = null
        }
        // 删除进行中的会话：同步移除流式标记，避免列表残留"进行中"徽标
        this.unmarkStreaming(sessionId)
        delete this.attachedStreamingSessions[sessionId]
      } catch (e) {
        console.error('删除会话失败:', e)
        this.error = '删除会话失败'
      }
    },
    async handleRenameSession(sessionId, newTitle) {
      try {
        await renameSession(sessionId, newTitle)
        const idx = this.sessions.findIndex(s => s.session_id === sessionId)
        if (idx !== -1) {
          setReactive(this.sessions, idx, { ...this.sessions[idx], title: newTitle })
        }
      } catch (e) {
        console.error('重命名会话失败:', e)
        this.error = '重命名会话失败'
      }
    },
    async handleTogglePin(sessionId) {
      try {
        const result = await togglePinSession(sessionId)
        const idx = this.sessions.findIndex(s => s.session_id === sessionId)
        if (idx !== -1) {
          setReactive(this.sessions, idx, { ...this.sessions[idx], pinned: result.pinned })
        }
        // 重新排序：置顶在前
        this.sessions.sort((a, b) => (b.pinned || 0) - (a.pinned || 0) || new Date(b.updated_at) - new Date(a.updated_at))
      } catch (e) {
        console.error('置顶操作失败:', e)
        this.error = '置顶操作失败'
      }
    },
    // 实现见 features/chat/services/chunkHandler.js（2026-10-20 抽出，行为不变）
    createStreamChunkHandler(ctx) {
      return createChunkHandler(this, ctx)
    },
    async handleSendMessage(message, files = [], signal, enableDeepThink = true, enableWebSearch = false, skills = [], expertEditId = '', knowledgeBaseIds = []) {
      const userMsgId = `user-${Date.now()}`
      const preStreamUsage = { ...this.sessionUsage }
      // 记录本次请求开始前已累计的耗时和迭代次数，用于流式过程中实时累加
      const preStreamDuration = this.sessionDuration
      const preStreamIterationCount = this.iterationCount
      // 本次流所属的会话 ID（新会话在 start 事件后才有值）。
      // 用于用户中途切换会话时，把用量更新写入所属会话的缓存而非当前显示
      let streamSessionId = this.currentSessionId

      const contentWithFiles = message.trim() // 只去首尾空白：换行与缩进是用户原话，气泡按纯文本原样展示
      const pageContext = this.getPageContextForSend() || null

      const userMessage = {
        id: userMsgId,
        role: 'user',
        content: contentWithFiles,
        page_context: pageContext,
        files: files.map(f => ({
          filename: f.filename,
          size: f.size,
          type: f.file.type,
          file_path: f.file_path || null
        })),
        created_at: new Date().toISOString()
      }
      // 结构化技能目录名（与后端落库字段同形，见 App.vue 的同类实现）
      if (skills.length > 0) userMessage.skills = [...skills]

      this.messages.push(userMessage)

      // 立即创建 assistant 占位消息，显示等待动画
      const assistantPlaceholderId = `assistant-${Date.now()}`
      // 专家模式：随消息记住本次回答使用的专家（执行过程头部展示头像+名称）。
      // 仅存在于本次会话的内存消息中；历史会话从后端加载的消息不含该字段，
      // 展示时按无专家兜底（后端消息结构不含专家信息，持久化需另行加字段）。
      const usedExpert = (this.selectedExpert && this.selectedExpert.export_agent_id)
        ? { id: this.selectedExpert.export_agent_id, name: this.selectedExpert.export_name_cn || '专家' }
        : null
      const assistantPlaceholder = {
        id: assistantPlaceholderId,
        role: 'assistant',
        content: '',
        created_at: null,
        thinking: '',
        tool_calls: [],
        blocks: [],
        loading: true,
        expert: usedExpert
      }
      this.messages.push(assistantPlaceholder)
      this.streamingAssistantId = assistantPlaceholderId

      let assistantMsgId = null
      let assistantMessageCreated = false

      const streamCtx = {
        isResume: false,
        // 本轮使用的专家 ID：start 事件里据此写「专家 -> 会话」映射（换专家 = 换会话）
        expertId: (this.selectedExpert && this.selectedExpert.export_agent_id) || '',
        get assistantMsgId() { return assistantMsgId },
        set assistantMsgId(v) { assistantMsgId = v },
        get streamSessionId() { return streamSessionId },
        set streamSessionId(v) { streamSessionId = v },
        preStreamUsage,
        preStreamIterationCount,
        preStreamDuration,
        initialBlockOrder: 0,
        ensureMessage: () => {
          if (!assistantMessageCreated) {
            if (assistantPlaceholderId && this.messages.find(m => m.id === assistantPlaceholderId)) {
              assistantMsgId = assistantPlaceholderId
            } else {
              assistantMsgId = `assistant-${Date.now()}`
              this.messages.push({
                id: assistantMsgId, role: 'assistant', content: '',
                created_at: null, thinking: '', tool_calls: [], blocks: [], loading: true,
                expert: usedExpert,
              })
            }
            this.streamingAssistantId = assistantMsgId
            assistantMessageCreated = true
          }
        },
        onApprovalRequired: (data) => {
          this.pendingApproval = { threadId: data.thread_id, assistantMsgId }
          // 会话列表立即亮起"待审批"标识（streamCtx 见下方 createStreamChunkHandler(streamCtx)，
          // 回调执行时已初始化完毕；普通流可能属于后台会话，优先取其 streamSessionId）
          this.syncPendingApprovalFlag(streamCtx.streamSessionId || this.currentSessionId, true)
          if (assistantMsgId && data.action_requests) {
            const idx = this.messages.findIndex(m => m.id === assistantMsgId)
            if (idx !== -1) {
              this.messages[idx].pending_approval = { thread_id: data.thread_id, action_requests: data.action_requests }
              // 挂不上工具卡片的（子代理内部审批）由 ChatMessage 的独立审批面板展示
              applyApprovalRequests(this.messages[idx].blocks, data.action_requests)
              setReactive(this.messages, idx, { ...this.messages[idx], blocks: [...this.messages[idx].blocks] })
            }
          }
        },
      }
      const { onChunk } = this.createStreamChunkHandler(streamCtx)
      let streamController = null

      try {
        this.markStreaming(this.currentSessionId)
        const controller = this.trackStream(new AbortController())
        streamController = controller
        this.currentAbortController = controller
        const abortSignal = signal || controller.signal

        // 更新会话缓存状态
        if (this.currentSessionId) {
          this.sessionStates[this.currentSessionId] = {
            ...this.sessionStates[this.currentSessionId],
            isStreaming: true,
            abortController: controller
          }
        }

        // 专家对话与通用对话只有一个入口 /agent/chat/stream：选中专家时把
        // expert_agent_id 一并上行，由后端加载该专家提示词、补装依赖技能、
        // 做职责范围预检（命中即短路拒答，Agent 不创建）。
        await sendMessage(
          this.currentSessionId,
          message,
          onChunk,
          abortSignal,
          enableDeepThink,
          files,
          this.selectedModel,
          enableWebSearch,
          pageContext,
          skills,
          '',
          this.selectedExpert && this.selectedExpert.export_agent_id
            ? this.selectedExpert.export_agent_id
            : '',
          // 新对话首条消息携带的知识库范围：后端建会话后先落 scope 再检索
          knowledgeBaseIds && knowledgeBaseIds.length ? { knowledgeBaseIds } : null
        )

        await this.refreshSessionFiles(null, 500)
      } catch (e) {
        if (e.name === 'AbortError') {
          // Mark assistant message as complete (loading=false) so spinners stop
          this.runInSession(streamSessionId, streamCtx, () => {
            if (assistantMsgId) {
              const idx = this.messages.findIndex(m => m.id === assistantMsgId)
              if (idx !== -1) {
                this.messages[idx].loading = false
                // 兜底：content 为空时从已渲染的 content 块拼取（createStreamChunkHandler
                // 内部的 currentContent 局部变量在此处不可见，不能直接引用）
                if (!this.messages[idx].content && this.messages[idx].blocks && this.messages[idx].blocks.length) {
                  this.messages[idx].content = this.messages[idx].blocks
                    .filter(b => b.type === 'content')
                    .map(b => b.content || '')
                    .join('')
                }
                this.messages[idx].created_at = new Date().toISOString()
                setReactive(this.messages, idx, { ...this.messages[idx] })
              }
            }
          })
          return
        }
        console.error('发送消息失败:', e)
        this.runInSession(streamSessionId, streamCtx, () => {
          if (assistantMsgId) {
            const idx = this.messages.findIndex(m => m.id === assistantMsgId)
            if (idx !== -1) {
              this.messages[idx].loading = false
              this.messages[idx].error = e.message || '发送消息失败，请检查网络连接'
              setReactive(this.messages, idx, { ...this.messages[idx] })
            } else {
              // 消息不存在，添加一条错误消息
              this.messages.push({
                id: assistantMsgId,
                role: 'assistant',
                content: '',
                error: e.message || '发送消息失败，请检查网络连接',
                loading: false,
                created_at: new Date().toISOString(),
                blocks: []
              })
            }
          }
        })
        // 仅当前展示会话的失败才提示到界面；后台会话失败不打扰当前会话
        if (!streamSessionId || streamSessionId === this.loadedSessionId) {
          this.error = e.message || '发送消息失败'
        }
      } finally {
        this.untrackStream(streamController)
        // HITL: 若有审批待处理，保持 isStreaming=true（用户需先审批）；卸载后不收尾（见 streamLifecycle）
        if (!this._chatDisposed && !this.pendingApprovalOf(streamSessionId)) {
          const sid = streamSessionId
          const isDisplayed = !sid || sid === this.loadedSessionId
          // 兜底：流已结束（列表"进行中"标记即将移除），若该会话的消息仍标记 loading
          // 就强制收尾，避免"会话列表已显示完成、会话页仍显示执行中"。
          this.runInSession(sid, streamCtx, () => {
            let idx = assistantMsgId ? this.messages.findIndex(m => m.id === assistantMsgId) : -1
            if (idx === -1) {
              for (let i = this.messages.length - 1; i >= 0; i--) {
                if (this.messages[i].role === 'assistant' && this.messages[i].loading) { idx = i; break }
              }
            }
            if (idx !== -1 && this.messages[idx].loading) {
              setReactive(this.messages, idx, { ...this.messages[idx], loading: false })
            }
          })
          // 只有展示中的会话才清显示级状态；后台流结束不能动当前会话的停止按钮等
          if (isDisplayed) {
            this.currentAbortController = null
            this.streamingAssistantId = null
          }
          if (sid && this.sessionStates[sid]) {
            this.sessionStates[sid].isStreaming = false
            this.sessionStates[sid].abortController = null
            if (!isDisplayed) this.sessionStates[sid].streamingAssistantId = null
          }
          if (sid) delete this.attachedStreamingSessions[sid]
          this.unmarkStreaming(sid)
          // 列表"进行中"标记此时移除（显示已完成）：以服务端历史为准回填，保证完整渲染
          await this.syncSessionFromServer(sid)

          await this.loadSessions()
        }
      }
    },
    // HITL: 用户审批后恢复执行。target.interruptId = 用户点的那个中断（DA-02）：
    // 只回答它，其余中断保持挂起，恢复流结束时后端会再次下发 approval_required。
    async handleToolApproval(decision, target) {
      if (!this.pendingApproval) return

      const currentApproval = this.pendingApproval
      const { threadId, assistantMsgId } = currentApproval
      const sessionId = this.currentSessionId

      const msgIdx = assistantMsgId ? this.messages.findIndex(m => m.id === assistantMsgId) : -1
      const pending = msgIdx !== -1 ? this.messages[msgIdx].pending_approval : null
      const actionRequests = (pending && pending.action_requests) || []
      const { decisions, interruptId } = buildResumeDecisions(actionRequests, decision, {
        interruptId: target && target.interruptId,
        // 编辑参数：只改被点的那一条（action_index 由载荷带下来）
        targetActionIndex: target && target.actionIndex,
        editedAction: target && target.editedArgs
          ? { name: target.actionName || '', args: target.editedArgs }
          : null,
      })

      // 清掉待审批按钮（恢复流期间不允许再点），只给本次作答的中断记上「已批准 / 已拒绝」
      if (msgIdx !== -1) {
        markApprovalDecision(this.messages[msgIdx].blocks, decision, actionRequests, interruptId)
        setReactive(this.messages, msgIdx, {
          ...this.messages[msgIdx],
          pending_approval: null,
          blocks: [...this.messages[msgIdx].blocks],
        })
      }
      // 本次决策已落地，重算标识（若恢复流中再遇嵌套审批会立即重新亮起）
      this.recomputePendingApprovalFlag(sessionId)

      const { onChunk } = this.createStreamChunkHandler({
        isResume: true,
        assistantMsgId,
        streamSessionId: sessionId,
        ...resumeUsageBase(this.sessionDuration, this.iterationCount, this.messages[msgIdx]), // 扣掉本条中断前已计入的耗时/步数
        initialBlockOrder: (() => {
          const i = this.messages.findIndex(m => m.id === assistantMsgId)
          return i !== -1 ? this.messages[i].blocks.length : 0
        })(),
        onApprovalRequired: (data) => {
          // 嵌套审批
          this.pendingApproval = {
            threadId: data.thread_id,
            assistantMsgId,
          }
          // 决策后标识可能已被重算熄灭，嵌套审批到达时重新亮起
          this.syncPendingApprovalFlag(sessionId, true)
          if (assistantMsgId && data.action_requests) {
            const idx = this.messages.findIndex(m => m.id === assistantMsgId)
            if (idx !== -1) {
              this.messages[idx].pending_approval = { thread_id: data.thread_id, action_requests: data.action_requests }
              // 用后端权威 blocks 刷新（含本轮已完成工具的结果）
              if (Array.isArray(data.blocks) && data.blocks.length > 0) {
                this.messages[idx].blocks = data.blocks.map(b => ({ ...b }))
              }
              // 防御：嵌套审批时本轮思考已完成，把残留无 duration 的思考块置 0，
              // 避免 block.duration==null && loading=true 误显示"正在思考"
              for (const b of this.messages[idx].blocks) {
                if (b.type === 'thinking' && (b.duration == null)) {
                  b.duration = 0
                }
              }
              // 挂不上工具卡片的（子代理内部审批）由 ChatMessage 的独立审批面板展示
              applyApprovalRequests(this.messages[idx].blocks, data.action_requests)
              setReactive(this.messages, idx, { ...this.messages[idx], blocks: [...this.messages[idx].blocks] })
            }
          }
        },
      })

      let streamController = null
      try {
        const controller = this.trackStream(new AbortController())
        streamController = controller
        this.currentAbortController = controller
        this.markStreaming(sessionId)
        this.streamingAssistantId = assistantMsgId

        // 恢复期间保持 loading=true，使思考 spinner 与工具"执行中"状态与正常流一致
        const _resumeIdx = this.messages.findIndex(m => m.id === assistantMsgId)
        if (_resumeIdx !== -1) {
          setReactive(this.messages, _resumeIdx, { ...this.messages[_resumeIdx], loading: true })
        }
        // 注意：人工介入（批准/拒绝）不再作为用户侧消息展示，
        // 直接在模型侧的 execute 工具上进行了 HITL 标注。
        await resumeStream(sessionId, threadId, decisions, onChunk, controller.signal, assistantMsgId)

        await this.loadSessions()
      } catch (e) {
        if (e.name === 'AbortError') return
        console.error('恢复执行失败:', e)
        // 恢复期间用户可能已切到别的会话：错误写回本会话（展示中或其缓存）
        this.runInSession(sessionId, null, () => {
          const idx = this.messages.findIndex(m => m.id === assistantMsgId)
          if (idx === -1) return
          this.messages[idx].loading = false
          this.messages[idx].error = e.message || '恢复执行失败'
          if (e.status === 409) this.messages[idx].pending_approval = null
          setReactive(this.messages, idx, { ...this.messages[idx] })
        })
      } finally {
        this.untrackStream(streamController)
        // 如果 resumeStream 期间收到了新的 approval_required（onChunk 重新设置了
        // 本会话的 pendingApproval），则不清除，保持 isStreaming=true 等待用户审批。
        // 判「新」只能比 thread_id：恢复流重放同一条中断时会新建对象，按对象引用比较
        // 会误判成新审批，把下面的回填与徽标重算整段跳过（徽标要等整页刷新才消失）。
        const latestApproval = this.pendingApprovalOf(sessionId)
        const latestThreadId = latestApproval && latestApproval.threadId
        const hasNewApproval = !!latestThreadId && latestThreadId !== (currentApproval && currentApproval.threadId)
        if (!this._chatDisposed && !hasNewApproval) {
          // 只清本会话的审批；当前展示的若是另一个会话，它的审批与停止按钮保持不动
          this.clearPendingApprovalOf(sessionId)
          if (sessionId === this.loadedSessionId) {
            this.currentAbortController = null
            this.streamingAssistantId = null
          }
          const sid = sessionId
          if (sid && this.sessionStates[sid]) {
            this.sessionStates[sid].isStreaming = false
            this.sessionStates[sid].abortController = null
          }
          this.unmarkStreaming(sid)
          this.saveCurrentSessionState()
          // 审批恢复流结束：以服务端历史为准回填，保证完整渲染
          await this.syncSessionFromServer(sid)
          this.recomputePendingApprovalFlag(sid)
          await this.loadSessions()
        }
      }
    },
    // ChatInput 的联网开关变化上报：存一份镜像，供不经过输入框的发送路径（重试）复用。
    // 与 EasyAgentPage.vue 的同名实现保持一致（Vue 2 组件方法优先于 mixin，两处都要改）。
    handleWebSearchChange(enabled) {
      this.webSearchEnabled = Boolean(enabled)
    },
    handleRetry(content) {
      // 直接重新发送消息内容，不经过输入框
      console.log('[handleRetry] content type:', typeof content, 'value:', content)

      if (content && typeof content === 'string' && content.trim()) {
        // 检查是否正在流式输出
        if (this.isStreaming) {
          this.error = '请等待当前消息完成'
          return
        }

        // 确保有当前会话
        if (!this.currentSessionId) {
          this.error = '请先创建会话'
          return
        }

        // 重试不经过输入框，联网开关必须从镜像取：漏传会落回 enableWebSearch 默认值 false
        this.handleSendMessage(content.trim(), [], null, true, this.webSearchEnabled)
      }
    },
    handleStop() {
      // Mark current assistant message as no longer loading (stops tool spinning)
      if (this.streamingAssistantId) {
        const idx = this.messages.findIndex(m => m.id === this.streamingAssistantId)
        if (idx !== -1) {
          setReactive(this.messages, idx, { ...this.messages[idx], loading: false })
        }
      }
      this.streamingAssistantId = null

      // 停止按钮仅在当前会话正在流式时显示，取消的会话即当前会话
      const sid = this.currentSessionId

      // 中止正在进行的 fetch 请求（关闭 SSE 连接）
      if (this.currentAbortController) {
        this.currentAbortController.abort()
        this.currentAbortController = null
      }

      // 更新会话缓存状态
      if (sid && this.sessionStates[sid]) {
        this.sessionStates[sid].isStreaming = false
        this.sessionStates[sid].abortController = null
      }
      this.unmarkStreaming(sid)
      delete this.attachedStreamingSessions[sid]

      // 通知后端取消正在运行的流式任务（中断 astream 执行）并清除 Agent 缓存
      if (sid) {
        authFetch(`/agent/chat/cancel?session_id=${encodeURIComponent(sid)}`, {
          method: 'POST',
        }).catch(() => {})
      }
      this.error = '已停止生成'
      setTimeout(() => {
        this.error = null
      }, 2000)
    },
    async handleRemoveFile(message, messageIndex, file) {
      try {
        // 调用后端的删除文件接口
        await deleteFile(this.currentSessionId, file)

        // 更新前端的消息列表，移除已删除的文件
        if (this.messages[messageIndex]) {
          const updatedMessage = {
            ...this.messages[messageIndex],
            files: this.messages[messageIndex].files.filter(f => f.filename !== file.filename)
          }
          this.messages.splice(messageIndex, 1, updatedMessage)
        }

        // 显示删除成功的提示
        this.error = '文件删除成功'
        setTimeout(() => {
          this.error = null
        }, 2000)
      } catch (err) {
        console.error('文件删除失败:', err)
        this.error = '文件删除失败'
        setTimeout(() => {
          this.error = null
        }, 2000)
      }
    }
  },
}
