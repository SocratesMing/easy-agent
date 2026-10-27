import { setReactive } from '../../../utils/reactiveSet.js'
import { createSession, listSessions, getChatHistory, deleteSession, renameSession, togglePinSession, attachStream } from '../../../api/chat.js'
import { getSessionGeneratedFiles } from '../../../api/files.js'
import { chatBus, chatStore, syncPendingApprovalFlag as setPendingApprovalFlag } from '../store/chatStore.js'
import { applyApprovalRequests, unmatchedApprovalRequests } from '../../../utils/hitlApproval.js'

/**
 * 对话引擎：单个"展示中会话"的消息、流式事件处理、HITL 审批、会话 CRUD。
 *
 * 移植自 frontend-vue2 的同名 mixin（Vue 3 版只有 AI 侧边栏混入；主页面 App.vue 仍用
 * composables/useStreamChunks.js），会话列表与"进行中"集合通过 chatStore 与主页面共享。
 * 发送 / 审批 / 停止等流式操作在 chatSend.js，宿主需同时混入两者。宿主组件可提供以下钩子：
 * - beforeSessionChange(kind)：新建（'create'）/切换（'select'）会话前调用
 * - getPageContextForSend()：返回随消息发送给模型的页面上下文（字符串或 null）
 * - selectedModel：发送时使用的模型
 */
export default {
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
    // HITL 历史恢复：切换/重载会话后重建待审批状态
    loadedSessionId() {
      this.reconstructPendingApproval()
    },
  },
  created() {
    chatBus.on('stream-started', this.onPeerStreamStarted)
    chatBus.on('session-removed', this.onPeerSessionRemoved)
  },
  beforeUnmount() {
    chatBus.off('stream-started', this.onPeerStreamStarted)
    chatBus.off('session-removed', this.onPeerSessionRemoved)
  },
  methods: {
    beforeSessionChange() {},
    getPageContextForSend() {
      return null
    },
    // 其他实例（主页面/侧边栏）在本实例正展示的会话里开始了流式：挂载实时流同步展示
    onPeerStreamStarted({ sessionId, origin }) {
      if (origin === this.$.uid || !sessionId) return
      if (this.loadedSessionId !== sessionId) return
      if (this.attachedStreamingSessions[sessionId] === 'displayed') return
      this.attachToStreamingSession(sessionId, { switchTo: false, displayed: true })
    },
    onPeerSessionRemoved({ sessionId, origin }) {
      if (origin === this.$.uid) return
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
    // 判定口径与 ChatMessage 的 hasPendingApproval 一致：tool_call 块 pending，
    // 或消息级 pending_approval（thread_id）仍存在。历史接口可能给数组形态，一并兼容。
    hasPendingApprovalInMessages(msgs) {
      return (msgs || []).some(m =>
        (m.pending_approval && (m.pending_approval.thread_id || (Array.isArray(m.pending_approval) && m.pending_approval.length))) ||
        (Array.isArray(m.blocks) && m.blocks.some(b => b.type === 'tool_call' && b.approval_status === 'pending'))
      )
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
          this.messages = history.messages || []
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
          if (!displayed) return
          this.pendingApproval = {
            threadId: data.thread_id,
            assistantMsgId: attachId,
          }
          // 会话列表立即亮起"待审批"标识
          this.syncPendingApprovalFlag(sessionId, true)
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

      const controller = new AbortController()
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
          if (displayed) this.error = e.message || '挂载流式输出失败'
        }
      } finally {
        if (this.attachedStreamingSessions[sessionId] === mode) {
          delete this.attachedStreamingSessions[sessionId]
        }
        if (!displayed || !this.pendingApproval) {
          if (displayed) this.currentAbortController = null
          this.unmarkStreaming(sessionId)
          if (this.sessionStates[sessionId]) {
            this.sessionStates[sessionId].isStreaming = false
            this.sessionStates[sessionId].abortController = null
          }
          // 列表"进行中"标记此时移除（显示已完成）：以服务端历史为准回填完整内容
          await this.syncSessionFromServer(sessionId)
          // 流结束回填后重算"待审批"标识（审批已被处理/失效则熄灭，仍 pending 则保持）
          this.recomputePendingApprovalFlag(sessionId)
          if (displayed) {
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
    // 保存当前会话状态到缓存（以界面数据实际所属的会话为 key，避免异步加载期间保存错位）
    saveCurrentSessionState() {
      if (!this.loadedSessionId) return
      this.sessionStates[this.loadedSessionId] = {
        messages: JSON.parse(JSON.stringify(this.messages)),
        isStreaming: this.isStreaming,
        sessionUsage: { ...this.sessionUsage },
        sessionDuration: this.sessionDuration,
        iterationCount: this.iterationCount,
        abortController: this.currentAbortController,
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
          sessionUsage: {
            input_tokens: 0,
            output_tokens: 0,
            reasoning_tokens: 0,
            context_length: this.sessionUsage.context_length,
            auto_compress_tokens: null,
            context_tokens: 0,
          },
          sessionDuration: 0,
          iterationCount: 0,
          abortController: null,
          todos: [],
          streamingAssistantId: null,
          assistantMsgId: null,
        }
      }
      this.sessionStates[sid] = st
      return st
    },
    runInSession(sid, ctx, fn) {
      if (!sid || sid === this.loadedSessionId) return fn() // 展示中：直接用组件数据
      const st = this.ensureSessionState(sid)
      const saved = {
        messages: this.messages,
        usage: this.sessionUsage,
        duration: this.sessionDuration,
        iterations: this.iterationCount,
        todos: this.currentTodos,
        streamingAssistantId: this.streamingAssistantId,
        assistantMsgId: ctx ? ctx.assistantMsgId : undefined,
      }
      this.messages = st.messages
      this.sessionUsage = st.sessionUsage
      this.sessionDuration = st.sessionDuration
      this.iterationCount = st.iterationCount
      this.currentTodos = st.todos
      this.streamingAssistantId = st.streamingAssistantId
      if (ctx) ctx.assistantMsgId = st.assistantMsgId
      const restore = () => {
        st.messages = this.messages
        st.sessionUsage = this.sessionUsage
        st.sessionDuration = this.sessionDuration
        st.iterationCount = this.iterationCount
        st.todos = this.currentTodos
        st.streamingAssistantId = this.streamingAssistantId
        if (ctx) st.assistantMsgId = ctx.assistantMsgId
        this.sessionStates[sid] = st
        this.messages = saved.messages
        this.sessionUsage = saved.usage
        this.sessionDuration = saved.duration
        this.iterationCount = saved.iterations
        this.currentTodos = saved.todos
        this.streamingAssistantId = saved.streamingAssistantId
        if (ctx) ctx.assistantMsgId = saved.assistantMsgId
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
    async syncSessionFromServer(sessionId) {
      if (!sessionId) return
      if (!this.sessions.some(s => s.session_id === sessionId)) return // 会话已删除/切换用户，跳过
      let history
      try {
        history = await getChatHistory(sessionId)
      } catch (e) {
        console.warn('回填会话历史失败:', e)
        return
      }
      // 拉取期间该会话若又开始了新一轮流式，别用旧历史覆盖
      if (this.isSessionStreaming(sessionId)) return

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
        this.sessionStates[sessionId] = st
      }
    },
    // HITL 历史恢复：从消息中重建待审批状态，使切换/重载会话后仍能显示审批按钮并继续执行。
    // 历史消息无前端 id，按 thread_id（= `${session_id}-${message_id}`）还原后端 message_id
    // 作为消息 id，保证恢复执行时记录使用一致的 message_id。监听 loadedSessionId 变化即可
    // 覆盖所有历史加载路径（初始加载、切换会话、欢迎流程后加载、删除会话回退等）。
    reconstructPendingApproval() {
      // 流式进行中由 onApprovalRequired 管理审批状态，不重建以免覆盖
      if (this.isStreaming) return
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
          // 子代理内部的审批没有对应工具卡片，靠消息级独立面板展示，也算待审批
          const hasPending = blocks.some(b => b.type === 'tool_call' && b.approval_status === 'pending') ||
            unmatchedApprovalRequests({ ...msg, blocks }).length > 0
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
        const data = await listSessions(chatStore.username || null)
        this.sessions = Array.isArray(data) ? data : (data.sessions || [])
        if (this.error === '加载会话列表失败') this.error = null
      } catch (e) {
        console.error('加载会话列表失败:', e)
        // 401 由登录失效流程接管（SDK 自动重登后会再次加载），不提示
        if (e && e.response && e.response.status === 401) return
        this.error = '加载会话列表失败'
      }
    },
    async ensureCurrentSession(initialTitle = '') {
      if (!this.currentSessionId) {
        const newSession = await createSession(initialTitle || '新会话', chatStore.username || null)
        this.currentSessionId = newSession.session_id
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

        chatBus.emit('session-removed', { sessionId, origin: this.$.uid })

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
  },
}
