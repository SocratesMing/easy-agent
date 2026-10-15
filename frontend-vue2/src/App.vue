<template>
  <div class="app-container">
    <Welcome 
      v-if="showWelcome" 
      @completed="handleWelcomeCompleted" 
    />

    <!-- 登录页关闭时（生产）：无有效登录态时的兜底提示 -->
    <div v-else-if="authBlocked" class="auth-blocked">
      <div class="auth-blocked-box">
        <h2>未授权访问</h2>
        <p v-if="passwordlessEnabled">
          免密登录未成功（未获取到门户共享登录信息或后端校验未通过）。请从门户系统重新进入本页面。
        </p>
        <p v-else>登录页已关闭，且免密登录未开启。请联系管理员分配账号后使用账号密码登录。</p>
      </div>
    </div>

    <template v-else-if="!isBootstrapping">
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
        @show-settings="showSettingsPanel = true"
        @show-user-management="showUserManagementPanel = true"
        @logout="handleLogout"
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

      <SkillCenter
        v-if="showSkillCenter"
        :session-id="currentSessionId || ''"
        :initial-tab="skillCenterTab"
        @close="showSkillCenter = false"
        @create-skill="handleSkillCreate"
        @use="handleUseExpert"
      />

      <ScheduledTasksPanel v-if="showScheduledTasks" @close="showScheduledTasks = false" />
      
      <SettingsPanel
        v-if="showSettingsPanel"
        @close="showSettingsPanel = false"
      />

      <UserManagementPanel
        v-if="showUserManagementPanel"
        @close="showUserManagementPanel = false"
      />
      
      <Chat
        v-else-if="!showAssets && !showSkillCenter && !showScheduledTasks"
        :messages="messages"
        :sessionLoading="sessionLoading"
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
        :activeExpert="selectedExpert"
        :expertPrompt="expertPrompt"
        @expert-prompt-applied="expertPrompt = ''"
        @update:selectedModel="selectedModel = $event"
        @toggle-workspace="isWorkspaceCollapsed = false"
        @send-message="handleSendMessage"
        @create-session="ensureCurrentSession"
        @remove-file="handleRemoveFile"
        @stop="handleStop"
        @retry="handleRetry"
        @approve="handleToolApproval('approve')"
        @reject="handleToolApproval('reject')"
        @openPanel="openSkillCenterPanel"
        @clear-expert="selectedExpert = null"
        @select-expert="handleUseExpert"
      />

      <div v-if="currentSessionId && !showAssets && !showSkillCenter" class="workspace-area">
        <WorkspacePanel
          :username="userProfile.username"
          :currentSessionId="currentSessionId"
          :isStreaming="isStreaming"
          :visible="!isWorkspaceCollapsed"
          @toggle="isWorkspaceCollapsed = !isWorkspaceCollapsed"
        />
      </div>

      <!-- AI 侧边栏：任意页面可唤起（默认隐藏），与主页面共用会话列表 -->
      <AiSidebar
        v-if="userProfile.username"
        :pageContext="aiPageContext"
        @open-in-main="handleOpenSessionInMain"
      />

      <div v-if="error" class="error-toast">
        {{ error }}
        <button @click="error = null">×</button>
      </div>
    </template>
  </div>
</template>

<script>
import SessionList from './components/SessionList.vue'
import Chat from './components/Chat.vue'
import AssetsPanel from './components/AssetsPanel.vue'
import SkillCenter from './components/SkillCenter.vue'
import ScheduledTasksPanel from './components/ScheduledTasksPanel.vue'
import UserManagementPanel from './components/UserManagementPanel.vue'
import Welcome from './components/Welcome.vue'
import WorkspacePanel from './components/WorkspacePanel.vue'
import SettingsPanel from './components/SettingsPanel.vue'
import AiSidebar from './components/AiSidebar.vue'
import { getChatHistory, getStreamStatus } from './api/chat.js'
import { getUserProfile } from './api/files.js'
import chatSessionMixin from './mixins/chatSession.js'
import { chatStore } from './store/chatStore.js'
import { buildPageContext } from './utils/pageContext.js'
import { logout as apiLogout, notifyLogout, getStoredToken, getStoredUsername, AUTH_EXPIRED_EVENT, authFetch, passwordlessLogin } from './api/auth.js'
import { getModels as fetchModels } from './api/settings.js'
import { streamExportRun } from './api/expert.js'

// ── 应用级常量（原 src/config.js 已移除，直接定义在组件内）────────────
// 应用名称与首页欢迎语；后端 /agent/auth/config 返回的 app_welcome_title 仍可在运行期覆盖
const APP_TITLE = 'Easy Agent'
const APP_WELCOME_TITLE = `${APP_TITLE}，让工作化繁为简`
// 登录页开关：true 显示登录 / 注册页；false 不显示，改走门户免密登录。
// 构建期环境变量 VUE_APP_LOGIN_PAGE_ENABLED=false 可关闭（生产门户场景），
// 未配置默认开启 —— 本地/联调可用登录页切换用户。
const LOGIN_PAGE_ENABLED = process.env.VUE_APP_LOGIN_PAGE_ENABLED !== 'false'
// 免密登录开关：仅在「登录页关」时生效 —— 由前端模拟登录用户信息直接免密登录
// （不调用 loadUserProfile，用户资料取自门户共享存储 system:share:*）。
const PASSWORDLESS_LOGIN_ENABLED = true
//
// 登录方式约定：
//   登录页开（默认）：显示 Welcome 登录 / 注册页；另支持 URL 免密直登 ?username=&user_id=
//   登录页关：门户免密登录；失败 → 「未授权访问」提示

// ── 门户共享存储键（由外部系统写入 localStorage）──────────────────────────
// system:share:loginId        登录账号     → 免密登录接口的 username
// system:share:iamEmpLoginNo  IAM 员工号   → 免密登录接口的 user_id
// system:share:name           用户姓名     → userProfile.username（展示用）
// system:share:bankName       所属机构/银行 → userProfile.organization_id
const SHARE_KEY_LOGIN_ID = 'system:share:loginId'
const SHARE_KEY_EMP_NO = 'system:share:iamEmpLoginNo'
const SHARE_KEY_NAME = 'system:share:name'
const SHARE_KEY_BANK = 'system:share:bankName'
// 共享存储缺失时的模拟默认值
const DEFAULT_PASSWORDLESS_USERNAME = 'demo'
const DEFAULT_PASSWORDLESS_USER_ID = 'demoid'

/** 安全读取 localStorage（隐私模式 / 禁用存储时返回空串） */
function readShareItem(key) {
  try {
    return localStorage.getItem(key) || ''
  } catch (e) {
    return ''
  }
}

// ── 当前会话视图持久化 ────────────────────────────────────────────
// 刷新后恢复到用户刷新前所在的会话，而不是永远跳到列表第一个。
// 用 '__new__' 记录"新会话首页"（currentSessionId 为 null）这一状态。
const ACTIVE_SESSION_KEY = 'easy_agent_active_session'
const NEW_SESSION_MARK = '__new__'

// ── 流式状态持久化（sessionStorage）─────────────────────────────────────
const STREAM_STATE_KEY = 'easy_agent_stream_state'

export default {
  components: {
    AiSidebar,
    AssetsPanel,
    Chat,
    ScheduledTasksPanel,
    SessionList,
    SettingsPanel,
    SkillCenter,
    UserManagementPanel,
    Welcome,
    WorkspacePanel,
  },
  mixins: [chatSessionMixin],
  data() {
    return {
      // bootstrap（认证 + 会话列表 + 首个会话历史）完成前不写入，避免用初始 null 覆盖已存值
      hasBootstrapped: false,
      // 模型选择：从配置加载可选列表，默认选 active model
      availableModels: [],
      selectedModel: null,
      welcomeTitle: APP_WELCOME_TITLE,
      // 「未授权」提示态（登录页关闭且免密登录未成功时显示）
      authBlocked: false,
      presetQuestions: [],
      isSidebarCollapsed: false,
      isWorkspaceCollapsed: true,
      showAssets: false,
      showSkillCenter: false,
      skillCenterTab: 'experts',
      selectedExpert: null,
      expertPrompt: '',
      showScheduledTasks: false,
      showSettingsPanel: false,
      showUserManagementPanel: false,
      showWelcome: false,
      // 免密登录开关（模板用：未授权提示的文案按开关区分）
      passwordlessEnabled: PASSWORDLESS_LOGIN_ENABLED,
      // 首屏引导中：认证 + 会话列表 + 首次历史加载完成前不渲染聊天区，
      // 否则会先闪出空会话首页、会话时间下方那条分隔线也会闪一下。
      isBootstrapping: true,
      userProfile: {
        username: '',
        organization_id: '',
        email: ''
      },
    }
  },
  computed: {
    // AI 侧边栏的页面上下文：描述用户唤起侧边栏时所在的页面/功能
    aiPageContext() {
      return buildPageContext({
        showAssets: this.showAssets,
        showSkillCenter: this.showSkillCenter,
        showScheduledTasks: this.showScheduledTasks,
        showSettingsPanel: this.showSettingsPanel,
        showUserManagementPanel: this.showUserManagementPanel,
        session: this.currentSessionId
          ? this.sessions.find(s => s.session_id === this.currentSessionId) || { session_id: this.currentSessionId }
          : null,
        messages: this.messages,
      })
    },
  },
  watch: {
    // 刷新后恢复到用户刷新前所在的会话；bootstrap 完成前不写入，避免用初始 null 覆盖已存值
    currentSessionId(sid) {
      if (!this.hasBootstrapped) return
      try {
        localStorage.setItem(ACTIVE_SESSION_KEY, sid || NEW_SESSION_MARK)
      } catch (_) { /* localStorage 不可用时忽略 */ }
    },
    // 流式会话集合变化 -> 持久化到 sessionStorage（页面刷新后据此恢复）
    streamingSessions() {
      this.saveStreamState()
    },
    lastStreamingSession() {
      this.saveStreamState()
    },
    // 同步到共享 store，供 AI 侧边栏使用同一用户、模型与上下文窗口配置
    'userProfile.username': {
      immediate: true,
      handler(name) { chatStore.username = name || '' },
    },
    'sessionUsage.context_length'(len) {
      if (len) chatStore.contextLength = len
    },
    availableModels(models) {
      chatStore.models = models
    },
    selectedModel(name) {
      chatStore.activeModel = name
    },
  },
  mounted() {
    // 页面标题（原 src/config.js 中设置，现收敛到组件内）
    document.title = APP_TITLE
    // 登录开关自检：两者同时关闭时没有任何登录入口，属于配置错误
    if (!LOGIN_PAGE_ENABLED && !PASSWORDLESS_LOGIN_ENABLED) {
      console.warn(
        '[登录] LOGIN_PAGE_ENABLED 与 PASSWORDLESS_LOGIN_ENABLED 同时为 false，将无任何登录入口，请检查配置'
      )
    }
    console.info(
      `[登录] 登录页=${LOGIN_PAGE_ENABLED ? '开启' : '关闭'} | 免密登录=${PASSWORDLESS_LOGIN_ENABLED ? '开启' : '关闭'}`
    )
    window.addEventListener(AUTH_EXPIRED_EVENT, this.handleLogout)
    this.initApp()
  },
  methods: {
    // 应用初始化：免密登录 -> 加载用户资料 -> 恢复会话与流式任务
    async initApp() {
      try {
        // URL 免密直登（?username=xxx&user_id=yyy）：成功则直接进入主界面
        if (await this.handlePasswordlessUrlLogin()) return
        // 免密登录（仅登录页关闭时生效）：由前端模拟用户信息直接登录，
        // 成功后 handleWelcomeCompleted 已完成首屏引导（此路径不调用 loadUserProfile）
        if (!LOGIN_PAGE_ENABLED && PASSWORDLESS_LOGIN_ENABLED && (await this.handlePasswordlessLogin())) return
        // 免密关闭或免密失败：走常规资料加载（无有效登录态时回落到登录页 / 未授权提示）
        await this.loadUserProfile()
        if (!this.showWelcome && !this.authBlocked) {
          // 拉取可选模型列表（不阻塞会话加载）
          this.loadModels()
          await this.loadSessions()
          await this.restoreInitialSession()
        }
      } finally {
        // 未登录时也解除首屏门控，让 Welcome 登录页正常显示
        this.isBootstrapping = false
      }
    },
    clearActiveSession() {
      try {
        localStorage.removeItem(ACTIVE_SESSION_KEY)
      } catch (_) { /* ignore */ }
    },
    // 需要登录时的落点：登录页开启则显示 Welcome；关闭则显示未授权提示（生产常用）
    requireLogin() {
      if (LOGIN_PAGE_ENABLED) {
        this.showWelcome = true
      } else {
        this.authBlocked = true
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
        this.attachToStreamingSession(attachTarget, { switchTo: true, displayed: true })
      }
      // 其余仍活跃的会话后台挂载：同步 token 用量、流结束时自动移除"进行中"标记
      for (const sid of active) {
        if (sid === attachTarget) continue
        this.attachToStreamingSession(sid, { switchTo: false, displayed: false })
      }
      // 清理一次性恢复标记；仍活跃的流会通过 watch 重新写入持久化
      this.saveStreamState()
    },
    toggleSidebar() {
      this.isSidebarCollapsed = !this.isSidebarCollapsed
    },
    handleShowAssets() {
      this.showAssets = !this.showAssets
      this.showSkillCenter = false
      this.showScheduledTasks = false
    },
    openSkillCenterPanel(tab) {
      // 「+」菜单入口：打开面板并直接定位到专家/技能/连接器
      this.skillCenterTab = tab
      this.showSkillCenter = true
    },
    handleShowSkillCenter() {
      this.skillCenterTab = 'experts'
      this.showSkillCenter = !this.showSkillCenter
      this.showAssets = false
      this.showScheduledTasks = false
    },
    handleShowScheduledTasks() {
      this.showScheduledTasks = !this.showScheduledTasks
      this.showAssets = false
      this.showSkillCenter = false
    },
    /**
     * 专家 Tab -> 卡片「召唤」/ 详情弹窗点示例问题：把专家挂到当前会话。
     *
     * prompt 非空时（点的是「专家帮你做」里的一条）额外把问题回填到输入框。
     *
     * 不强制新建会话：后端 thread_id = `{session_id}-{message_id}`，每条消息都是新线程，
     * 且 get_or_create_agent_for_session() 已按「专家是否变更」驱逐缓存，
     * 因此同一会话内切换专家能立即生效（详见 docs/expert-agent-solution.md）。
     */
    handleUseExpert(expert, prompt) {
      if (!expert || !expert.export_agent_id) return
      this.selectedExpert = {
        export_agent_id: expert.export_agent_id,
        export_name_cn: expert.export_name_cn,
        export_version: expert.export_version,
      }
      this.expertPrompt = typeof prompt === 'string' ? prompt : ''
      this.showSkillCenter = false
    },
    applyAgentConfig(configData) {
      if (!configData) return
      if (configData.context_length) {
        this.sessionUsage.context_length = configData.context_length
      }
      if (configData.preset_questions) {
        this.presetQuestions = configData.preset_questions
      }
      if (typeof configData.app_welcome_title === 'string' && configData.app_welcome_title.trim()) {
        this.welcomeTitle = configData.app_welcome_title
      }
    },
    // 依据持久化的选择恢复初始会话视图：
    //   - 上次所在会话（仍存在）→ 恢复该会话；
    //   - '__new__' → 保持新会话首页（currentSessionId 为 null）；
    //   - 无记录 / 会话已被删除 → 回退到列表第一个；列表为空则新会话首页。
    // 加载对应历史后解除首屏门控（isBootstrapping），避免闪出空首页与顶部分隔线。
    async restoreInitialSession() {
      let stored = null
      try {
        stored = localStorage.getItem(ACTIVE_SESSION_KEY)
      } catch (_) { /* ignore */ }

      // ?session=<id>：嵌入第三方页面的 AI 侧边栏「在主页面打开」时携带，优先打开该会话
      const params = new URLSearchParams(window.location.search)
      const linked = params.get('session')
      if (linked) {
        params.delete('session')
        const query = params.toString()
        window.history.replaceState({}, '', window.location.pathname + (query ? `?${query}` : ''))
      }

      let initialSessionId = null
      if (linked && this.sessions.some(s => s.session_id === linked)) {
        initialSessionId = linked
      } else if (stored === NEW_SESSION_MARK) {
        initialSessionId = null
      } else if (stored && this.sessions.some(s => s.session_id === stored)) {
        initialSessionId = stored
      } else if (this.sessions.length > 0) {
        initialSessionId = this.sessions[0].session_id
      }

      if (initialSessionId) {
        this.currentSessionId = initialSessionId
        if (!this.restoreSessionState(initialSessionId)) {
          try {
            const history = await getChatHistory(initialSessionId)
            // 加载期间用户可能已切换会话，丢弃过期响应（正常 bootstrap 期间 UI 门控，不会发生）
            if (this.currentSessionId === initialSessionId) {
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
              this.loadedSessionId = initialSessionId
            }
          } catch (e) {
            console.error('加载聊天历史失败:', e)
          }
        }
      } else {
        this.currentSessionId = null
        this.messages = []
        this.currentTodos = []
        this.loadedSessionId = null
      }

      this.hasBootstrapped = true
      try {
        localStorage.setItem(ACTIVE_SESSION_KEY, this.currentSessionId || NEW_SESSION_MARK)
      } catch (_) { /* ignore */ }
      this.isBootstrapping = false

      // 刷新前若正在流式，重新挂载并继续接收事件
      await this.tryAttachLiveStream()
    },
    async handleWelcomeCompleted(profile) {
      this.isBootstrapping = true
      this.userProfile = profile
      this.showWelcome = false
      if (profile.context_length) {
        this.sessionUsage.context_length = profile.context_length
      }
      try {
        const configResp = await authFetch('/agent/auth/config')
        if (configResp.ok) {
          const configData = await configResp.json()
          this.applyAgentConfig(configData)
        }
      } catch (e) {
        console.warn('获取模型配置失败:', e)
      }
      this.loadModels()
      await this.loadSessions()
      await this.restoreInitialSession()
    },
    async handleLogout() {
      // 通知后端记录登出（用户名/上次登录缓存时间/在线时长），best-effort。
      // 仅在仍持有 token 时通知：被动登出（401 被踢下线/过期）时 token 已被 clearAuth
      // 清除，再调登出接口会再次 401 触发 AUTH_EXPIRED 事件造成循环。
      if (getStoredToken()) {
        await notifyLogout()
      }
      apiLogout()
      this.sessions = []
      this.currentSessionId = null
      this.loadedSessionId = null
      this.sessionStates = {}
      this.messages = []
      this.streamingSessions = []
      this.lastStreamingSession = null
      this.attachedStreamingSessions = {}
      this.streamingAssistantId = null
      this.currentAbortController = null
      sessionStorage.removeItem(STREAM_STATE_KEY)
      sessionStorage.removeItem('easy_agent_input_draft')
      this.userProfile = {
        username: '',
        organization_id: '',
        email: ''
      }
      this.showAssets = false
      this.showSkillCenter = false
      this.selectedExpert = null
      this.hasBootstrapped = false
      this.clearActiveSession()
      this.requireLogin()
    },
    async handleUnregister() {
      this.sessions = []
      this.currentSessionId = null
      this.loadedSessionId = null
      this.sessionStates = {}
      this.messages = []
      this.userProfile = {
        username: '',
        organization_id: '',
        email: ''
      }
      this.hasBootstrapped = false
      this.clearActiveSession()
      this.requireLogin()
    },
    async loadUserProfile() {
      const storedToken = getStoredToken()
      const storedUsername = getStoredUsername()

      if (!storedToken || !storedUsername) {
        this.requireLogin()
        return
      }

      try {
        const profile = await getUserProfile()
        if (!profile.username || profile.username === 'admin') {
          this.requireLogin()
          return
        }
        this.userProfile = {
          username: profile.username || '',
          organization_id: profile.organization_id || '',
          email: profile.email || ''
        }
      } catch (e) {
        console.error('加载用户资料失败:', e)
        this.requireLogin()
        return
      }

      try {
        const configResp = await authFetch('/agent/auth/config')
        if (configResp.ok) {
          const configData = await configResp.json()
          this.applyAgentConfig(configData)
        }
      } catch (e) {
        console.warn('获取模型配置失败:', e)
      }
    },
    // 免密登录（登录页关闭时的登录方式）：在前端「模拟登录用户信息」后调用后端免密登录接口，
    // 成功即进入主界面。
    //   · 登录身份：username 取 system:share:loginId，user_id 取 system:share:iamEmpLoginNo，
    //     两者缺失时分别回落到默认值 demo / demoid；
    //   · 用户资料：直接取门户共享存储（name / bankName / loginId），不调用 loadUserProfile；
    //   · 登录页开启（LOGIN_PAGE_ENABLED=true）或免密开关关闭时，本方法不会被调用。
    async handlePasswordlessLogin() {
      const shareLoginId = readShareItem(SHARE_KEY_LOGIN_ID)
      const username = shareLoginId || DEFAULT_PASSWORDLESS_USERNAME
      const userId =
        readShareItem(SHARE_KEY_EMP_NO) || DEFAULT_PASSWORDLESS_USER_ID
      try {
        const data = await passwordlessLogin(username, userId)
        await this.handleWelcomeCompleted({
          // username 用姓名展示；姓名缺失时回落到登录账号，避免界面显示为空
          username: readShareItem(SHARE_KEY_NAME) || username,
          organization_id: readShareItem(SHARE_KEY_BANK),
          email: shareLoginId,
          context_length: data.context_length
        })
        return true
      } catch (e) {
        console.error('免密登录失败:', e)
        return false
      }
    },
    // URL 免密直登：地址栏携带 ?username=xxx&user_id=yyy（user_id 可省略，默认 0）时
    // 直接免密登录进入主界面，优先级高于已存储的登录态；登录后清除地址栏凭证参数。
    async handlePasswordlessUrlLogin() {
      const params = new URLSearchParams(window.location.search)
      const username = params.get('username')
      if (!username) return false
      const userId = params.get('user_id') || '0'
      try {
        const data = await passwordlessLogin(username, userId)
        // 清除地址栏中的凭证参数，避免留在浏览器历史/后端访问日志
        params.delete('username')
        params.delete('user_id')
        const query = params.toString()
        window.history.replaceState({}, '', window.location.pathname + (query ? `?${query}` : ''))
        await this.handleWelcomeCompleted({
          username: data.username,
          context_length: data.context_length
        })
        return true
      } catch (e) {
        console.error('URL 免密登录失败:', e)
        return false
      }
    },
    /**
     * 技能中心的「创建技能」：预填内容已写入 sessionStorage，
     * 这里新建会话并关闭技能中心，ChatInput 随之重新挂载、自动恢复技能标签与提示词。
     */
    async handleSkillCreate() {
      await this.handleCreateSession()
    },
    // chatSessionMixin 钩子：主页面新建/切换会话时回到对话视图
    beforeSessionChange(kind) {
      this.showAssets = false
      this.showSkillCenter = false
      // 新建会话回到干净状态：专家需在新会话里重新选择，避免误把旧专家的设定带进来
      this.selectedExpert = null
      this.showScheduledTasks = false
      if (kind === 'create') this.showSettingsPanel = false
    },
    // AI 侧边栏「在主页面打开」：主对话区切到该会话
    async handleOpenSessionInMain(sessionId) {
      if (!sessionId) return
      if (sessionId === this.currentSessionId && !this.showAssets && !this.showSkillCenter && !this.showScheduledTasks) return
      await this.handleSelectSession(sessionId)
    },
  }
}
</script>

<style scoped>
.app-container {
  display: flex;
  height: 100%;
  width: 100vw;
  background: #f8fafc;
  position: relative;
}

/* 登录页关闭且未授权时的提示页 */
.auth-blocked {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}

.auth-blocked-box {
  max-width: 480px;
  padding: 32px;
  text-align: center;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  color: var(--text-secondary, #64748b);
}

.auth-blocked-box h2 {
  margin: 0 0 12px;
  font-size: 18px;
  color: var(--text-primary, #1e293b);
}

.auth-blocked-box p {
  margin: 0;
  font-size: 14px;
  line-height: 1.7;
}

.auth-blocked-box code {
  padding: 2px 6px;
  background: #f1f5f9;
  border-radius: 4px;
  color: #0ea5e9;
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

/* 工作区参与 Flex 分栏：原先是 position:fixed 的浮层，会盖住聊天区右侧内容。
   改为普通 flex 项后，展开时聊天区自动收窄、互不遮挡；宽度由 WorkspacePanel
   自己控制（可拖拽），这里只保证它不被压缩。 */
.workspace-area {
  position: relative;
  flex-shrink: 0;
  height: 100%;
  display: flex;
  z-index: 1;
}

/* 工作区页面内全屏时抬高整列：.workspace-area 自身的 z-index:1 会形成
   层叠上下文，把内部 fixed 全屏面板的层级一起限制住 —— 聊天区里的
   .scroll-btn（z-index:10）会因此浮在全屏面板之上。用 :has() 精准提升。 */
.workspace-area:has(.workspace-panel.is-fullscreen) {
  z-index: 150;
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
  .expand-sidebar-btn {
    width: 36px;
    height: 36px;
  }

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

<!-- 非 scoped 主题样式：:root 选择器在 scoped 中无法匹配 <html> 元素 -->
<style>
/* 深色主题 CSS 变量定义 */
:root[data-theme="dark"] {
  --bg-primary: #000000;
  --bg-secondary: #1a1a1a;
  --bg-tertiary: #2a2a2a;
  --bg-surface: #1a1a1a;
  --text-primary: #ffffff;
  /* 与主文字拉开层次：原来与 primary 同为 #ffffff，导致暗色下所有次要文字
     （标签、空态提示、文件树名）都过亮、信息层级丢失。
     #a1a1aa 对 #1a1a1a 背景 6.65:1、对纯黑 8.19:1，均达标。 */
  --text-secondary: #a1a1aa;
  --border-color: #3a3a3a;
  --accent-color: #7c6aef;
}

/* 浅色主题 CSS 变量定义（默认） */
:root[data-theme="light"],
:root:not([data-theme="dark"]) {
  --bg-primary: #f8fafc;
  --bg-secondary: #ffffff;
  --bg-tertiary: #f1f5f9;
  --bg-surface: #ffffff;
  --text-primary: #1e293b;
  --text-secondary: #64748b;
  --border-color: #e2e8f0;
  --accent-color: #0ea5e9;
}

/* ========== 全局 ========== */
html[data-theme="dark"] body,
html[data-theme="dark"] #app,
html[data-theme="dark"] .app-container {
  background: var(--bg-primary) !important;
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .error-toast {
  background: #7f1d1d !important;
  color: #fecaca !important;
}

/* ========== 侧边栏 SessionList ========== */
html[data-theme="dark"] .session-list {
  background: var(--bg-secondary) !important;
  border-color: var(--border-color) !important;
}

html[data-theme="dark"] .session-header {
  border-color: var(--border-color) !important;
}

html[data-theme="dark"] .logo-text {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .action-btn {
  background: transparent !important;
  border: none !important;
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .action-btn:hover {
  background: var(--bg-tertiary) !important;
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .action-btn.active {
  background: rgba(124, 106, 239, 0.2) !important;
  color: var(--accent-color) !important;
}

html[data-theme="dark"] .session-item {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .session-item:hover {
  background: var(--bg-tertiary) !important;
}

html[data-theme="dark"] .session-item.active {
  background: rgba(124, 106, 239, 0.15) !important;
}

html[data-theme="dark"] .session-name {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .session-time {
  color: var(--text-secondary) !important;
}

html[data-theme="dark"] .menu-btn {
  color: var(--text-secondary) !important;
}

html[data-theme="dark"] .menu-btn:hover {
  background: var(--bg-tertiary) !important;
}

/* ========== 用户信息区域 ========== */
html[data-theme="dark"] .user-profile {
  background: transparent !important;
  border-color: var(--border-color) !important;
}

html[data-theme="dark"] .user-profile:hover {
  background: transparent !important;
}

html[data-theme="dark"] .user-name {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .user-status {
  display: none;
}

html[data-theme="dark"] .user-more-icon {
  color: var(--text-secondary) !important;
}

html[data-theme="dark"] .user-profile:hover .user-more-icon {
  color: var(--text-primary) !important;
}

/* ========== 用户下拉菜单 ========== */
html[data-theme="dark"] .user-dropdown {
  background: var(--bg-secondary) !important;
  border-color: var(--border-color) !important;
  box-shadow: 0 -4px 20px rgba(0, 0, 0, 0.4) !important;
}

html[data-theme="dark"] .user-dropdown-name {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .user-dropdown-email {
  color: var(--text-secondary) !important;
}

html[data-theme="dark"] .user-dropdown-divider {
  background: var(--border-color) !important;
}

html[data-theme="dark"] .user-dropdown-item {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .user-dropdown-item:hover {
  background: var(--bg-tertiary) !important;
}

html[data-theme="dark"] .logout-item {
  color: #f87171 !important;
}

/* ========== 聊天区域 ========== */
html[data-theme="dark"] .chat-container {
  background: var(--bg-primary) !important;
}

html[data-theme="dark"] .chat-main {
  background: var(--bg-primary) !important;
}

html[data-theme="dark"] .chat-header {
  background: var(--bg-secondary) !important;
  border-color: var(--border-color) !important;
}

html[data-theme="dark"] .chat-messages {
  background: var(--bg-primary) !important;
}

html[data-theme="dark"] .message-text {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .message.user .message-text {
  background: var(--bg-tertiary) !important;
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .session-created-time {
  color: var(--text-secondary) !important;
  background: #000000 !important;
}

html[data-theme="dark"] .message-error {
  background: rgba(127, 29, 29, 0.3) !important;
  border-color: #7f1d1d !important;
  color: #fca5a5 !important;
}

/* ========== 思考区域 ========== */
html[data-theme="dark"] .thinking-header {
  color: var(--text-secondary) !important;
}

html[data-theme="dark"] .thinking-header:hover {
  background: var(--bg-tertiary) !important;
}

html[data-theme="dark"] .thinking-icon {
  color: var(--text-secondary) !important;
}

html[data-theme="dark"] .thinking-title {
  color: var(--text-secondary) !important;
}

html[data-theme="dark"] .thinking-active .thinking-title {
  color: #818cf8 !important;
}

html[data-theme="dark"] .thinking-duration {
  color: var(--text-secondary) !important;
}

html[data-theme="dark"] .thinking-content {
  background: var(--bg-secondary) !important;
}

html[data-theme="dark"] .thinking-text {
  color: var(--text-secondary) !important;
}

/* ========== 工具调用 ========== */
html[data-theme="dark"] .tool-call-header {
  color: var(--text-secondary) !important;
}

html[data-theme="dark"] .tool-call-header:hover {
  background: var(--bg-tertiary) !important;
}

html[data-theme="dark"] .tool-call-body {
  background: transparent !important;
}

html[data-theme="dark"] .tool-section {
  background: var(--bg-secondary) !important;
}

html[data-theme="dark"] .tool-section-label {
  color: var(--text-secondary) !important;
}

html[data-theme="dark"] .tool-section-content {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .tool-section-content.error {
  background: transparent !important;
  color: #fca5a5 !important;
}

html[data-theme="dark"] .tool-name-badge {
  background: var(--bg-tertiary) !important;
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .tool-duration {
  color: var(--text-secondary) !important;
}

/* ========== 输入框 ========== */
html[data-theme="dark"] .chat-input-container {
  background: var(--bg-primary) !important;
}

html[data-theme="dark"] .input-box {
  background: var(--bg-secondary) !important;
  border-color: var(--border-color) !important;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.3) !important;
}

html[data-theme="dark"] .input-box:focus-within {
  border-color: var(--accent-color) !important;
  box-shadow: 0 2px 16px rgba(124, 106, 239, 0.2) !important;
}

html[data-theme="dark"] .input-area {
  background: var(--bg-secondary) !important;
}

html[data-theme="dark"] .input-area textarea {
  background: transparent !important;
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .input-area textarea::placeholder {
  color: var(--text-secondary) !important;
}

html[data-theme="dark"] .uploaded-files {
  background: var(--bg-tertiary) !important;
  border-color: var(--border-color) !important;
}

html[data-theme="dark"] .uploaded-file {
  background: var(--bg-secondary) !important;
  border-color: var(--border-color) !important;
}

html[data-theme="dark"] .uploaded-file .file-name {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .send-btn {
  background: var(--accent-color) !important;
}

html[data-theme="dark"] .send-btn:disabled {
  background: var(--bg-tertiary) !important;
}

html[data-theme="dark"] .upload-btn {
  background: var(--bg-tertiary) !important;
  border-color: var(--border-color) !important;
}

html[data-theme="dark"] .upload-btn svg {
  color: var(--text-secondary) !important;
}

html[data-theme="dark"] .upload-btn:hover:not(.disabled) {
  background: linear-gradient(135deg, #0ea5e9 0%, #0284c7 100%) !important;
  border-color: transparent !important;
}

html[data-theme="dark"] .upload-btn:hover:not(.disabled) svg {
  color: #ffffff !important;
}

html[data-theme="dark"] .tool-btn {
  color: var(--text-secondary) !important;
}

html[data-theme="dark"] .tool-btn:hover {
  background: var(--bg-tertiary) !important;
}

/* ========== 资产面板 ========== */
html[data-theme="dark"] .assets-panel {
  background: var(--bg-primary) !important;
}

html[data-theme="dark"] .assets-header {
  background: var(--bg-secondary) !important;
  border-color: var(--border-color) !important;
}

html[data-theme="dark"] .assets-header h2 {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .assets-content {
  background: var(--bg-primary) !important;
}

html[data-theme="dark"] .asset-item,
html[data-theme="dark"] .file-item {
  background: var(--bg-secondary) !important;
  border-color: var(--border-color) !important;
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .asset-item:hover,
html[data-theme="dark"] .file-item:hover {
  background: var(--bg-tertiary) !important;
}

html[data-theme="dark"] .asset-name,
html[data-theme="dark"] .file-name {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .asset-size,
html[data-theme="dark"] .file-size {
  color: var(--text-secondary) !important;
}

html[data-theme="dark"] .upload-btn {
  background: var(--accent-color) !important;
  border-color: var(--accent-color) !important;
}

html[data-theme="dark"] .close-btn {
  color: var(--text-secondary) !important;
}

html[data-theme="dark"] .close-btn:hover {
  background: var(--bg-tertiary) !important;
}

/* ========== 技能中心 ========== */
html[data-theme="dark"] .skill-center {
  background: var(--bg-primary) !important;
}

html[data-theme="dark"] .skill-center-content {
  background: var(--bg-primary) !important;
}

html[data-theme="dark"] .skill-card {
  background: var(--bg-surface) !important;
  border-color: var(--border-color) !important;
}

html[data-theme="dark"] .skill-card:hover {
  background: var(--bg-tertiary) !important;
  border-color: var(--accent-color) !important;
}

html[data-theme="dark"] .skill-card-inner {
  background: transparent !important;
}

html[data-theme="dark"] .skill-card-name {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .skill-card-desc {
  color: var(--text-secondary) !important;
}

/* ========== 工作区面板 ========== */
html[data-theme="dark"] .workspace-panel,
html[data-theme="dark"] .wp-header,
html[data-theme="dark"] .wp-content {
  background: var(--bg-secondary) !important;
  color: var(--text-primary) !important;
  border-color: var(--border-color) !important;
}

html[data-theme="dark"] .wp-header {
  border-color: var(--border-color) !important;
}

html[data-theme="dark"] .wp-title {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .wp-icon-btn {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .wp-icon-btn:hover:not(:disabled) {
  background: var(--bg-tertiary) !important;
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .wp-center-text {
  color: var(--text-secondary) !important;
}

html[data-theme="dark"] .wp-spinner {
  border-color: var(--border-color) !important;
  border-top-color: var(--accent-color) !important;
}

html[data-theme="dark"] .wp-retry-btn {
  background: var(--bg-tertiary) !important;
  border-color: var(--border-color) !important;
  color: var(--text-primary) !important;
}

/* 工作区文件树节点 */
html[data-theme="dark"] .tree-item {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .tree-item-name {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .tree-item:hover {
  background: var(--bg-tertiary) !important;
}

html[data-theme="dark"] .tree-item.active {
  background: rgba(124, 106, 239, 0.15) !important;
}

html[data-theme="dark"] .file-tree-item {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .file-tree-item:hover {
  background: var(--bg-tertiary) !important;
}

html[data-theme="dark"] .file-tree-item.active {
  background: rgba(124, 106, 239, 0.15) !important;
}

/* ========== 设置面板 ========== */
html[data-theme="dark"] .settings-modal,
html[data-theme="dark"] .settings-header,
html[data-theme="dark"] .settings-nav,
html[data-theme="dark"] .settings-content {
  background: var(--bg-secondary) !important;
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .settings-header h2,
html[data-theme="dark"] .panel-header h3 {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .nav-item {
  color: var(--text-secondary) !important;
}

html[data-theme="dark"] .nav-item:hover {
  background: var(--bg-tertiary) !important;
}

html[data-theme="dark"] .nav-item.active {
  background: rgba(124, 106, 239, 0.2) !important;
  color: var(--accent-color) !important;
}

html[data-theme="dark"] .theme-option {
  background: var(--bg-tertiary) !important;
  border-color: var(--border-color) !important;
}

html[data-theme="dark"] .theme-option.active {
  background: rgba(124, 106, 239, 0.2) !important;
  border-color: var(--accent-color) !important;
}

html[data-theme="dark"] .theme-option-name {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .theme-option-desc {
  color: var(--text-secondary) !important;
}

html[data-theme="dark"] .theme-check {
  color: var(--accent-color) !important;
}

/* ========== 悬浮按钮 ========== */
html[data-theme="dark"] .expand-sidebar-btn {
  background: var(--bg-secondary) !important;
  border-color: var(--border-color) !important;
}

html[data-theme="dark"] .expand-sidebar-btn svg {
  color: var(--text-secondary) !important;
}

/* ========== 通用元素 ========== */
html[data-theme="dark"] input,
html[data-theme="dark"] textarea,
html[data-theme="dark"] select {
  background: var(--bg-tertiary) !important;
  color: var(--text-primary) !important;
  border-color: var(--border-color) !important;
}

html[data-theme="dark"] input::placeholder,
html[data-theme="dark"] textarea::placeholder {
  color: var(--text-secondary) !important;
}

html[data-theme="dark"] button {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] h1,
html[data-theme="dark"] h2,
html[data-theme="dark"] h3,
html[data-theme="dark"] h4 {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] p,
html[data-theme="dark"] span,
html[data-theme="dark"] label {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] a {
  color: var(--accent-color) !important;
}

html[data-theme="dark"] code {
  background: var(--bg-tertiary) !important;
  color: var(--text-primary) !important;
}

html[data-theme="dark"] pre {
  background: var(--bg-secondary) !important;
  color: var(--text-primary) !important;
}

html[data-theme="dark"] hr {
  border-color: var(--border-color) !important;
}

html[data-theme="dark"] table {
  border-color: var(--border-color) !important;
}

html[data-theme="dark"] th {
  background: var(--bg-tertiary) !important;
  color: var(--text-primary) !important;
  border-color: var(--border-color) !important;
}

html[data-theme="dark"] td {
  border-color: var(--border-color) !important;
  color: var(--text-primary) !important;
}

html[data-theme="dark"] blockquote {
  border-left-color: var(--accent-color) !important;
  color: var(--text-secondary) !important;
}

/* ========== 滚动条 ========== */
html[data-theme="dark"] ::-webkit-scrollbar {
  width: 6px;
}

html[data-theme="dark"] ::-webkit-scrollbar-track {
  background: var(--bg-primary) !important;
}

html[data-theme="dark"] ::-webkit-scrollbar-thumb {
  background: var(--border-color) !important;
  border-radius: 3px;
}

html[data-theme="dark"] ::-webkit-scrollbar-thumb:hover {
  background: var(--text-secondary) !important;
}

/* ========== 补充：暗黑模式残留白底修复 ========== */
/* 输入区操作栏（上传附件所在区域） */
html[data-theme="dark"] .input-actions {
  background: var(--bg-secondary) !important;
}

html[data-theme="dark"] .send-btn {
  background: var(--bg-tertiary) !important;
}

html[data-theme="dark"] .send-btn.active {
  background: var(--accent-color) !important;
}

html[data-theme="dark"] .remove-file-btn {
  background: var(--bg-tertiary) !important;
}

html[data-theme="dark"] .remove-file-btn svg {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .progress-bar {
  background: var(--bg-tertiary) !important;
}

/* Token 用量弹窗 */
html[data-theme="dark"] .token-popup {
  background: var(--bg-secondary) !important;
  border-color: var(--border-color) !important;
}

html[data-theme="dark"] .token-popup-title,
html[data-theme="dark"] .token-popup-label,
html[data-theme="dark"] .token-popup-value,
html[data-theme="dark"] .token-popup-context-value {
  color: var(--text-primary) !important;
}

/* 语义色单独提升：上面的通用 .token-popup-value 规则会（靠 !important）把
   输入/输出/思考的配色抹成纯白，导致亮暗主题表现不一致。下面三条特异性更高，
   使暗色下仍保留色彩区分（均达到 5:1 以上）。 */
html[data-theme="dark"] .token-popup-value.input,
html[data-theme="dark"] .token-popup-value.duration-value {
  color: #818cf8 !important;
}

html[data-theme="dark"] .token-popup-value.output {
  color: #22d3ee !important;
}

html[data-theme="dark"] .token-popup-value.reasoning {
  color: #c084fc !important;
}

html[data-theme="dark"] .token-popup-divider,
html[data-theme="dark"] .token-popup-bar-inner {
  background: var(--bg-tertiary) !important;
}

/* Markdown 表格残留白底 */
html[data-theme="dark"] .message-text tr:nth-child(even) {
  background: rgba(255, 255, 255, 0.04) !important;
}

html[data-theme="dark"] .message-text tr:hover {
  background: rgba(255, 255, 255, 0.08) !important;
}

html[data-theme="dark"] .message-text td {
  background: transparent !important;
}

/* 用时徽标与旧版工具列表 */
html[data-theme="dark"] .tool-calls-block .tool-duration {
  background: transparent !important;
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .tool-call-item {
  background: var(--bg-secondary) !important;
}

html[data-theme="dark"] .tool-result {
  background: transparent !important;
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .tool-result.error {
  background: transparent !important;
  color: #fca5a5 !important;
}

/* 文件卡片 */
html[data-theme="dark"] .file-card {
  background: var(--bg-secondary) !important;
  border-color: var(--border-color) !important;
}

html[data-theme="dark"] .file-card .file-name {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .file-type {
  background: var(--bg-tertiary) !important;
  color: var(--text-primary) !important;
}

/* 预设问题卡片：样式全部走语义化令牌（--bg-secondary / --text-primary ...），
   深浅主题自动适配，这里无需额外覆盖。 */

/* 滚动按钮 */
html[data-theme="dark"] .scroll-btn {
  background: var(--bg-secondary) !important;
  border-color: var(--border-color) !important;
}

html[data-theme="dark"] .scroll-btn svg {
  color: var(--text-primary) !important;
}

/* 下拉菜单与弹窗 */
html[data-theme="dark"] .menu-dropdown {
  background: var(--bg-secondary) !important;
  border-color: var(--border-color) !important;
}

html[data-theme="dark"] .menu-item {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .modal-content {
  background: var(--bg-secondary) !important;
}

html[data-theme="dark"] .modal-content input {
  background: var(--bg-tertiary) !important;
  color: var(--text-primary) !important;
  border-color: var(--border-color) !important;
}

html[data-theme="dark"] .cancel-btn {
  background: var(--bg-tertiary) !important;
  border-color: var(--border-color) !important;
  color: var(--text-primary) !important;
}

/* 生成文件按钮 */
html[data-theme="dark"] .generated-files-btn {
  background: rgba(34, 197, 94, 0.15) !important;
  border-color: rgba(34, 197, 94, 0.4) !important;
  color: #4ade80 !important;
}

/* 等待与加载文字 */
html[data-theme="dark"] .waiting-text,
html[data-theme="dark"] .loading-text {
  color: var(--text-primary) !important;
}

/* 上下文环文字 */
html[data-theme="dark"] .context-ring-text {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .context-ring-bg {
  stroke: var(--bg-tertiary) !important;
}

/* ========== Task Plan (TodoListPanel) 暗黑模式 ========== */
html[data-theme="dark"] .todo-badge {
  background: var(--bg-secondary) !important;
  border-color: var(--border-color) !important;
  box-shadow: 2px 2px 12px rgba(0, 0, 0, 0.4) !important;
}

html[data-theme="dark"] .todo-badge-text {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .todo-panel {
  background: var(--bg-secondary) !important;
  border-color: var(--border-color) !important;
  box-shadow: 4px 0 20px rgba(0, 0, 0, 0.4) !important;
}

html[data-theme="dark"] .todo-header {
  border-color: var(--border-color) !important;
}

html[data-theme="dark"] .todo-title {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .todo-count {
  color: var(--text-primary) !important;
  background: var(--bg-tertiary) !important;
}

html[data-theme="dark"] .todo-close {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .todo-close:hover {
  color: var(--text-primary) !important;
  background: var(--bg-tertiary) !important;
}

html[data-theme="dark"] .todo-progress {
  background: var(--bg-tertiary) !important;
}

html[data-theme="dark"] .todo-item:hover {
  background: var(--bg-tertiary) !important;
}

html[data-theme="dark"] .todo-item.in_progress {
  background: rgba(124, 106, 239, 0.15) !important;
}

html[data-theme="dark"] .todo-content {
  color: var(--text-primary) !important;
}



html[data-theme="dark"] .todo-item.in_progress .todo-content {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .todo-pending-dot {
  border-color: var(--text-secondary) !important;
}

/* ========== 资产面板暗黑模式补充 ========== */
html[data-theme="dark"] .assets-panel,
html[data-theme="dark"] .assets-header,
html[data-theme="dark"] .assets-content {
  background: transparent !important;
}

html[data-theme="dark"] .assets-header h2 {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .upload-btn,
html[data-theme="dark"] .refresh-btn {
  background: var(--bg-tertiary) !important;
  border-color: var(--border-color) !important;
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .upload-btn:hover:not(.disabled),
html[data-theme="dark"] .refresh-btn:hover:not(:disabled) {
  background: var(--border-color) !important;
}

html[data-theme="dark"] .tab {
  background: transparent !important;
  border-color: var(--border-color) !important;
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .tab:hover {
  background: var(--bg-tertiary) !important;
}

html[data-theme="dark"] .tab.active {
  background: var(--accent-color) !important;
  border-color: var(--accent-color) !important;
  color: #fff !important;
}

html[data-theme="dark"] .tab-count {
  background: var(--bg-tertiary) !important;
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .tab.active .tab-count {
  background: rgba(255, 255, 255, 0.2) !important;
  color: #fff !important;
}

html[data-theme="dark"] .file-card {
  background: var(--bg-secondary) !important;
  border-color: var(--border-color) !important;
}

html[data-theme="dark"] .file-card:hover {
  border-color: var(--accent-color) !important;
}

html[data-theme="dark"] .file-card .file-name,
html[data-theme="dark"] .assets-panel .file-name {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .file-type-badge {
  background: transparent !important;
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .file-action:hover {
  background: var(--bg-tertiary) !important;
}

html[data-theme="dark"] .file-action svg {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .dropdown-menu {
  background: var(--bg-secondary) !important;
  border-color: var(--border-color) !important;
}

html[data-theme="dark"] .dropdown-item {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .dropdown-item:hover {
  background: var(--bg-tertiary) !important;
}

html[data-theme="dark"] .empty-state,
html[data-theme="dark"] .loading-state {
  color: var(--text-secondary) !important;
}

html[data-theme="dark"] .empty-state h3 {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .spinner {
  border-color: var(--border-color) !important;
  border-top-color: var(--accent-color) !important;
}

/* ========== 技能中心暗黑模式补充 ========== */
html[data-theme="dark"] .skill-center,
html[data-theme="dark"] .skill-center-content,
html[data-theme="dark"] .skill-center .tabs {
  background: transparent !important;
}

/* 仅主 Tab（.main-tab）：技能子 Tab 有自己的一套样式，不要被这里接管 */
html[data-theme="dark"] .skill-center .main-tab {
  background: transparent !important;
  color: var(--text-secondary) !important;
}

html[data-theme="dark"] .skill-center .main-tab:hover {
  background: var(--bg-tertiary) !important;
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .skill-center .main-tab.active {
  background: color-mix(in srgb, var(--accent-color) 18%, var(--bg-surface)) !important;
  border-color: color-mix(in srgb, var(--accent-color) 34%, transparent) !important;
  color: var(--accent-color) !important;
}

html[data-theme="dark"] .skill-card {
  background: var(--bg-secondary) !important;
  border-color: var(--border-color) !important;
}

html[data-theme="dark"] .skill-card:hover {
  border-color: var(--accent-color) !important;
}

html[data-theme="dark"] .skill-card-name {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .skill-card-desc {
  color: var(--text-secondary) !important;
}

html[data-theme="dark"] .add-icon-btn {
  background: var(--bg-tertiary) !important;
  border-color: var(--border-color) !important;
  color: var(--accent-color) !important;
}

html[data-theme="dark"] .add-icon-btn:hover:not(:disabled) {
  background: var(--border-color) !important;
}

html[data-theme="dark"] .retry-btn {
  background: var(--bg-tertiary) !important;
  border-color: var(--border-color) !important;
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .retry-btn:hover {
  background: var(--border-color) !important;
}

html[data-theme="dark"] .btn-spinner,
html[data-theme="dark"] .btn-spinner-sm {
  border-color: var(--border-color) !important;
  border-top-color: var(--accent-color) !important;
}

html[data-theme="dark"] .toast.success {
  background: rgba(34, 197, 94, 0.15) !important;
  color: #4ade80 !important;
  border-color: rgba(34, 197, 94, 0.4) !important;
}

html[data-theme="dark"] .toast.error {
  background: rgba(239, 68, 68, 0.15) !important;
  color: #f87171 !important;
  border-color: rgba(239, 68, 68, 0.4) !important;
}

/* ========== 工具执行状态文字 ========== */
html[data-theme="dark"] .tool-status-text.executing {
  background: transparent !important;
  color: var(--accent-color) !important;
}

/* ========== 用户消息卡片暗黑模式 ========== */
html[data-theme="dark"] .message.user .message-text {
  background: var(--bg-tertiary) !important;
  color: var(--text-primary) !important;
  border-color: var(--border-color) !important;
  box-shadow: none !important;
}

/* ========== 设置面板 MCP 区域 ========== */
html[data-theme="dark"] .mcp-card {
  background: var(--bg-tertiary) !important;
  border-color: var(--border-color) !important;
}

html[data-theme="dark"] .mcp-name {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .mcp-transport {
  background: rgba(14, 165, 233, 0.15) !important;
  color: #38bdf8 !important;
}

html[data-theme="dark"] .detail-label {
  color: var(--text-secondary) !important;
}

html[data-theme="dark"] .mcp-detail code {
  background: var(--bg-secondary) !important;
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .prompt-content {
  background: var(--bg-tertiary) !important;
  border-color: var(--border-color) !important;
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .empty-hint {
  color: var(--text-secondary) !important;
}

/* ========== 全局边框统一为灰色 ========== */
html[data-theme="dark"] .session-list,
html[data-theme="dark"] .session-header,
html[data-theme="dark"] .divider,
html[data-theme="dark"] .user-profile,
html[data-theme="dark"] .action-btn,
html[data-theme="dark"] .session-item,
html[data-theme="dark"] .menu-dropdown,
html[data-theme="dark"] .user-dropdown,
html[data-theme="dark"] .modal-content,
html[data-theme="dark"] .modal-content input,
html[data-theme="dark"] .chat-input-container .input-box {
  border-color: var(--border-color) !important;
}

/* ========== 模型下拉列表（Teleport 到 body，首页 / 输入框）========== */
html[data-theme="dark"] .model-dropdown-menu {
  background: var(--bg-secondary) !important;
  border-color: var(--border-color) !important;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5) !important;
}

html[data-theme="dark"] .model-dropdown-item:hover {
  background: var(--bg-tertiary) !important;
}

html[data-theme="dark"] .model-dropdown-item.active {
  background: color-mix(in srgb, var(--accent-color) 16%, transparent) !important;
}

html[data-theme="dark"] .model-dropdown-header {
  color: var(--text-secondary) !important;
  border-bottom-color: var(--border-color) !important;
}

html[data-theme="dark"] .model-item-name {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .model-item-check {
  color: var(--accent-color) !important;
}

html[data-theme="dark"] .model-item-badge {
  background: rgba(34, 197, 94, 0.15) !important;
  color: #4ade80 !important;
}

/* ========== 首页预设问题：卡片样式走语义化令牌，深浅主题自动适配 ========== */
/* ========== 聊天页（Chat.vue）深色适配 ========== */
html[data-theme="dark"] .chat-container {
  background: var(--bg-primary) !important;
}
html[data-theme="dark"] .chat-header {
  background: var(--bg-secondary) !important;
  border-bottom-color: var(--border-color) !important;
}
html[data-theme="dark"] .session-created-time {
  background: #000000 !important;
  color: var(--text-secondary) !important;
}
html[data-theme="dark"] .welcome-screen {
  color: var(--text-secondary) !important;
}
html[data-theme="dark"] .welcome-screen h2 {
  color: var(--text-primary) !important;
}
html[data-theme="dark"] .scroll-btn {
  background: var(--bg-secondary) !important;
  color: var(--text-secondary) !important;
}
html[data-theme="dark"] .scroll-btn:hover {
  background: var(--bg-tertiary) !important;
}
html[data-theme="dark"] .scroll-btn svg {
  color: var(--text-secondary) !important;
}
html[data-theme="dark"] .generated-files-header-btn {
  background: rgba(34, 197, 94, 0.15) !important;
  border-color: rgba(34, 197, 94, 0.4) !important;
  color: #4ade80 !important;
}
</style>
