import { setReactive } from '../../../utils/reactiveSet.js'
import { createSession, listSessions, getChatHistory, deleteSession, sendMessage, resumeStream, renameSession, togglePinSession, attachStream } from '../../../api/chat.js'
import { deleteFile, getSessionGeneratedFiles } from '../../../api/files.js'
import { authFetch } from '../../../api/auth.js'
import { chatBus, chatStore } from '../store/chatStore.js'
import { createStreamChunkHandler as createChunkHandler } from '../services/chunkHandler.js'
import { applyApprovalRequests, buildResumeDecisions, markApprovalDecision, unmatchedApprovalRequests } from '../../../utils/hitlApproval.js'

// MCP 工具结果解析：模块级函数，供正常流(handleSendMessage)与 HITL 恢复流(handleToolApproval)共用。
// 注意：不要把它定义在某个函数内部，否则另一处引用会抛 ReferenceError，
// 导致 tool_result 事件处理中断、工具一直显示"等待运行结果"。
export function parseMCPResult(rawResult) {
  // MCP results come as a string representation of a Python list: "[{'type': 'text', 'text': '...'}]"
  // or JSON format: '[{"type": "text", "text": "..."}]'
  // Extract just the text content.
  if (!rawResult) return ''
  if (typeof rawResult !== 'string') return String(rawResult)

  // Try JSON format first (single quotes replaced with double)
  try {
    const jsonStr = rawResult.replace(/'/g, '"')
    const parsed = JSON.parse(jsonStr)
    if (Array.isArray(parsed)) {
      return parsed
        .filter(item => item.type === 'text')
        .map(item => item.text)
        .join('\n\n')
    }
  } catch (e) {
    // Not valid JSON, return as-is
  }
  return rawResult
}

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
    chatBus.$on('stream-started', this.onPeerStreamStarted)
    chatBus.$on('session-removed', this.onPeerSessionRemoved)
  },
  beforeDestroy() {
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
      if (!sessionId) return
      const list = this.pendingApprovalSessions
      const has = list.includes(sessionId)
      if (hasPending && !has) list.push(sessionId)
      else if (!hasPending && has) list.splice(list.indexOf(sessionId), 1)
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
      this.$set(this.sessionStates, sid, st)
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
        this.$set(this.sessionStates, sid, st)
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
        this.$set(this.sessionStates, sessionId, st)
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
    async handleSendMessage(message, files = [], signal, enableDeepThink = true, enableWebSearch = false, skills = []) {
      const userMsgId = `user-${Date.now()}`
      const preStreamUsage = { ...this.sessionUsage }
      // 记录本次请求开始前已累计的耗时和迭代次数，用于流式过程中实时累加
      const preStreamDuration = this.sessionDuration
      const preStreamIterationCount = this.iterationCount
      // 本次流所属的会话 ID（新会话在 start 事件后才有值）。
      // 用于用户中途切换会话时，把用量更新写入所属会话的缓存而非当前显示
      let streamSessionId = this.currentSessionId

      const contentWithFiles = message.trim().replace(/\s+/g, ' ')
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

      try {
        this.markStreaming(this.currentSessionId)
        const controller = new AbortController()
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
            : ''
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
        // HITL: 若有审批待处理，保持 isStreaming=true（用户需先审批）
        if (!this.pendingApproval) {
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

      try {
        const controller = new AbortController()
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
        const idx = this.messages.findIndex(m => m.id === assistantMsgId)
        if (idx !== -1) {
          this.messages[idx].loading = false
          this.messages[idx].error = e.message || '恢复执行失败'
          setReactive(this.messages, idx, { ...this.messages[idx] })
        }
      } finally {
        // 如果 resumeStream 期间收到了新的 approval_required（onChunk 重新设置了
        // pendingApproval），则不清除，保持 isStreaming=true 等待用户审批
        const hasNewApproval = this.pendingApproval && this.pendingApproval !== currentApproval
        if (!hasNewApproval) {
          this.pendingApproval = null
          this.currentAbortController = null
          this.streamingAssistantId = null
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

        // 直接调用发送
        this.handleSendMessage(content.trim())
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
