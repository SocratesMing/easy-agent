import Vue from 'vue'
import { createSession, listSessions, getChatHistory, deleteSession, sendMessage, resumeStream, renameSession, togglePinSession, attachStream } from '../api/chat.js'
import { deleteFile, getSessionGeneratedFiles } from '../api/files.js'
import { authFetch } from '../api/auth.js'
import { streamExportRun } from '../api/expert.js'
import { chatBus, chatStore } from '../store/chatStore.js'
import { parseLeadingSkillTokens } from '../utils/skillTokens.js'
import { ensureSkillDirectoryLoaded, skillDirectoryStore } from '../store/skillDirectoryStore.js'

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
function setReactive(target, key, value) {
  if (!target) return
  if (Array.isArray(target)) {
    if (key >= 0 && key < target.length) target.splice(key, 1, value)
  } else {
    Vue.set(target, key, value)
  }
}

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
      // 无会话时勾选的知识范围暂存（选知识库不再立即建会话）：
      // 等发消息/上传文件真正建立会话时（ensureCurrentSession）才落库
      pendingKnowledgeScope: null,
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
              for (const ar of data.action_requests) {
                let blk = this.messages[idx].blocks.find(
                  b => b.type === 'tool_call' && (b.tool_call_id === ar.tool_call_id || b.id === ar.tool_call_id)
                )
                if (!blk && ar.tool_name) {
                  for (let i = this.messages[idx].blocks.length - 1; i >= 0; i--) {
                    const b = this.messages[idx].blocks[i]
                    if (b.type === 'tool_call' && b.tool_name === ar.tool_name) { blk = b; break }
                  }
                }
                if (blk) {
                  blk.pending_approval = true
                  blk.approval_status = 'pending'
                  if (ar.tool_call_id) {
                    blk.tool_call_id = ar.tool_call_id
                    blk.id = ar.tool_call_id
                  }
                  if (ar.file_paths && ar.file_paths.length > 0) {
                    blk.file_paths = ar.file_paths
                  }
                }
              }
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
        return true
      }
      return false
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
          const hasPending = blocks.some(b => b.type === 'tool_call' && b.approval_status === 'pending')
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
        // 新建会话时把无会话期间暂存的知识范围落库（先于首条消息发送，后端才能读到）。
        // $nextTick 等 sessionId prop 传导到输入框的知识库选择器后再落库
        if (this.pendingKnowledgeScope && this.pendingKnowledgeScope.length) {
          const pendingScope = this.pendingKnowledgeScope
          this.pendingKnowledgeScope = null
          await this.$nextTick()
          if (this.$refs.chatRef) await this.$refs.chatRef.flushKnowledgeScopeSave(pendingScope)
        }
      }
      return this.currentSessionId
    },
    // 知识范围变化：无会话时仅暂存（发消息建会话时落库）；有会话时选择器已直接落库
    handleKnowledgeScopeChange(baseIds) {
      this.pendingKnowledgeScope = this.currentSessionId ? null : [...(baseIds || [])]
    },
    async handleCreateSession() {
      this.saveCurrentSessionState()
      this.beforeSessionChange('create')
      // 回到新对话页：无会话期间暂存的知识范围作废（重新勾选才有）
      this.pendingKnowledgeScope = null
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
      // 切换会话：无会话期间暂存的知识范围作废，目标会话以自己的服务端范围为准
      this.pendingKnowledgeScope = null
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
    createStreamChunkHandler(ctx) {
      let currentBlock = null
      let currentThinking = ''
      let currentContent = ''
      let currentToolCalls = []
      let blockOrderCounter = ctx.initialBlockOrder || 0

      const findIdx = () => {
        return this.messages.findIndex(m => m.id === ctx.assistantMsgId)
      }
      const touchBlocks = () => {
        const idx = findIdx()
        if (idx !== -1) setReactive(this.messages, idx, { ...this.messages[idx], blocks: [...this.messages[idx].blocks] })
      }
      const ensureMessage = () => {
        if (ctx.ensureMessage) ctx.ensureMessage()
      }
      const addBlock = (type, data, replace = false) => {
        ensureMessage()
        const idx = findIdx()
        if (idx === -1) return null
        if (type === 'thinking' && data.step !== undefined) {
          const existing = this.messages[idx].blocks.find(b => b.type === 'thinking' && b.step === data.step)
          if (existing) { currentBlock = existing; return currentBlock }
        }
        blockOrderCounter++
        // 严格按 order 展示时，reopen 场景（late reasoning 晚于同 step 工具块到达）会让
        // 思考块的 order 大于工具块而排到工具之后。此时给思考块一个更小的 order
        //（同 step 最小 order - 0.5），使其排在工具之前，符合「先思考后工具」。
        let blkOrder = blockOrderCounter
        if (type === 'thinking' && data.step !== undefined) {
          const sameStep = this.messages[idx].blocks.filter(b => b.step === data.step && b.type !== 'thinking')
          if (sameStep.length > 0) {
            blkOrder = Math.min(...sameStep.map(b => b.order || 0)) - 0.5
          }
        }
        const needNewBlock = !currentBlock || currentBlock.type !== type ||
          (type === 'thinking' && data.step !== undefined && currentBlock.step !== data.step)
        if (needNewBlock) {
          currentBlock = { type, content: '', order: blkOrder, ...data }
          this.messages[idx].blocks.push(currentBlock)
          setReactive(this.messages, idx, { ...this.messages[idx], blocks: [...this.messages[idx].blocks] })
        } else {
          if (replace) {
            currentBlock.content = data.content || ''
          } else if (data.content !== undefined) {
            currentBlock.content = (currentBlock.content || '') + (data.content || '')
          }
          if (data.tool_name) currentBlock.tool_name = data.tool_name
          if (data.arguments !== undefined) currentBlock.arguments = data.arguments
          if (data.result !== undefined) currentBlock.result = data.result
          if (data.success !== undefined) currentBlock.success = data.success
          if (data.duration !== undefined) currentBlock.duration = data.duration
          if (data.step !== undefined) currentBlock.step = data.step
          if (data.id !== undefined) currentBlock.id = data.id
          setReactive(this.messages, idx, { ...this.messages[idx] })
        }
        return currentBlock
      }
      const updateThinkingDuration = (duration, step) => {
        const idx = findIdx()
        if (idx !== -1) {
          const blockIdx = this.messages[idx].blocks.findIndex(b => b.type === 'thinking' && b.step === step)
          if (blockIdx !== -1) {
            setReactive(this.messages[idx].blocks, blockIdx, { ...this.messages[idx].blocks[blockIdx], duration })
          }
          setReactive(this.messages, idx, { ...this.messages[idx], thinking_duration: duration, blocks: [...this.messages[idx].blocks] })
        }
      }
      const usagePatchFrom = (data) => {
        const patch = {
          input_tokens: data.input_tokens || 0,
          output_tokens: data.output_tokens || 0,
          reasoning_tokens: data.reasoning_tokens || 0,
          context_tokens: data.context_tokens || this.sessionUsage.context_tokens || 0,
        }
        if (data.context_length) patch.context_length = data.context_length
        if (data.auto_compress_tokens) patch.auto_compress_tokens = data.auto_compress_tokens
        return patch
      }
      const applyUsage = (data, sid) => {
        const patch = usagePatchFrom(data)
        const match = !sid || this.currentSessionId === sid
        if (ctx.attachMode) {
          // 重挂载回放：token_usage/done 携带的 elapsed_time 是原始流从头开始的
          // 累计值，应 SET 而非累加（isResume 分支的累加语义只适用于 HITL 续跑）
          if (match) {
            Object.assign(this.sessionUsage, patch)
            if (typeof data.elapsed_time === 'number') this.sessionDuration = Math.round(data.elapsed_time * 10) / 10
            if (typeof data.step_count === 'number') this.iterationCount = data.step_count
          } else {
            const cached = this.sessionStates[sid]
            if (cached) {
              cached.sessionUsage = { ...cached.sessionUsage, ...patch }
              if (typeof data.elapsed_time === 'number') cached.sessionDuration = Math.round(data.elapsed_time * 10) / 10
              if (typeof data.step_count === 'number') cached.iterationCount = data.step_count
            }
          }
          return
        }
        if (ctx.isResume) {
          if (match) {
            Object.assign(this.sessionUsage, patch)
            if (typeof data.elapsed_time === 'number') this.sessionDuration = Math.round((this.sessionDuration + data.elapsed_time) * 10) / 10
            if (typeof data.step_count === 'number') this.iterationCount = data.step_count
          } else {
            const cached = this.sessionStates[sid]
            if (cached) {
              cached.sessionUsage = { ...cached.sessionUsage, ...patch }
              if (typeof data.elapsed_time === 'number') cached.sessionDuration = Math.round(((cached.sessionDuration || 0) + data.elapsed_time) * 10) / 10
              if (typeof data.step_count === 'number') cached.iterationCount = data.step_count
            }
          }
        } else {
          const newDuration = typeof data.elapsed_time === 'number' ? Math.round((ctx.preStreamDuration + data.elapsed_time) * 10) / 10 : null
          const newIterations = typeof data.step_count === 'number' ? ctx.preStreamIterationCount + data.step_count : null
          if (match) {
            Object.assign(this.sessionUsage, patch)
            if (newDuration !== null) this.sessionDuration = newDuration
            if (newIterations !== null) this.iterationCount = newIterations
          } else {
            const cached = this.sessionStates[sid]
            if (cached) {
              cached.sessionUsage = { ...cached.sessionUsage, ...patch }
              if (newDuration !== null) cached.sessionDuration = newDuration
              if (newIterations !== null) cached.iterationCount = newIterations
            }
          }
        }
      }

      const onChunk = (data) => {
        const { type: eventType, content, duration, step, tool_name: toolName, tool_call_id: toolCallId, arguments: args, result, success, title } = data

        if (eventType === 'start') {
          if (!ctx.isResume) {
            this.currentTodos = []
            // 仅「本次请求就是当前展示会话的新建」时才认领 session_id；
            // 后台会话（ctx.streamSessionId 已确定）的 start 不得把界面抢过来。
            if (data.session_id && !this.currentSessionId && !ctx.streamSessionId) {
              this.currentSessionId = data.session_id
              this.loadedSessionId = data.session_id
              this.loadSessions()
            }
            if (data.session_id) ctx.streamSessionId = data.session_id
            // 标记该会话已有本页流处理器：切回时按缓存直接显示，避免再起 attach 重复渲染
            if (data.session_id && !ctx.attachMode) {
              this.$set(this.attachedStreamingSessions, data.session_id, 'displayed')
            }
            this.markStreaming(ctx.streamSessionId || this.currentSessionId)
            if (data.session_id) chatBus.$emit('stream-started', { sessionId: data.session_id, origin: this._uid })
          }
        } else if (eventType === 'token_usage') {
          applyUsage(data, ctx.streamSessionId)
        } else if (eventType === 'thinking_start') {
          // 始终按 step 解析思考块，不再用「currentBlock 已是 thinking 则跳过」守卫：
          // 该守卫会在上一 step 的 currentBlock 未被 thinking_end 及时置空时跳过新 step
          // 的 thinking_start，导致后续 thinking 增量回退到上一步卡片，造成同一 step 的
          // 思考内容被拆分/串步渲染。按 step 查找：存在则复用（同 turn 思考分段重开），
          // 不存在则新建，保证每个 step 恰好一张思考卡片。
          // 第一步思考事件可能早于 content/tool_call 到达（此时 assistant 消息尚未创建），
          // 必须先 ensureMessage，否则 findIdx 返回 -1 导致首步思考被丢弃（历史能查到、实时不渲染）。
          ensureMessage()
          const idx = findIdx()
          if (idx !== -1) {
            const targetStep = step || 0
            const existing = this.messages[idx].blocks.find(b => b.type === 'thinking' && b.step === targetStep)
            if (existing) {
              // 复用同 step 思考块：恢复已累积内容继续追加，不重置 duration 以避免卡片在
              //「思考过程」与「正在思考」间闪烁、也避免同一步骤思考被视觉上分成两段。
              currentThinking = existing.content || ''
              currentBlock = existing
              touchBlocks()
            } else {
              currentThinking = ''
              currentBlock = null
              addBlock('thinking', { content: '', step: targetStep })
            }
          }
        } else if (eventType === 'thinking') {
          const targetStep = step || 0
          // 优先用后端 full_content（本 step 思考完整内容）采用 SET 语义覆盖——幂等，
          // 重复/聚合增量也不会重复渲染；旧后端无 full_content 时回退到 append 增量。
          if (data.full_content) {
            currentThinking = data.full_content
          } else {
            currentThinking += content || ''
          }
          ensureMessage()
          const idx = findIdx()
          if (idx !== -1) {
            // 按 step 定位思考块写入；若该 step 尚无思考块（thinking_start 被跳过或事件
            // 乱序），按 step 新建一块，绝不回退到其他 step 的 currentBlock——否则会把
            // 本步思考写入上一步卡片（覆盖其内容），造成一个 step 的思考被拆分/错位。
            let blk = this.messages[idx].blocks.find(b => b.type === 'thinking' && b.step === targetStep)
            if (!blk) {
              blk = addBlock('thinking', { content: '', step: targetStep })
              if (!data.full_content) currentThinking = content || ''
            }
            if (blk) {
              blk.content = currentThinking
              currentBlock = blk
              setReactive(this.messages, idx, { ...this.messages[idx] })
            }
          }
        } else if (eventType === 'thinking_end') {
          touchBlocks()
          if (ctx.isResume) {
            // 健壮地为对应思考块设置 duration。HITL 恢复流中，思考之后往往紧跟
            // tool_call/tool_result 事件，currentBlock 已被改写为工具块或 null，
            // 仅依赖 currentBlock 会导致思考块 duration 一直为 null（前端误显示
            // "正在思考…"）。因此优先按 step 匹配，再回退到 currentBlock 与最后
            // 一个无 duration 的思考块。
            const idx = findIdx()
            if (idx !== -1) {
              let blockIdx = this.messages[idx].blocks.findIndex(b => b.type === 'thinking' && b.step === (step || 0))
              if (blockIdx === -1 && currentBlock && currentBlock.type === 'thinking') {
                blockIdx = this.messages[idx].blocks.indexOf(currentBlock)
              }
              if (blockIdx === -1) {
                for (let i = this.messages[idx].blocks.length - 1; i >= 0; i--) {
                  if (this.messages[idx].blocks[i].type === 'thinking') { blockIdx = i; break }
                }
              }
              if (blockIdx !== -1) {
                setReactive(this.messages[idx].blocks, blockIdx, { ...this.messages[idx].blocks[blockIdx], duration: duration || 0 })
                setReactive(this.messages, idx, { ...this.messages[idx], blocks: [...this.messages[idx].blocks] })
              }
            }
          } else {
            updateThinkingDuration(duration || 0, step || 0)
          }
          currentThinking = ''
          currentBlock = null
        } else if (eventType === 'content_start') {
          if (!currentBlock || currentBlock.type !== 'content') {
            currentContent = ''
            currentBlock = null
            addBlock('content', { content: '', step: step || 0 })
          }
        } else if (eventType === 'content') {
          const targetStep = step || 0
          // 正文块必须带 step，否则按 step 排序时会落到顶部（step=0）。同一 step 的
          // 正文分段到达（reopen）时复用已有正文块，避免同 step 产生多个正文块。
          if (!currentBlock || currentBlock.type !== 'content' || currentBlock.step !== targetStep) {
            const idx0 = findIdx()
            let existing = null
            if (idx0 !== -1) existing = this.messages[idx0].blocks.find(b => b.type === 'content' && b.step === targetStep)
            if (existing) {
              currentContent = (existing.content || '') + (content || '')
              currentBlock = existing
            } else {
              currentContent = content || ''
              currentBlock = null
              addBlock('content', { content: currentContent, step: targetStep })
            }
          } else {
            currentContent += content || ''
          }
          if (currentBlock && currentBlock.type === 'content') currentBlock.content = currentContent
          const idx = findIdx()
          if (idx !== -1) {
            if (ctx.isResume) this.messages[idx].content = (this.messages[idx].content || '') + (content || '')
            touchBlocks()
          }
        } else if (eventType === 'content_end') {
          currentContent = ''
          currentBlock = null
        } else if (eventType === 'todo_list') {
          if (data.todos && Array.isArray(data.todos)) this.currentTodos = data.todos
        } else if (eventType === 'assistant_start') {
          if (!ctx.isResume) {
            ensureMessage()
            currentContent = ''
            currentThinking = ''
            currentToolCalls = []
            currentBlock = null
          }
        } else if (eventType === 'user_input_required') {
          if (!ctx.isResume) {
            const idx = findIdx()
            if (idx !== -1) { this.messages[idx].loading = false; setReactive(this.messages, idx, { ...this.messages[idx] }) }
          }
        } else if (eventType === 'tool_call') {
          if (!ctx.isResume) ensureMessage()
          const idx = findIdx()
          if (idx !== -1) {
            const callId = toolCallId || `tool-${toolName}`
            const existingBlockIdx = this.messages[idx].blocks.findIndex(b => b.type === 'tool_call' && (b.id === callId || b.tool_call_id === callId))
            if (existingBlockIdx !== -1) {
              this.messages[idx].blocks[existingBlockIdx].arguments = args || {}
              currentBlock = this.messages[idx].blocks[existingBlockIdx]
              touchBlocks()
            } else {
              currentBlock = null
              if (!ctx.isResume) currentToolCalls.push({ tool_call_id: callId, tool_name: toolName || '', arguments: args || {}, result: '', success: true })
              addBlock('tool_call', { id: callId, tool_name: toolName || '', arguments: args || {}, result: '', success: true, step: step || 0 })
            }
          }
        } else if (eventType === 'tool_result') {
          const callId = toolCallId || `tool-${toolName}`
          const toolDuration = duration != null ? duration : 0
          if (!ctx.isResume && currentToolCalls.length > 0) {
            const matchingCall = currentToolCalls.find(tc => tc.tool_call_id === callId)
            if (matchingCall) {
              matchingCall.result = result || ''
              matchingCall.success = success !== false
              matchingCall.duration = toolDuration
              if (args) matchingCall.arguments = args
            }
          }
          const idx = findIdx()
          if (idx !== -1) {
            const blockIdx = this.messages[idx].blocks.findIndex(b => b.type === 'tool_call' && (b.id === callId || b.tool_call_id === callId))
            if (blockIdx !== -1) {
              const blk = { ...this.messages[idx].blocks[blockIdx], arguments: args || this.messages[idx].blocks[blockIdx].arguments, result: parseMCPResult(result || ''), success: success !== false, duration: toolDuration }
              if (ctx.isResume) blk.loading = false
              setReactive(this.messages[idx].blocks, blockIdx, blk)
              touchBlocks()
            }
          }
          if (!currentBlock || currentBlock.type !== 'content') currentBlock = null
        } else if (eventType === 'approval_required') {
          if (ctx.onApprovalRequired) ctx.onApprovalRequired(data)
        } else if (eventType === 'done') {
          const idx = findIdx()
          if (idx !== -1) {
            this.messages[idx].loading = false
            this.messages[idx].pending_approval = null
            this.pendingApproval = null
            this.messages[idx].created_at = new Date().toISOString()
            if (ctx.isResume) {
              if (Array.isArray(data.blocks) && data.blocks.length > 0) this.messages[idx].blocks = data.blocks.map(b => ({ ...b }))
              for (const b of this.messages[idx].blocks) { if (b.type === 'thinking' && b.duration == null) b.duration = 0 }
              if (data.usage) applyUsage(data.usage, ctx.streamSessionId)
            } else {
              const finalContent = data.content || currentContent
              this.messages[idx].content = finalContent
              // SET 语义下 currentThinking 只剩最后一个 step 的内容；此处从所有思考块
              // 拼接出完整 thinking，保证 message.thinking 字段（旧数据回退/兼容）正确。
              this.messages[idx].thinking = (this.messages[idx].blocks || [])
                .filter(b => b.type === 'thinking')
                .map(b => b.content || '')
                .join('')
              this.messages[idx].tool_calls = currentToolCalls
              if (data.usage) {
                this.messages[idx].usage = { input_tokens: data.usage.input_tokens || 0, output_tokens: data.usage.output_tokens || 0, reasoning_tokens: data.usage.reasoning_tokens || 0 }
              }
              if (data.usage) {
                const patch = usagePatchFrom(data.usage)
                const match = !ctx.streamSessionId || this.currentSessionId === ctx.streamSessionId
                const finalDuration = typeof data.elapsed_time === 'number' ? Math.round((ctx.preStreamDuration + data.elapsed_time) * 10) / 10 : null
                const finalIterations = data.usage && typeof data.usage.step_count === 'number' ? ctx.preStreamIterationCount + data.usage.step_count : null
                if (match) {
                  Object.assign(this.sessionUsage, patch)
                  if (finalDuration !== null) this.sessionDuration = finalDuration
                  if (finalIterations !== null) this.iterationCount = finalIterations
                } else {
                  const cached = this.sessionStates[ctx.streamSessionId]
                  if (cached) {
                    cached.sessionUsage = { ...cached.sessionUsage, ...patch }
                    if (finalDuration !== null) cached.sessionDuration = finalDuration
                    if (finalIterations !== null) cached.iterationCount = finalIterations
                  }
                }
              }
            }
            setReactive(this.messages, idx, { ...this.messages[idx] })
          }
          if (!ctx.isResume && title) {
            const existingIdx = this.sessions.findIndex(s => s.session_id === this.currentSessionId)
            if (existingIdx === -1) {
              this.sessions = [{ session_id: this.currentSessionId, title, created_at: new Date().toISOString(), message_count: this.messages.length }, ...this.sessions]
            } else {
              setReactive(this.sessions, existingIdx, { ...this.sessions[existingIdx], title })
              this.sessions = [...this.sessions]
            }
          }
        } else if (eventType === 'error') {
          const idx = findIdx()
          if (idx !== -1) {
            this.messages[idx].loading = false
            this.messages[idx].error = data.content || '处理失败'
            if (!ctx.isResume) {
              for (const blk of this.messages[idx].blocks) {
                if (blk.type === 'thinking' && blk.duration == null) blk.duration = 0
                if (blk.type === 'tool_call' && blk.duration == null) { blk.duration = 0; blk.success = false; if (!blk.result) blk.result = data.content || '执行中断' }
              }
            }
            setReactive(this.messages, idx, { ...this.messages[idx], blocks: [...this.messages[idx].blocks] })
          }
          this.error = data.content || '处理失败'
        }
      }

      // 事件按「所属会话」路由：后台会话写入其自身缓冲，不污染当前展示
      return { onChunk: (data) => this.runInSession(ctx.streamSessionId, ctx, () => onChunk(data)) }
    },
    async handleSendMessage(message, files = [], signal, enableDeepThink = true, enableWebSearch = false, skills = []) {
      // 无会话且已暂存知识范围：先建会话并落库知识范围（ensureCurrentSession 内完成）再发送，
      // 确保后端处理首条消息时能从会话上读到知识范围
      if (!this.currentSessionId && this.pendingKnowledgeScope && this.pendingKnowledgeScope.length) {
        await this.ensureCurrentSession()
      }
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
              for (const ar of data.action_requests) {
                let blk = this.messages[idx].blocks.find(
                  b => b.type === 'tool_call' && (b.tool_call_id === ar.tool_call_id || b.id === ar.tool_call_id)
                )
                if (!blk && ar.tool_name) {
                  for (let i = this.messages[idx].blocks.length - 1; i >= 0; i--) {
                    const b = this.messages[idx].blocks[i]
                    if (b.type === 'tool_call' && b.tool_name === ar.tool_name) { blk = b; break }
                  }
                }
                if (!blk) {
                  for (let i = this.messages[idx].blocks.length - 1; i >= 0; i--) {
                    const b = this.messages[idx].blocks[i]
                    if (b.type === 'tool_call' && !b.result && b.duration == null) { blk = b; break }
                  }
                }
                if (blk) {
                  blk.pending_approval = true
                  blk.approval_status = 'pending'
                  if (ar.tool_call_id) { blk.tool_call_id = ar.tool_call_id; blk.id = ar.tool_call_id }
                  if (ar.file_paths && ar.file_paths.length > 0) blk.file_paths = ar.file_paths
                }
              }
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

        // 专家模式：改走 /agent/stream_export_run，由后端按选中的专家动态构建 Agent
        // （加载该专家当前生效版本的提示词，未选专家时退化为通用 Agent）。
        // SSE 事件格式与 /agent/chat/stream 完全一致，onChunk 复用同一处理器。
        if (this.selectedExpert && this.selectedExpert.export_agent_id) {
          await streamExportRun(
            {
              message,
              sessionId: this.currentSessionId,
              expertAgentId: this.selectedExpert.export_agent_id,
              model: this.selectedModel,
              enableDeepThink,
              enableWebSearch,
              files,
              // 已选技能：结构化字段（技能目录名），`/目录名` 由后端拼进模型输入。
              // 连接器不随消息提交：本会话启用哪些连接器由 /agent/connectors/session 设置。
              skills,
            },
            onChunk,
            abortSignal
          )
        } else {
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
            skills
          )
        }

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
    // HITL: 用户审批文件删除操作后恢复执行
    async handleToolApproval(decision) {
      if (!this.pendingApproval) return

      const currentApproval = this.pendingApproval
      const { threadId, assistantMsgId } = currentApproval
      const sessionId = this.currentSessionId

      const decisions = decision === 'approve'
        ? [{ type: 'approve' }]
        : [{ type: 'reject', message: '用户拒绝了此操作，请勿重试此删除命令。' }]

      // 清除 tool_call block 的 pending_approval 状态
      if (assistantMsgId) {
        const idx = this.messages.findIndex(m => m.id === assistantMsgId)
        if (idx !== -1) {
          for (const blk of this.messages[idx].blocks) {
            if (blk.pending_approval) {
              blk.pending_approval = false
              // 记录用户审批决策，供前端显示「已批准 / 已拒绝」持久标记
              blk.approval_status = decision === 'approve' ? 'approved' : 'rejected'
            }
          }
          setReactive(this.messages, idx, { ...this.messages[idx], blocks: [...this.messages[idx].blocks] })
        }
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
              for (const ar of data.action_requests) {
                let blk = this.messages[idx].blocks.find(
                  b => b.type === 'tool_call' && (b.tool_call_id === ar.tool_call_id || b.id === ar.tool_call_id)
                )
                // 兜底：按 tool_name 匹配最后一个同名工具块
                if (!blk && ar.tool_name) {
                  for (let i = this.messages[idx].blocks.length - 1; i >= 0; i--) {
                    const b = this.messages[idx].blocks[i]
                    if (b.type === 'tool_call' && b.tool_name === ar.tool_name) {
                      blk = b
                      break
                    }
                  }
                }
                if (blk) {
                  blk.pending_approval = true
                  blk.approval_status = 'pending'
                  if (ar.tool_call_id) {
                    blk.tool_call_id = ar.tool_call_id
                    blk.id = ar.tool_call_id
                  }
                  if (ar.file_paths && ar.file_paths.length > 0) {
                    blk.file_paths = ar.file_paths
                  }
                }
              }
              setReactive(this.messages, idx, { ...this.messages[idx], blocks: [...this.messages[idx].blocks] })
            }
          }
        },
      })

      let resumeFailed = false
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
        resumeFailed = true
        const idx = this.messages.findIndex(m => m.id === assistantMsgId)
        if (idx !== -1) {
          // 回滚乐观更新：请求根本没送达/未被受理时，本轮被批准的工具尚未在后端
          // 执行（duration==null），恢复「待审批」让审批按钮可重试；否则徽章会停在
          // 「已批准」而库里仍是 pending，刷新页面后状态回跳，看起来像"审批了却还在询问"
          const rolledStatus = decision === 'approve' ? 'approved' : 'rejected'
          for (const blk of this.messages[idx].blocks) {
            if (blk.approval_status === rolledStatus && blk.duration == null) {
              blk.approval_status = 'pending'
              blk.pending_approval = true
            }
          }
          this.messages[idx].loading = false
          this.messages[idx].error = `审批请求未生效：${e.message || '恢复执行失败'}，请重试`
          setReactive(this.messages, idx, { ...this.messages[idx], blocks: [...this.messages[idx].blocks] })
        }
      } finally {
        // 如果 resumeStream 期间收到了新的 approval_required（onChunk 重新设置了
        // pendingApproval），则不清除，保持 isStreaming=true 等待用户审批
        const hasNewApproval = this.pendingApproval && this.pendingApproval !== currentApproval
        if (!hasNewApproval) {
          // 失败时保留审批上下文，让回滚出的「待审批」按钮可以再次点击重试；
          // 否则 pendingApproval 被清空后 handleToolApproval 直接 return，按钮永久失效
          this.pendingApproval = resumeFailed ? currentApproval : null
          this.currentAbortController = null
          this.streamingAssistantId = null
          const sid = sessionId
          if (sid && this.sessionStates[sid]) {
            this.sessionStates[sid].isStreaming = false
            this.sessionStates[sid].abortController = null
          }
          this.unmarkStreaming(sid)
          this.saveCurrentSessionState()
          // 审批恢复流结束：以服务端历史为准回填，保证完整渲染。
          // 失败时跳过：服务端未变更（请求未送达），本地已回滚并保留了错误提示，
          // 回填会把错误提示清掉
          if (!resumeFailed) {
            await this.syncSessionFromServer(sid)
          }
          this.recomputePendingApprovalFlag(sid)
          await this.loadSessions()
        }
      }
    },
    async handleRetry(content, skills = []) {
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

        // 重发带上本轮技能：优先用结构化 message.skills（后端落库正文是用户原话，
        // 技能只存在 skills 字段）；老数据 content 带发送时拼接的 `/目录名` 前缀，
        // 由前缀解析兜底（解析按已知目录名保守匹配，不误伤路径），两者合并去重
        const trimmed = content.trim()
        await ensureSkillDirectoryLoaded()
        const parsed = parseLeadingSkillTokens(trimmed, skillDirectoryStore.names)
        const turnSkills = [...new Set([
          ...(Array.isArray(skills) ? skills.filter(Boolean) : []),
          ...parsed.dirNames,
        ])]
        this.handleSendMessage(parsed.body.trim(), [], null, true, false, turnSkills)
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
