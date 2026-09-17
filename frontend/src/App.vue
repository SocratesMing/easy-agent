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
        :style="{ width: sidebarWidth + 'px', flexShrink: 0 }"
        :sessions="sessions"
        :currentSessionId="currentSessionId"
        :streamingSessionIds="streamingSessions"
        :username="userProfile.username"
        :organizationId="userProfile.organization_id"
        :email="userProfile.email"
        :showAssets="showAssets"
        :showKnowledge="showKnowledge"
        @show-knowledge="handleShowKnowledge"
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
        @show-user-management="handleShowUserManagement"
        @logout="handleLogout"
      />
      <SidebarResizeHandle v-if="!isSidebarCollapsed" v-model="sidebarWidth" />
      
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
      
      <SettingsPanel
        v-if="showSettingsPanel"
        @close="showSettingsPanel = false"
      />

      <UserManagementPanel
        v-if="showUserManagementPanel"
        @close="showUserManagementPanel = false"
      />
      
      <KnowledgeWorkbench
        v-if="showKnowledge"
        @close="showKnowledge = false"
        @managePersonnel="handleShowUserManagement"
      />

      <Chat
        v-else-if="!showUserManagementPanel && !showAssets && !showSkillCenter && !showScheduledTasks"
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
        @update:selectedModel="selectedModel = $event"
        @toggle-workspace="isWorkspaceCollapsed = false"
        @send-message="handleSendMessage"
        @create-session="ensureCurrentSession"
        @remove-file="handleRemoveFile"
        @stop="handleStop"
        @retry="handleRetry"
        @approve="handleToolApproval('approve')"
        @reject="handleToolApproval('reject')"
      />

      <div v-if="currentSessionId && !showKnowledge && !showUserManagementPanel && !showAssets && !showSkillCenter" class="workspace-area">
        <WorkspacePanel
          :username="userProfile.username"
          :currentSessionId="currentSessionId"
          :isStreaming="isStreaming"
          :visible="!isWorkspaceCollapsed"
          @toggle="isWorkspaceCollapsed = !isWorkspaceCollapsed"
        />
      </div>

      <div v-if="error" class="error-toast">
        {{ error }}
        <button @click="error = null">×</button>
      </div>
    </template>
  </div>
</template>

<script>
import Vue from 'vue'
import SessionList from './components/SessionList.vue'
import Chat from './components/Chat.vue'
import AssetsPanel from './components/AssetsPanel.vue'
import SkillCenter from './components/SkillCenter.vue'
import ScheduledTasksPanel from './components/ScheduledTasksPanel.vue'
import UserManagementPanel from './features/personnel/PersonnelManagement.vue'
import KnowledgeWorkbench from './features/knowledge/KnowledgeWorkbench.vue'
import SidebarResizeHandle from './features/knowledge/SidebarResizeHandle.vue'
import Welcome from './components/Welcome.vue'
import WorkspacePanel from './components/WorkspacePanel.vue'
import SettingsPanel from './components/SettingsPanel.vue'
import { createSession, listSessions, getChatHistory, deleteSession, sendMessage, resumeStream, renameSession, togglePinSession, getStreamStatus, attachStream } from './api/chat.js'
import { uploadFile, deleteFile, getUserProfile, getSessionGeneratedFiles } from './api/files.js'
import { logout as apiLogout, notifyLogout, getStoredToken, getStoredUsername, AUTH_EXPIRED_EVENT, authFetch, passwordlessLogin } from './api/auth.js'
import { getModels as fetchModels } from './api/settings.js'
import { isRestorableUserProfile } from './utils/userProfile.js'

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

// MCP 工具结果解析：模块级函数，供正常流(handleSendMessage)与 HITL 恢复流(handleToolApproval)共用。
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
    SidebarResizeHandle,
    KnowledgeWorkbench,
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
  data() {
    return {
      sessions: [],
      currentSessionId: null,
      currentSessionHasFiles: false,
      // bootstrap（认证 + 会话列表 + 首个会话历史）完成前不写入，避免用初始 null 覆盖已存值
      hasBootstrapped: false,
      // 模型选择：从配置加载可选列表，默认选 active model
      availableModels: [],
      selectedModel: null,
      welcomeTitle: APP_WELCOME_TITLE,
      // 「未授权」提示态（登录页关闭且免密登录未成功时显示）
      authBlocked: false,
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
      sessionUsage: { input_tokens: 0, output_tokens: 0, reasoning_tokens: 0, context_length: null, auto_compress_tokens: null, context_tokens: 0 },
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
      showKnowledge: false,
      sidebarWidth: 280,
      showSkillCenter: false,
      showScheduledTasks: false,
      showSettingsPanel: false,
      showUserManagementPanel: false,
      showWelcome: false,
      // 免密登录开关（模板用：未授权提示的文案按开关区分）
      passwordlessEnabled: PASSWORDLESS_LOGIN_ENABLED,
      // 首屏引导中：认证 + 会话列表 + 首次历史加载完成前不渲染聊天区，
      // 否则会先闪出空会话首页、会话时间下方那条分隔线也会闪一下。
      isBootstrapping: true,
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
    // HITL 历史恢复：切换/重载会话后重建待审批状态
    loadedSessionId() {
      this.reconstructPendingApproval()
    }
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
    },
    toggleSidebar() {
      this.isSidebarCollapsed = !this.isSidebarCollapsed
    },
    handleShowAssets() {
      this.showKnowledge = false
      this.showAssets = !this.showAssets
      this.showSkillCenter = false
      this.showScheduledTasks = false
    },
    handleShowSkillCenter() {
      this.showKnowledge = false
      this.showSkillCenter = !this.showSkillCenter
      this.showAssets = false
      this.showScheduledTasks = false
    },
    handleShowScheduledTasks() {
      this.showKnowledge = false
      this.showScheduledTasks = !this.showScheduledTasks
      this.showAssets = false
      this.showSkillCenter = false
    },
    handleShowKnowledge() {
      this.showKnowledge = !this.showKnowledge
      this.showAssets = false
      this.showSkillCenter = false
      this.showScheduledTasks = false
      this.showSettingsPanel = false
      this.showUserManagementPanel = false
    },
    handleShowUserManagement() {
      this.showUserManagementPanel = true
      this.showKnowledge = false
      this.showAssets = false
      this.showSkillCenter = false
      this.showScheduledTasks = false
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

      let initialSessionId = null
      if (stored === NEW_SESSION_MARK) {
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
      this.showKnowledge = false
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
        if (!isRestorableUserProfile(profile)) {
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
        window.history.replaceState({}, '', window.location.pathname)
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
    async loadSessions() {
      try {
        const data = await listSessions(this.userProfile.username || null)
        this.sessions = Array.isArray(data) ? data : (data.sessions || [])
      } catch (e) {
        console.error('加载会话列表失败:', e)
        this.error = '加载会话列表失败'
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
      this.showKnowledge = false
      this.showUserManagementPanel = false
      this.saveCurrentSessionState()

      this.showAssets = false
      this.showSkillCenter = false
      this.showScheduledTasks = false
      this.showSettingsPanel = false
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
      this.showKnowledge = false
      this.showUserManagementPanel = false
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
        this.sessions = this.sessions.filter(s => s.session_id !== sessionId)

        // 清除会话缓存
        delete this.sessionStates[sessionId]

        if (this.currentSessionId === sessionId) {
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
          }
        } else if (eventType === 'knowledge_evidence') {
          ensureMessage()
          const idx = findIdx()
          if (idx !== -1) setReactive(this.messages, idx, {
            ...this.messages[idx],
            knowledge_evidence: Array.isArray(data.evidence) ? data.evidence : [],
            knowledge_warnings: Array.isArray(data.warnings) ? data.warnings : [],
          })
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

        await sendMessage(this.currentSessionId, message, onChunk, abortSignal, enableDeepThink, files, this.selectedModel)

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
          // 审批恢复流结束：以服务端历史为准回填，保证完整渲染
          await this.syncSessionFromServer(sid)
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

html[data-theme="dark"] .skill-center-header {
  background: var(--bg-secondary) !important;
  border-color: var(--border-color) !important;
}

html[data-theme="dark"] .skill-center-header h2 {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .skill-center-content {
  background: var(--bg-primary) !important;
}

html[data-theme="dark"] .skill-card {
  background: var(--bg-secondary) !important;
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

html[data-theme="dark"] .skill-card-icon {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .add-skill-btn {
  color: var(--accent-color) !important;
}

html[data-theme="dark"] .add-skill-btn:hover {
  background: rgba(124, 106, 239, 0.15) !important;
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

/* 预设问题卡片堆叠 */
html[data-theme="dark"] .preset-card {
  background: var(--bg-secondary) !important;
  border-color: var(--border-color) !important;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.4) !important;
}

html[data-theme="dark"] .preset-card.is-front {
  border-color: var(--accent-color, #0ea5e9) !important;
  box-shadow: 0 10px 30px rgba(14, 165, 233, 0.25) !important;
}

html[data-theme="dark"] .preset-card-text {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .preset-card-index,
html[data-theme="dark"] .preset-card-foot {
  color: var(--text-muted) !important;
}

html[data-theme="dark"] .preset-nav-btn {
  background: var(--bg-secondary) !important;
  border-color: var(--border-color) !important;
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .preset-dot {
  background: var(--border-color) !important;
}

html[data-theme="dark"] .preset-dot.active {
  background: var(--accent-color, #0ea5e9) !important;
}

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
html[data-theme="dark"] .skill-center-header,
html[data-theme="dark"] .skill-center-content,
html[data-theme="dark"] .skill-center .tabs {
  background: transparent !important;
}

html[data-theme="dark"] .skill-center-header h2 {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .skill-center .tab {
  background: transparent !important;
  border-color: var(--border-color) !important;
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .skill-center .tab:hover {
  background: var(--bg-tertiary) !important;
}

html[data-theme="dark"] .skill-center .tab.active {
  background: var(--accent-color) !important;
  border-color: var(--accent-color) !important;
  color: #fff !important;
}

html[data-theme="dark"] .skill-center .tab-count {
  background: var(--bg-tertiary) !important;
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .skill-center .tab.active .tab-count {
  background: rgba(255, 255, 255, 0.2) !important;
  color: #fff !important;
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

html[data-theme="dark"] .add-icon-btn.added {
  background: var(--bg-tertiary) !important;
  color: var(--text-secondary) !important;
}

html[data-theme="dark"] .remove-icon-btn:hover:not(:disabled) {
  background: rgba(239, 68, 68, 0.15) !important;
  border-color: rgba(239, 68, 68, 0.4) !important;
}

html[data-theme="dark"] .popover-card {
  background: var(--bg-secondary) !important;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5), 0 0 0 1px var(--border-color) !important;
}

html[data-theme="dark"] .popover-name {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .popover-category {
  color: var(--text-secondary) !important;
}

html[data-theme="dark"] .popover-desc {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .popover-close {
  color: var(--text-secondary) !important;
}

html[data-theme="dark"] .popover-close:hover {
  background: var(--bg-tertiary) !important;
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .popover-btn {
  background: var(--bg-tertiary) !important;
  border-color: var(--accent-color) !important;
  color: var(--accent-color) !important;
}

html[data-theme="dark"] .popover-btn:hover:not(:disabled) {
  background: var(--border-color) !important;
}

html[data-theme="dark"] .popover-btn.added {
  background: var(--bg-tertiary) !important;
  border-color: var(--border-color) !important;
  color: var(--text-secondary) !important;
}

html[data-theme="dark"] .popover-btn.remove {
  border-color: rgba(239, 68, 68, 0.5) !important;
  color: #f87171 !important;
}

html[data-theme="dark"] .popover-btn.remove:hover:not(:disabled) {
  background: rgba(239, 68, 68, 0.15) !important;
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

/* ========== 首页预设问题（分类标签 + 悬浮面板 + 问题卡片）========== */
html[data-theme="dark"] .preset-category-tab {
  background: var(--bg-tertiary) !important;
  border-color: var(--border-color) !important;
  color: var(--text-secondary) !important;
}

html[data-theme="dark"] .preset-category-tab:hover {
  border-color: var(--accent-color) !important;
  color: var(--accent-color) !important;
}

html[data-theme="dark"] .preset-category-tab.active {
  background: color-mix(in srgb, var(--accent-color) 22%, var(--bg-tertiary)) !important;
  border-color: var(--accent-color) !important;
  color: var(--accent-color) !important;
}

html[data-theme="dark"] .preset-category-panel {
  background: var(--bg-secondary) !important;
  border-color: var(--border-color) !important;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5) !important;
}

html[data-theme="dark"] .preset-chip {
  background: var(--bg-tertiary) !important;
  border-color: var(--border-color) !important;
  color: var(--text-secondary) !important;
}

html[data-theme="dark"] .preset-chip:hover {
  border-color: var(--accent-color) !important;
  color: var(--accent-color) !important;
  transform: translateY(-1px);
}

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
html[data-theme="dark"] .preset-card {
  background: var(--bg-secondary) !important;
  border-color: var(--border-color) !important;
}
html[data-theme="dark"] .preset-card-text {
  color: var(--text-primary) !important;
}
html[data-theme="dark"] .preset-card-foot {
  color: var(--text-secondary) !important;
}
html[data-theme="dark"] .preset-nav-btn {
  background: var(--bg-secondary) !important;
  border-color: var(--border-color) !important;
  color: var(--text-secondary) !important;
}
html[data-theme="dark"] .preset-nav-btn:hover {
  background: var(--bg-tertiary) !important;
  border-color: var(--accent-color) !important;
  color: var(--accent-color) !important;
}
html[data-theme="dark"] .preset-dot {
  background: var(--border-color) !important;
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
