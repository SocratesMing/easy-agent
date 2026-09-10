<template>
  <div class="app-container">
    <SessionList
      v-show="!isSidebarCollapsed"
      :sessions="sessions"
      :currentSessionId="currentSessionId"
      :streamingSessionIds="streamingSessions"
      :username="userProfile.username"
      :organizationId="userProfile.organization_id"
      :email="userProfile.email"
      :showAssets="showAssets"
      @create-session="handleCreateSession"
      @select-session="handleSelectSession"
      @delete-session="handleDeleteSession"
      @rename-session="handleRenameSession"
      @toggle-pin="handleTogglePin"
      @toggle-sidebar="toggleSidebar"
      @show-assets="handleShowAssets"
      @show-skill-center="handleShowSkillCenter"
      @show-scheduled-tasks="handleShowScheduledTasks"
      @show-profile="handleShowProfile"
      @show-settings="showSettingsPanel = true"
      @show-user-management="showUserManagementPanel = true"
    />
    
    <button 
      v-if="isSidebarCollapsed"
      class="expand-sidebar-btn"
      @click="toggleSidebar"
      title="展开侧边栏"
    >
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
        <line x1="9" y1="3" x2="9" y2="21"></line>
      </svg>
    </button>
    
    <AssetsPanel v-if="showAssets" :visible="showAssets" @close="showAssets = false" />

    <SkillCenter v-if="showSkillCenter" @close="showSkillCenter = false" />

    <ScheduledTasksPanel v-if="showScheduledTasks" @close="showScheduledTasks = false" />
    
    <UserProfile
      v-if="showUserProfile"
      @close="showUserProfile = false"
    />
    
    <SettingsPanel
      v-if="showSettingsPanel"
      @close="showSettingsPanel = false"
    />

    <UserManagementPanel
      v-if="showUserManagementPanel"
      @close="showUserManagementPanel = false"
    />
    
    <Chat
      v-else-if="!showAssets && !showUserProfile && !showSkillCenter && !showScheduledTasks"
      :messages="messages"
      :currentSessionId="currentSessionId"
      :sessionCreatedAt="currentSessionCreatedAt"
      :isStreaming="isStreaming"
      :scrollTrigger="scrollTrigger"
      :sessionUsage="sessionUsage"
      :sessionDuration="sessionDuration"
      :iterationCount="iterationCount"
      :todos="currentTodos"
      :presetQuestions="presetQuestions"
      :welcomeTitle="welcomeTitle"
      :workspaceExpanded="!isWorkspaceCollapsed"
      :sidebarCollapsed="isSidebarCollapsed"
      :models="availableModels"
      :selectedModel="selectedModel"
      @update:selectedModel="selectedModel = $event"
      @send-message="handleSendMessage"
      @create-session="ensureCurrentSession"
      @remove-file="handleRemoveFile"
      @stop="handleStop"
      @retry="handleRetry"
      @approve="handleToolApproval('approve')"
      @reject="handleToolApproval('reject')"
    />

    <div v-if="currentSessionId && !showAssets && !showUserProfile && !showSkillCenter" class="workspace-area">
      <WorkspacePanel
        :username="userProfile.username"
        :currentSessionId="currentSessionId"
        :isStreaming="isStreaming"
        :visible="!isWorkspaceCollapsed"
        @toggle="isWorkspaceCollapsed = !isWorkspaceCollapsed"
      />
    </div>

    <button
      v-if="currentSessionId && isWorkspaceCollapsed && !showAssets && !showUserProfile && !showSkillCenter && !showScheduledTasks"
      class="expand-workspace-btn"
      @click="isWorkspaceCollapsed = false"
      title="展开工作区"
    >
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path>
      </svg>
    </button>

    <div v-if="error" class="error-toast">
      {{ error }}
      <button @click="error = null">×</button>
    </div>
  </div>
</template>

<script>
import { API_BASE_URL, APP_WELCOME_TITLE, appRuntime } from './config.js'
import Vue from 'vue'
import SessionList from './components/SessionList.vue'
import Chat from './components/Chat.vue'
import AssetsPanel from './components/AssetsPanel.vue'
import SkillCenter from './components/SkillCenter.vue'
import ScheduledTasksPanel from './components/ScheduledTasksPanel.vue'
import UserProfile from './components/UserProfile.vue'
import UserManagementPanel from './components/UserManagementPanel.vue'
import WorkspacePanel from './components/WorkspacePanel.vue'
import SettingsPanel from './components/SettingsPanel.vue'
import { createSession, listSessions, getChatHistory, deleteSession, sendMessage, resumeStream, renameSession, togglePinSession, getStreamStatus, attachStream } from './api/chat.js'
import { deleteFile, getUserProfile, getSessionGeneratedFiles } from './api/files.js'
import { AUTH_EXPIRED_EVENT, authFetch, passwordlessLogin } from './api/auth.js'
import { getModels as fetchModels } from './api/settings.js'

// 流式状态持久化 key
const STREAM_STATE_KEY = 'easy_agent_stream_state'

// 免密登录固定账号（已移除登录页，直接在此模拟固定值，便于联调/演示）
// 注意：username 不能为 "admin"（后端禁止免密登录 admin）；user_id 为 '0' 时由后端自动生成唯一 ID
const FIXED_LOGIN_USERNAME = 'demo'
const FIXED_LOGIN_USER_ID = '1001'

// MCP 工具结果解析：供正常流(handleSendMessage)与 HITL 恢复流(handleToolApproval)共用。
// 注意：不要把它定义在某个函数内部，否则另一处引用会抛 ReferenceError，
// 导致 tool_result 事件处理中断、工具一直显示"等待运行结果"。
function parseMCPResult(rawResult) {
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

export default {
  components: {
    AssetsPanel,
    Chat,
    ScheduledTasksPanel,
    SessionList,
    SettingsPanel,
    SkillCenter,
    UserManagementPanel,
    UserProfile,
    WorkspacePanel,
  },
  data() {
    return {
      sessions: [],
      currentSessionId: null,
      currentSessionHasFiles: false,
      // 模型选择：从配置加载可选列表，默认选 active model
      availableModels: [],
      selectedModel: null,
      welcomeTitle: APP_WELCOME_TITLE,
      // 会话状态缓存：为每个会话保存独立的流式状态
      sessionStates: {},
      loadedSessionId: null,
      messages: [],
      // 多个会话可同时流式：记录所有正在流式输出的会话 id（会话列表逐个显示"进行中"徽标）
      streamingSessions: [],
      // 最近开始流式的会话（页面刷新后据此重新挂载）
      lastStreamingSession: null,
      // 当前正在流式输出的 assistant 消息 id（组件级，供 handleStop 等跨函数使用；
      // 之前误引用 handleSendMessage 的局部变量 assistantMsgId 导致停止按钮抛 ReferenceError）
      streamingAssistantId: null,
      error: null,
      currentAbortController: null,
      // HITL: 审批待处理状态，存储 { threadId, assistantMsgId }
      pendingApproval: null,
      sessionUsage: { input_tokens: 0, output_tokens: 0, total_tokens: 0, max_input_tokens: null, auto_compress_tokens: null, context_tokens: 0 },
      // 当前会话累计耗时（秒），每次 AI 回复完成后累加
      sessionDuration: 0,
      // 当前会话累计迭代次数（step 数），每次 AI 回复完成后累加
      iterationCount: 0,
      currentTodos: [],
      presetQuestions: [],
      // 本页已建立实时挂载的会话及模式（避免重复挂载/重复回放）：
      // { [sessionId]: 'displayed' | 'background' }
      attachedStreamingSessions: {},
      isSidebarCollapsed: false,
      isWorkspaceCollapsed: true,
      showAssets: false,
      showSkillCenter: false,
      showScheduledTasks: false,
      showUserProfile: false,
      showSettingsPanel: false,
      showUserManagementPanel: false,
      scrollTrigger: 0,
      userProfile: {
        username: '',
        organization_id: '',
        email: ''
      },
      // 非响应式定时器句柄
      filesCheckTimer: null
    }
  },
  computed: {
    currentSessionCreatedAt() {
      if (!this.currentSessionId) return null
      const session = this.sessions.find(s => s.session_id === this.currentSessionId)
      return session && session.created_at ? session.created_at : null
    },
    // 输入框状态：当前展示的会话正在流式时才显示停止按钮与 token 用量
    isStreaming() {
      return this.isSessionStreaming(this.currentSessionId)
    }
  },
  watch: {
    // 流式会话集合变化 -> 持久化到 sessionStorage（页面刷新后据此恢复）
    streamingSessions() {
      this.saveStreamState()
    },
    lastStreamingSession() {
      this.saveStreamState()
    },
    // HITL 历史恢复：切换/重载会话后重建待审批状态
    loadedSessionId() {
      this.reconstructPendingApproval()
    }
  },
  mounted() {
    // 事件监听需绑定实例，便于移除
    // 登录态失效（401：token 过期/被顶下线）→ 自动重新免密登录，无需人工介入
    this._onAuthExpired = () => { this.handleAuthExpired() }
    window.addEventListener(AUTH_EXPIRED_EVENT, this._onAuthExpired)
    this.initApp()
  },
  beforeDestroy() {
    window.removeEventListener(AUTH_EXPIRED_EVENT, this._onAuthExpired)
  },
methods: {
    // 应用初始化：打开页面即免密登录 -> 加载用户资料 -> 恢复会话与流式任务
    async initApp() {
      // 免密登录（已移除登录页）：username 取地址栏参数或本地上次登录名
      if (!(await this.autoLogin())) return
      await this.loadUserProfile()
      // 拉取可选模型列表（不阻塞会话加载）
      this.loadModels()
      await this.loadSessions()
      if (this.sessions.length > 0) {
        const initialSessionId = this.sessions[0].session_id
        this.currentSessionId = initialSessionId
        // 尝试从缓存恢复，否则加载历史
        if (!this.restoreSessionState(initialSessionId)) {
          try {
            const history = await getChatHistory(initialSessionId)
            // 加载期间用户可能已切换会话，丢弃过期响应
            if (this.currentSessionId !== initialSessionId) return
            this.messages = history.messages || []
            this.currentTodos = history.todos || []
            if (history.usage) {
              this.sessionUsage.input_tokens = history.usage.input_tokens || 0
              this.sessionUsage.output_tokens = history.usage.output_tokens || 0
              this.sessionUsage.total_tokens = history.usage.total_tokens || 0
              this.sessionUsage.context_tokens = history.usage.context_tokens || 0
              this.sessionDuration = history.usage.elapsed_time || 0
              this.iterationCount = history.usage.step_count || 0
            }
            if (history.max_input_tokens) {
              this.sessionUsage.max_input_tokens = history.max_input_tokens
            }
            this.loadedSessionId = initialSessionId
          } catch (e) {
            console.error('加载聊天历史失败:', e)
          }
        }
        // 刷新前若正在流式，重新挂载并继续接收事件
        await this.tryAttachLiveStream()
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
    // ── 流式状态持久化（sessionStorage）─────────────────────────────────────
    // 页面刷新后据此恢复「输入框状态不变、页面持续流式返回」：
    // - 恢复输入框草稿（ChatInput 自行持久化）
    // - 若刷新前正在流式，则重新挂载到后端进行中的流式任务继续接收事件
    saveStreamState() {
      const ids = this.streamingSessions.filter(Boolean)
      if (ids.length > 0) {
        sessionStorage.setItem(STREAM_STATE_KEY, JSON.stringify({
          streamingIds: ids,
          lastActive: this.lastStreamingSession || ids[ids.length - 1],
          streaming: true,
          at: Date.now(),
        }))
      } else {
        sessionStorage.removeItem(STREAM_STATE_KEY)
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
    // HITL 历史恢复：从消息中重建待审批状态，使切换/重载会话后仍能显示审批按钮并继续执行。
    // 历史消息无前端 id，按 thread_id（= `${session_id}-${message_id}`）还原后端 message_id
    // 作为消息 id，保证恢复执行时记录使用一致的 message_id。监听 loadedSessionId 变化即可
    // 覆盖所有历史加载路径（初始加载、切换会话、免密登录后加载、删除会话回退等）。
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
    },
    // ── 面板显隐 ──
    toggleSidebar() {
      this.isSidebarCollapsed = !this.isSidebarCollapsed
    },
    handleShowAssets() {
      this.showAssets = !this.showAssets
      this.showSkillCenter = false
      this.showScheduledTasks = false
    },
    handleShowSkillCenter() {
      this.showSkillCenter = !this.showSkillCenter
      this.showAssets = false
      this.showScheduledTasks = false
    },
    handleShowScheduledTasks() {
      this.showScheduledTasks = !this.showScheduledTasks
      this.showAssets = false
      this.showSkillCenter = false
    },
    handleShowProfile() {
      this.showUserProfile = true
      this.showAssets = false
      this.showSkillCenter = false
      this.showScheduledTasks = false
    },
    // 应用配置下发
    applyAgentConfig(configData) {
      if (!configData) return
      if (configData.max_input_tokens) {
        this.sessionUsage.max_input_tokens = configData.max_input_tokens
      }
      if (configData.preset_questions) {
        this.presetQuestions = configData.preset_questions
      }
      if (typeof configData.win === 'boolean') {
        appRuntime.win = configData.win
      }
      if (configData.agent_env) {
        appRuntime.agentEnv = configData.agent_env
      }
      if (typeof configData.app_welcome_title === 'string' && configData.app_welcome_title.trim()) {
        this.welcomeTitle = configData.app_welcome_title
      }
    },
    // 打开页面即免密登录：账号直接使用代码中固定的模拟值（见 FIXED_LOGIN_USERNAME / FIXED_LOGIN_USER_ID）
    async autoLogin() {
      const username = FIXED_LOGIN_USERNAME
      const userId = FIXED_LOGIN_USER_ID
      try {
        const data = await passwordlessLogin(username, userId)
        this.userProfile = {
          username: data.username || username,
          organization_id: '',
          email: ''
        }
        if (data.max_input_tokens) {
          this.sessionUsage.max_input_tokens = data.max_input_tokens
        }
        return true
      } catch (e) {
        console.error('免密登录失败:', e)
        this.error = e.message || '免密登录失败'
        return false
      }
    },
    // 登录态失效（401：token 过期/被顶下线）后自动重新免密登录，避免出现需要人工介入的登录页
    async handleAuthExpired() {
      if (this._relogging) return
      this._relogging = true
      try {
        console.warn('登录态已失效，正在自动重新免密登录…')
        await this.autoLogin()
      } finally {
        this._relogging = false
      }
    },
    // 加载用户资料并应用后端下发的运行期配置；失败只提示，不再回到登录页
    async loadUserProfile() {
      try {
        const profile = await getUserProfile()
        this.userProfile = {
          username: profile.username || this.userProfile.username,
          organization_id: profile.organization_id || '',
          email: profile.email || ''
        }
        if (profile.max_input_tokens) {
          this.sessionUsage.max_input_tokens = profile.max_input_tokens
        }
      } catch (e) {
        console.error('加载用户资料失败:', e)
      }

      try {
        const configResp = await authFetch(`${API_BASE_URL}/agent/auth/config`)
        if (configResp.ok) {
          const configData = await configResp.json()
          this.applyAgentConfig(configData)
        }
      } catch (e) {
        console.warn('获取模型配置失败:', e)
      }
    },
    async loadModels() {
      try {
        const data = await fetchModels()
        this.availableModels = data.models || []
        // 默认选 active model（仅当当前未选择时）
        if (!this.selectedModel && data.active_model) {
          this.selectedModel = data.active_model
        }
        console.log(
          `[${new Date().toISOString()}] [模型列表] 加载成功 | 可选: ${this.availableModels.map(m => m.name).join(',')} | 当前: ${this.selectedModel}`
        )
      } catch (e) {
        console.error('加载模型列表失败:', e)
      }
    },
    async loadSessions() {
      try {
        const data = await listSessions(this.userProfile.username || null)
        this.sessions = Array.isArray(data) ? data : (data.sessions || [])
      } catch (e) {
        console.error('加载会话列表失败:', e)
        this.error = '加载会话列表失败'
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
          console.log('[Files] Session', targetId, 'has files:', this.currentSessionHasFiles, 'count:', files && files.length)
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
    async ensureCurrentSession(initialTitle = '') {
      if (!this.currentSessionId) {
        const newSession = await createSession(initialTitle || '新会话', this.userProfile.username || null)
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

      this.showAssets = false
      this.showSkillCenter = false
      this.showScheduledTasks = false
      this.showUserProfile = false
      this.showSettingsPanel = false
      this.currentSessionId = null
      this.loadedSessionId = null
      this.messages = []
      this.currentTodos = []
      // 注意：保留 max_input_tokens（全局上下文窗口，对所有会话通用），不重置为 null，
      // 否则 contextPercent 分母为 null 时会强制显示为 0%
      this.sessionUsage = { input_tokens: 0, output_tokens: 0, total_tokens: 0, max_input_tokens: this.sessionUsage.max_input_tokens, auto_compress_tokens: null, context_tokens: 0 }
      this.sessionDuration = 0
      this.iterationCount = 0
      this.refreshSessionFiles(null)
    },
    async handleSelectSession(sessionId) {
      // 保存当前会话状态
      this.saveCurrentSessionState()

      this.showAssets = false
      this.showSkillCenter = false
      this.showScheduledTasks = false
      this.currentSessionId = sessionId
      // 切换后按当前会话是否正在流式输出决定输入框状态：
      // 历史会话通常不是当前流式会话，应显示发送按钮而非停止按钮
      // 输入框状态由 isStreaming（当前会话是否在流式会话集合中）自动决定

      // 切换到进行中的会话：若本页尚未挂载其流式任务，懒挂载以继续接收事件
      // （刷新后恢复出的多个进行中会话，除当前展示外都走这里）
      if (this.isSessionStreaming(sessionId) && this.attachedStreamingSessions[sessionId] !== 'displayed') {
        await this.attachToStreamingSession(sessionId, { switchTo: false, displayed: true })
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
      // 保留 max_input_tokens（全局上下文窗口），仅清空用量计数；
      // 若服务器返回了 max_input_tokens 则以其为准（见下方恢复逻辑）
      this.sessionUsage = { input_tokens: 0, output_tokens: 0, total_tokens: 0, max_input_tokens: this.sessionUsage.max_input_tokens, auto_compress_tokens: null, context_tokens: 0 }
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
          this.sessionUsage.total_tokens = history.usage.total_tokens || 0
          this.sessionUsage.context_tokens = history.usage.context_tokens || 0
          this.sessionDuration = history.usage.elapsed_time || 0
          this.iterationCount = history.usage.step_count || 0
        }
        if (history.max_input_tokens) {
          this.sessionUsage.max_input_tokens = history.max_input_tokens
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
      }
    },
async handleDeleteSession(sessionId) {
      try {
        await deleteSession(sessionId)
        this.sessions = this.sessions.filter(s => s.session_id !== sessionId)

        // 清除会话缓存
        delete this.sessionStates[sessionId]

        if (this.currentSessionId === sessionId) {
          this.currentSessionId = this.sessions[0] ? this.sessions[0].session_id : null
          if (this.currentSessionId && this.sessionStates[this.currentSessionId]) {
            this.restoreSessionState(this.currentSessionId)
          } else {
            this.messages = []
            this.loadedSessionId = null
          }
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
            this.sessionUsage.total_tokens = history.usage.total_tokens || 0
            this.sessionUsage.context_tokens = history.usage.context_tokens || 0
            this.sessionDuration = history.usage.elapsed_time || 0
            this.iterationCount = history.usage.step_count || 0
          }
          if (history.max_input_tokens) {
            this.sessionUsage.max_input_tokens = history.max_input_tokens
          }
        } catch (e) {
          console.error('加载聊天历史失败:', e)
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
            total_tokens: 0,
            max_input_tokens: this.sessionUsage.max_input_tokens,
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
          if (displayed) {
            this.streamingAssistantId = null
            this.saveCurrentSessionState()
          }
          await this.loadSessions()
        }
      }
    },
    // 刷新后恢复全部进行中的流式任务：逐个查询后端状态，活跃的恢复"进行中"标记；
    // 最近活跃的会话直接重挂载，其余会话在切换到它时懒挂载。
    async tryAttachLiveStream() {
      let saved = null
      try {
        const raw = sessionStorage.getItem(STREAM_STATE_KEY)
        if (!raw) return
        saved = JSON.parse(raw)
      } catch (e) {
        return
      }
      if (!saved || !saved.streaming) return

      const candidates = Array.isArray(saved.streamingIds) && saved.streamingIds.length
        ? saved.streamingIds
        : (saved.sessionId ? [saved.sessionId] : [])
      const active = []
      for (const sid of candidates) {
        try {
          const status = await getStreamStatus(sid)
          if (status && status.active && !active.includes(sid)) active.push(sid)
        } catch (e) {
          // 会话可能已被删除：忽略
        }
      }
      for (const sid of active) this.markStreaming(sid)

      const attachTarget = saved.lastActive && active.includes(saved.lastActive)
        ? saved.lastActive
        : active[0]
      if (attachTarget) {
        await this.attachToStreamingSession(attachTarget, { switchTo: true, displayed: true })
      }
      // 其余仍活跃的会话后台挂载：同步 token 用量、流结束时自动移除"进行中"标记
      for (const sid of active) {
        if (sid === attachTarget) continue
        this.attachToStreamingSession(sid, { switchTo: false, displayed: false })
      }
      // 清理一次性恢复标记；仍活跃的流会通过 watch 重新写入持久化
      this.saveStreamState()
    },
    // 流式事件处理工厂：所有正常流 / 重挂载 / HITL 恢复流共用同一套渲染逻辑
    createStreamChunkHandler(ctx) {
      let currentBlock = null
      let currentThinking = ''
      let currentContent = ''
      let currentToolCalls = []
      let blockOrderCounter = ctx.initialBlockOrder || 0
      let totalThinkingDuration = 0

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
          total_tokens: data.session_estimate || data.total_tokens || 0,
          context_tokens: data.context_tokens || this.sessionUsage.context_tokens || 0,
        }
        if (data.max_input_tokens) patch.max_input_tokens = data.max_input_tokens
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
        const { type: eventType, content, duration, step, tool_name, tool_call_id: toolCallId, arguments: args, result, success, title } = data
        const sid = ctx.streamSessionId

        if (eventType === 'start') {
          if (!ctx.isResume) {
            this.currentTodos = []
            if (data.session_id && !this.currentSessionId) {
              this.currentSessionId = data.session_id
              this.loadedSessionId = data.session_id
              this.loadSessions()
            }
            if (data.session_id) ctx.streamSessionId = data.session_id
            this.markStreaming(ctx.streamSessionId || this.currentSessionId)
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
          totalThinkingDuration += duration || 0
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
            const callId = toolCallId || `tool-${tool_name}`
            const existingBlockIdx = this.messages[idx].blocks.findIndex(b => b.type === 'tool_call' && (b.id === callId || b.tool_call_id === callId))
            if (existingBlockIdx !== -1) {
              this.messages[idx].blocks[existingBlockIdx].arguments = args || {}
              currentBlock = this.messages[idx].blocks[existingBlockIdx]
              touchBlocks()
            } else {
              currentBlock = null
              if (!ctx.isResume) currentToolCalls.push({ tool_call_id: callId, tool_name: tool_name || '', arguments: args || {}, result: '', success: true })
              addBlock('tool_call', { id: callId, tool_name: tool_name || '', arguments: args || {}, result: '', success: true, step: step || 0 })
            }
          }
        } else if (eventType === 'tool_result') {
          const callId = toolCallId || `tool-${tool_name}`
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
                this.messages[idx].usage = { input_tokens: data.usage.input_tokens || 0, output_tokens: data.usage.output_tokens || 0, total_tokens: data.usage.total_tokens || 0 }
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

      return { onChunk }
    },
    async handleSendMessage(message, files = [], signal, enableDeepThink = true) {
      const userMsgId = `user-${Date.now()}`
      const preStreamUsage = { ...this.sessionUsage }
      // 记录本次请求开始前已累计的耗时和迭代次数，用于流式过程中实时累加
      const preStreamDuration = this.sessionDuration
      const preStreamIterationCount = this.iterationCount
      // 本次流所属的会话 ID（新会话在 start 事件后才有值）。
      // 用于用户中途切换会话时，把用量更新写入所属会话的缓存而非当前显示
      let streamSessionId = this.currentSessionId

      const contentWithFiles = message.trim().replace(/\s+/g, ' ')

      const userMessage = {
        id: userMsgId,
        role: 'user',
        content: contentWithFiles,
        files: files.map(f => ({
          filename: f.filename,
          size: f.size,
          type: f.file.type,
          file_path: f.file_path || null
        })),
        created_at: new Date().toISOString()
      }

      this.messages.push(userMessage)

      // 立即创建 assistant 占位消息，显示等待动画
      const assistantPlaceholderId = `assistant-${Date.now()}`
      const assistantPlaceholder = {
        id: assistantPlaceholderId,
        role: 'assistant',
        content: '',
        created_at: null,
        thinking: '',
        tool_calls: [],
        blocks: [],
        loading: true
      }
      this.messages.push(assistantPlaceholder)
      this.streamingAssistantId = assistantPlaceholderId

      let assistantMsgId = null
      let assistantMessageCreated = false

      const { onChunk } = this.createStreamChunkHandler({
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
              })
            }
            this.streamingAssistantId = assistantMsgId
            assistantMessageCreated = true
          }
        },
        onApprovalRequired: (data) => {
          this.pendingApproval = { threadId: data.thread_id, assistantMsgId }
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
      })

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

        await sendMessage(this.currentSessionId, message, onChunk, abortSignal, enableDeepThink, files, this.selectedModel)

        await this.refreshSessionFiles(null, 500)
      } catch (e) {
        if (e.name === 'AbortError') {
          // Mark assistant message as complete (loading=false) so spinners stop
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
          return
        }
        console.error('发送消息失败:', e)
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
        this.error = e.message || '发送消息失败'
      } finally {
        // HITL: 若有审批待处理，保持 isStreaming=true（用户需先审批）
        if (!this.pendingApproval) {
          this.currentAbortController = null
          this.streamingAssistantId = null

          const sid = streamSessionId
          if (sid && this.sessionStates[sid]) {
            this.sessionStates[sid].isStreaming = false
            this.sessionStates[sid].abortController = null
          }
          this.unmarkStreaming(sid)

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
        authFetch(`${API_BASE_URL}/agent/chat/cancel?session_id=${encodeURIComponent(sid)}`, {
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
  }
}
</script>

<style scoped>
.app-container {
  display: flex;
  height: 100vh;
  width: 100vw;
  background: #f8fafc;
  position: relative;
}

.expand-sidebar-btn {
  position: absolute;
  left: 16px;
  top: 16px;
  width: 40px;
  height: 40px;
  background: white;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  z-index: 100;
  transition: all 0.2s ease;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
}

.expand-sidebar-btn:hover {
  background: #f8fafc;
  border-color: #cbd5e1;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

.expand-workspace-btn {
  position: fixed;
  right: 16px;
  top: 16px;
  width: 40px;
  height: 40px;
  background: white;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  z-index: 100;
  transition: all 0.2s ease;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
}

.expand-workspace-btn:hover {
  background: #f0fdf4;
  border-color: #86efac;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

.workspace-area {
  position: fixed;
  right: 0;
  top: 0;
  bottom: 0;
  z-index: 30;
}

.expand-sidebar-btn svg {
  width: 20px;
  height: 20px;
  color: #64748b;
}

.error-toast {
  position: fixed;
  bottom: 100px;
  left: 50%;
  transform: translateX(-50%);
  background: #fee2e2;
  color: #dc2626;
  padding: 12px 20px;
  border-radius: 10px;
  font-size: 14px;
  display: flex;
  align-items: center;
  gap: 12px;
  animation: slideUp 0.3s ease-out;
  box-shadow: 0 4px 12px rgba(220, 38, 38, 0.3);
  z-index: 1000;
  max-width: 600px;
  word-break: break-word;
}

.error-toast button {
  background: transparent;
  border: none;
  font-size: 18px;
  cursor: pointer;
  color: #dc2626;
  padding: 0;
  line-height: 1;
}

@keyframes slideUp {
  from {
    opacity: 0;
    transform: translateX(-50%) translateY(20px);
  }
  to {
    opacity: 1;
    transform: translateX(-50%) translateY(0);
  }
}

/* 响应式：小屏幕优化 */
@media (max-width: 768px) {
  .expand-workspace-btn,
  .expand-sidebar-btn {
    width: 36px;
    height: 36px;
  }

  .expand-workspace-btn svg,
  .expand-sidebar-btn svg {
    width: 18px;
    height: 18px;
  }

  .error-toast {
    max-width: 90vw;
    font-size: 13px;
    padding: 10px 16px;
  }
}
</style>

<!-- 非 scoped 样式：:root 选择器在 scoped 中无法匹配 <html> 元素 -->
<style>
/* 全局 CSS 变量（仅浅色主题） */
:root {
  --bg-primary: #f8fafc;
  --bg-secondary: #ffffff;
  --bg-tertiary: #f1f5f9;
  --bg-surface: #ffffff;
  --text-primary: #1e293b;
  --text-secondary: #64748b;
  --border-color: #e2e8f0;
  --accent-color: #0ea5e9;
}
</style>
