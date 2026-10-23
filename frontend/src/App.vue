<template>
  <div class="app-container">
    <Welcome 
      v-if="showWelcome" 
      @completed="handleWelcomeCompleted" 
    />
    
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
        :showKnowledge="showKnowledge"
        @createSession="handleCreateSession"
        @selectSession="handleSelectSession"
        @deleteSession="handleDeleteSession"
        @renameSession="handleRenameSession"
        @togglePin="handleTogglePin"
        @toggleSidebar="toggleSidebar"
        @showAssets="handleShowAssets"
        @showKnowledge="handleShowKnowledge"
        @showSkillCenter="handleShowSkillCenter"
        @showScheduledTasks="handleShowScheduledTasks"
        @showSettings="showSettingsPanel = true"
        @showUserManagement="showUserManagementPanel = true"
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
        @use="handleUseExpert"
        @create-skill="handleSkillCreate"
        @create-expert="handleExpertCreate"
        @edit-expert="handleExpertEdit"
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

      <KnowledgeWorkbench
        v-if="showKnowledge"
        @close="showKnowledge = false"
        @manage-personnel="showUserManagementPanel = true"
        @chat-session-prepared="loadSessions"
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
        @expertPromptApplied="expertPrompt = ''"
        @update:selectedModel="selectedModel = $event"
        @toggle-workspace="isWorkspaceCollapsed = false"
        @sendMessage="handleSendMessage"
        @createSession="ensureCurrentSession"
        @removeFile="handleRemoveFile"
        @stop="handleStop"
        @retry="handleRetry"
        @approve="handleToolApproval('approve', $event)"
        @reject="handleToolApproval('reject', $event)"
        @edit="handleToolApproval('edit', $event)"
        @clearExpert="selectedExpert = null"
        @selectExpert="handleUseExpert"
        @openPanel="openSkillCenterPanel"
        @openKnowledge="openKnowledgeWorkbench"
      />

      <div v-if="currentSessionId && !showKnowledge && !showAssets && !showSkillCenter" class="workspace-area">
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

<script setup>
import { API_BASE_URL, APP_WELCOME_TITLE, appRuntime } from './config.js'
import { ref, computed, onMounted, watch, defineAsyncComponent } from 'vue'
import SessionList from './features/chat/components/SessionList.vue'
import Chat from './features/chat/components/Chat.vue'
import AssetsPanel from './features/chat/components/AssetsPanel.vue'
const SkillCenter = defineAsyncComponent(() => import('./features/skills/components/SkillCenter.vue'))
const ScheduledTasksPanel = defineAsyncComponent(() => import('./features/tasks/components/ScheduledTasksPanel.vue'))
const KnowledgeWorkbench = defineAsyncComponent(() => import('./features/knowledge/KnowledgeWorkbench.vue'))
const UserManagementPanel = defineAsyncComponent(() => import('./features/personnel/components/UserManagementPanel.vue'))
import Welcome from './features/chat/components/Welcome.vue'
import WorkspacePanel from './features/workspace/components/WorkspacePanel.vue'
const SettingsPanel = defineAsyncComponent(() => import('./features/settings/components/SettingsPanel.vue'))
import { createSession, listSessions, getChatHistory, deleteSession, sendMessage, resumeStream, renameSession, togglePinSession, getStreamStatus, attachStream } from './api/chat.js'
import { applyApprovalRequests, buildResumeDecisions, markApprovalDecision, unmatchedApprovalRequests } from './utils/hitlApproval.js'
import { createStreamChunkHandler } from './features/chat/composables/useStreamChunks.js'
import { uploadFile, deleteFile, getUserProfile, getSessionGeneratedFiles } from './api/files.js'
import { logout as apiLogout, notifyLogout, getStoredToken, getStoredUsername, AUTH_EXPIRED_EVENT, authFetch, passwordlessLogin } from './api/auth.js'
import { getModels as fetchModels } from './api/settings.js'
import { streamExportRun } from './api/expert.js'

const sessions = ref([])
const currentSessionId = ref(null)
const currentSessionHasFiles = ref(false)

// ── 当前会话视图持久化 ────────────────────────────────────────────
// 刷新后恢复到用户刷新前所在的会话，而不是永远跳到列表第一个。
// 用 '__new__' 记录"新会话首页"（currentSessionId 为 null）这一状态。
const ACTIVE_SESSION_KEY = 'easy_agent_active_session'
const NEW_SESSION_MARK = '__new__'
// bootstrap（认证 + 会话列表 + 首个会话历史）完成前不写入，避免用初始 null 覆盖已存值
const hasBootstrapped = ref(false)
watch(currentSessionId, (sid) => {
  if (!hasBootstrapped.value) return
  try {
    localStorage.setItem(ACTIVE_SESSION_KEY, sid || NEW_SESSION_MARK)
  } catch (_) { /* localStorage 不可用时忽略 */ }
})
function clearActiveSession() {
  try {
    localStorage.removeItem(ACTIVE_SESSION_KEY)
  } catch (_) { /* ignore */ }
}

// 模型选择：从配置加载可选列表，默认选 active model
const availableModels = ref([])
const selectedModel = ref(null)
const welcomeTitle = ref(APP_WELCOME_TITLE)

async function loadModels() {
  try {
    const data = await fetchModels()
    availableModels.value = data.models || []
    // 默认选 active model（仅当当前未选择时）
    if (!selectedModel.value && data.active_model) {
      selectedModel.value = data.active_model
    }
    console.log(
      `[${new Date().toISOString()}] [模型列表] 加载成功 | 可选: ${availableModels.value.map(m => m.name).join(',')} | 当前: ${selectedModel.value}`
    )
  } catch (e) {
    console.error('加载模型列表失败:', e)
  }
}
const currentSessionCreatedAt = computed(() => {
  if (!currentSessionId.value) return null
  const session = sessions.value.find(s => s.session_id === currentSessionId.value)
  return session?.created_at || null
})
let filesCheckTimer = null

// 会话状态缓存：为每个会话保存独立的流式状态
const sessionStates = ref({})
// 当前界面上展示的数据（messages/sessionUsage 等）实际所属的会话 ID。
// 与 currentSessionId 的区别：切换会话后、历史数据加载完成前，currentSessionId 已指向新会话，
// 但界面数据仍属于旧会话。保存缓存必须以 loadedSessionId 为 key，否则会把旧数据/清零的用量
// 错误地存到新会话名下，导致来回切换时 token 用量显示为 0。
const loadedSessionId = ref(null)

async function refreshSessionFiles(sessionId = null, delayMs = 0) {
  const targetId = sessionId || currentSessionId.value
  if (!targetId) {
    currentSessionHasFiles.value = false
    return false
  }
  if (filesCheckTimer) {
    clearTimeout(filesCheckTimer)
    filesCheckTimer = null
  }
  const doCheck = async () => {
    try {
      const files = await getSessionGeneratedFiles(targetId)
      const hasFiles = Array.isArray(files) && files.length > 0
      if (currentSessionId.value === targetId) {
        currentSessionHasFiles.value = hasFiles
      }
      console.log('[Files] Session', targetId, 'has files:', hasFiles, 'count:', files?.length)
      return currentSessionId.value === targetId && hasFiles
    } catch (e) {
      console.error('[Files] 检查会话文件失败:', e)
      if (currentSessionId.value === targetId) {
        currentSessionHasFiles.value = false
      }
      return false
    }
  }
  if (delayMs > 0) {
    return await new Promise((resolve) => {
      filesCheckTimer = setTimeout(() => {
        doCheck().then(resolve).catch(resolve)
      }, delayMs)
    })
  } else {
    return await doCheck()
  }
}

async function expandWorkspaceAfterSessionCompletion(sessionId, delayMs = 500) {
  if (!sessionId) return
  if (pendingApproval.value) return
  const hasFiles = await refreshSessionFiles(sessionId, delayMs)
  if (hasFiles && currentSessionId.value === sessionId && !pendingApproval.value) {
    isWorkspaceCollapsed.value = false
  }
}
const messages = ref([])
// 多个会话可同时流式：记录所有正在流式输出的会话 id（会话列表逐个显示"进行中"徽标）
const streamingSessions = ref([])
// 最近开始流式的会话（页面刷新后据此重新挂载）
const lastStreamingSession = ref(null)
// 输入框状态：当前展示的会话正在流式时才显示停止按钮与 token 用量
const isStreaming = computed(() => isSessionStreaming(currentSessionId.value))
// 当前正在流式输出的 assistant 消息 id（组件级，供 handleStop 等跨函数使用；
// 之前误引用 handleSendMessage 的局部变量 assistantMsgId 导致停止按钮抛 ReferenceError）
const streamingAssistantId = ref(null)
const error = ref(null)
const currentAbortController = ref(null)
// HITL: 审批待处理状态，存储 { threadId, assistantMsgId }
const pendingApproval = ref(null)
const sessionUsage = ref({ input_tokens: 0, output_tokens: 0, reasoning_tokens: 0, context_length: null, auto_compress_tokens: null, context_tokens: 0 })
// 当前会话累计耗时（秒），每次 AI 回复完成后累加
const sessionDuration = ref(0)
// 当前会话累计迭代次数（step 数），每次 AI 回复完成后累加
const iterationCount = ref(0)
const currentTodos = ref([])
const presetQuestions = ref([])

function markStreaming(sid) {
  if (!sid) return
  if (!streamingSessions.value.includes(sid)) {
    streamingSessions.value.push(sid)
  }
  lastStreamingSession.value = sid
}

function unmarkStreaming(sid) {
  if (!sid) return
  const idx = streamingSessions.value.indexOf(sid)
  if (idx !== -1) streamingSessions.value.splice(idx, 1)
}

function isSessionStreaming(sid) {
  return !!sid && streamingSessions.value.includes(sid)
}

// ── 流式状态持久化（sessionStorage）─────────────────────────────────────
// 页面刷新后据此恢复「输入框状态不变、页面持续流式返回」：
// - 恢复输入框草稿（ChatInput 自行持久化）
// - 若刷新前正在流式，则重新挂载到后端进行中的流式任务继续接收事件
const STREAM_STATE_KEY = 'easy_agent_stream_state'

function saveStreamState() {
  const ids = streamingSessions.value.filter(Boolean)
  if (ids.length > 0) {
    sessionStorage.setItem(STREAM_STATE_KEY, JSON.stringify({
      streamingIds: ids,
      lastActive: lastStreamingSession.value || ids[ids.length - 1],
      streaming: true,
      at: Date.now(),
    }))
  } else {
    sessionStorage.removeItem(STREAM_STATE_KEY)
  }
}

watch([streamingSessions, lastStreamingSession], saveStreamState)

// 本页已建立实时挂载的会话及模式（避免重复挂载/重复回放）：
// { [sessionId]: 'displayed' | 'background' }
const attachedStreamingSessions = ref({})

// 挂载到进行中的流式任务：回放该流已产生的事件并持续接收后续事件。
// displayed=true：重建界面消息并持续更新（当前查看的会话）；
// displayed=false：仅后台同步 token 用量、流结束时移除"进行中"标记，不触碰界面。
async function attachToStreamingSession(sessionId, opts = {}) {
  const { switchTo = false, displayed = true } = opts
  if (!sessionId) return
  const mode = displayed ? 'displayed' : 'background'
  // 已展示挂载的会话无需重复挂载；后台挂载可被升级为展示挂载
  if (attachedStreamingSessions.value[sessionId] === 'displayed') return
  attachedStreamingSessions.value[sessionId] = mode

  if (switchTo) {
    currentSessionId.value = sessionId
  }
  // 展示挂载（含切换懒挂载）时，界面消息/用量归属于该会话；
  // 否则 loadedSessionId 停留在旧会话，缓存写入错位，来回切换会显示空会话主页。
  if (displayed) {
    loadedSessionId.value = sessionId
  }
  markStreaming(sessionId)

  if (displayed) {
    sessionLoading.value = true
    try {
      const history = await getChatHistory(sessionId)
      if (currentSessionId.value !== sessionId) {
        // 加载期间用户已切换到其他会话：放弃本次挂载，避免覆盖当前展示
        if (attachedStreamingSessions.value[sessionId] === mode) {
          delete attachedStreamingSessions.value[sessionId]
        }
        return
      }
      messages.value = history.messages || []
      currentTodos.value = history.todos || []
      if (history.usage) {
        sessionUsage.value.input_tokens = history.usage.input_tokens || 0
        sessionUsage.value.output_tokens = history.usage.output_tokens || 0
        sessionUsage.value.reasoning_tokens = history.usage.reasoning_tokens || 0
        sessionUsage.value.context_tokens = history.usage.context_tokens || 0
        sessionDuration.value = history.usage.elapsed_time || 0
        iterationCount.value = history.usage.step_count || 0
      }
      if (history.context_length) {
        sessionUsage.value.context_length = history.context_length
      }
    } catch (e) {
      console.error('加载聊天历史失败:', e)
    } finally {
      sessionLoading.value = false
    }
  }

  // 正在生成中的回复应是消息列表的最后一条：最后一条是 assistant 则原位替换为
  // 空占位由回放重建（避免重复）；最后一条仍是用户消息则末尾追加占位。
  // 此前按「倒数第一条 assistant」查找，会把上一轮历史 assistant 误替换，导致
  // 正文渲染在最后一次用户输入的上方。
  const attachId = `assistant-attach-${Date.now()}-${String(sessionId).slice(-5)}`
  if (displayed) {
    const lastMsg = messages.value[messages.value.length - 1]
    const lastAssistantIdx = lastMsg && lastMsg.role === 'assistant'
      ? messages.value.length - 1
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
      messages.value.push(placeholder)
    } else {
      messages.value[lastAssistantIdx] = placeholder
    }
    streamingAssistantId.value = attachId
  }

  const { onChunk } = createStreamChunkHandler(messages, {
    isResume: true,
    attachMode: true,
    get assistantMsgId() { return attachId },
    set assistantMsgId(v) {},
    streamSessionId: sessionId,
    runInSession,
    state: buildStreamState(),
    preStreamUsage: { ...sessionUsage.value },
    preStreamIterationCount: iterationCount.value,
    preStreamDuration: sessionDuration.value,
    initialBlockOrder: 0,
    onApprovalRequired: (data) => {
      if (!displayed) return
      pendingApproval.value = {
        threadId: data.thread_id,
        assistantMsgId: attachId,
      }
      if (attachId && data.action_requests) {
        const idx = messages.value.findIndex(m => m.id === attachId)
        if (idx !== -1) {
          messages.value[idx].pending_approval = { thread_id: data.thread_id, action_requests: data.action_requests }
          if (Array.isArray(data.blocks) && data.blocks.length > 0) {
            messages.value[idx].blocks = data.blocks.map(b => ({ ...b }))
          }
          // 挂不上工具卡片的（子代理内部审批）由 ChatMessage 的独立审批面板展示
          applyApprovalRequests(messages.value[idx].blocks, data.action_requests)
          messages.value[idx] = { ...messages.value[idx], blocks: [...messages.value[idx].blocks] }
        }
      }
    },
  })

  const controller = new AbortController()
  if (displayed) currentAbortController.value = controller
  if (!displayed && !sessionStates.value[sessionId]) {
    // 后台挂载：确保会话缓存存在，供 token 用量回放写入
    sessionStates.value[sessionId] = {
      messages: [],
      isStreaming: true,
      sessionUsage: {
        input_tokens: 0,
        output_tokens: 0,
        context_length: sessionUsage.value.context_length,
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
    if (displayed) {
      await expandWorkspaceAfterSessionCompletion(sessionId)
    }
  } catch (e) {
    if (e.name !== 'AbortError') {
      console.error('挂载流式输出失败:', e)
      if (displayed) error.value = e.message || '挂载流式输出失败'
    }
  } finally {
    if (attachedStreamingSessions.value[sessionId] === mode) {
      delete attachedStreamingSessions.value[sessionId]
    }
    if (!displayed || !pendingApproval.value) {
      if (displayed) currentAbortController.value = null
      unmarkStreaming(sessionId)
      if (sessionStates.value[sessionId]) {
        sessionStates.value[sessionId].isStreaming = false
        sessionStates.value[sessionId].abortController = null
      }
      // 列表"进行中"标记此时移除（显示已完成）：以服务端历史为准回填完整内容
      await syncSessionFromServer(sessionId)
      if (displayed) {
        // 兜底：挂载流结束时确保占位消息不再标记 loading，避免页面残留"执行中"
        let idx = messages.value.findIndex(m => m.id === attachId)
        if (idx === -1) {
          for (let i = messages.value.length - 1; i >= 0; i--) {
            if (messages.value[i].role === 'assistant' && messages.value[i].loading) { idx = i; break }
          }
        }
        if (idx !== -1 && messages.value[idx].loading) {
          messages.value[idx] = { ...messages.value[idx], loading: false }
        }
        streamingAssistantId.value = null
        saveCurrentSessionState()
      }
      await loadSessions()
    }
  }
}

// 刷新后恢复全部进行中的流式任务：逐个查询后端状态，活跃的恢复"进行中"标记；
// 最近活跃的会话直接重挂载，其余会话在切换到它时懒挂载。
async function tryAttachLiveStream() {
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
  for (const sid of active) markStreaming(sid)

  const attachTarget = saved.lastActive && active.includes(saved.lastActive)
    ? saved.lastActive
    : active[0]
  if (attachTarget) {
    attachToStreamingSession(attachTarget, { switchTo: true, displayed: true })
  }
  // 其余仍活跃的会话后台挂载：同步 token 用量、流结束时自动移除"进行中"标记
  for (const sid of active) {
    if (sid === attachTarget) continue
    attachToStreamingSession(sid, { switchTo: false, displayed: false })
  }
  // 清理一次性恢复标记；仍活跃的流会通过 watch 重新写入持久化
  saveStreamState()
}

// 保存当前会话状态到缓存（以界面数据实际所属的会话为 key，避免异步加载期间保存错位）
function saveCurrentSessionState() {
  if (!loadedSessionId.value) return
  sessionStates.value[loadedSessionId.value] = {
    messages: JSON.parse(JSON.stringify(messages.value)),
    isStreaming: isStreaming.value,
    sessionUsage: { ...sessionUsage.value },
    sessionDuration: sessionDuration.value,
    iterationCount: iterationCount.value,
    abortController: currentAbortController.value,
    todos: [...currentTodos.value]
  }
}

// 从缓存恢复会话状态
function restoreSessionState(sessionId) {
  const state = sessionStates.value[sessionId]
  if (state) {
    messages.value = state.messages
    // 输入框是否显示停止按钮由 isStreaming（当前会话是否在流式会话集合中）决定
    sessionUsage.value = { ...state.sessionUsage }
    sessionDuration.value = state.sessionDuration || 0
    iterationCount.value = state.iterationCount || 0
    currentAbortController.value = state.abortController
    currentTodos.value = state.todos || []
    loadedSessionId.value = sessionId
    return true
  }
  return false
}

// ── 会话级流式隔离 ──────────────────────────────────────────────────────
// 一个流事件只应写入「它所属会话」的数据，不能写当前正在查看的会话，否则多会话
// 并行流式（A 流式中新建/切换到 B）会串台：A 的增量被追加进 B 的 messages。
// 做法：展示中的会话（sid === loadedSessionId）直接用全局 refs；后台会话临时把全局
// refs 指向其 sessionStates 缓冲，处理完立即还原（全程同步，不会触发中途渲染）。
function ensureSessionState(sid) {
  let st = sessionStates.value[sid]
  if (!st) {
    st = {
      messages: [],
      isStreaming: true,
      sessionUsage: {
        input_tokens: 0,
        output_tokens: 0,
        reasoning_tokens: 0,
        context_length: sessionUsage.value.context_length,
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
  sessionStates.value = { ...sessionStates.value, [sid]: st }
  return st
}

// 流式处理器需要的页面级 refs：抽成模块后通过 ctx.state 注入
function buildStreamState() {
  return {
    sessionUsage,
    sessionDuration,
    iterationCount,
    sessionStates,
    currentSessionId,
    currentTodos,
    loadedSessionId,
    attachedStreamingSessions,
    sessions,
    pendingApproval,
    error,
    markStreaming,
    loadSessions,
    parseMCPResult,
  }
}

function runInSession(sid, ctx, fn) {
  if (!sid || sid === loadedSessionId.value) return fn() // 展示中的会话：直接用全局 refs
  const st = ensureSessionState(sid)
  const saved = {
    messages: messages.value,
    usage: sessionUsage.value,
    duration: sessionDuration.value,
    iterations: iterationCount.value,
    todos: currentTodos.value,
    streamingAssistantId: streamingAssistantId.value,
    assistantMsgId: ctx ? ctx.assistantMsgId : undefined,
  }
  messages.value = st.messages
  sessionUsage.value = st.sessionUsage
  sessionDuration.value = st.sessionDuration
  iterationCount.value = st.iterationCount
  currentTodos.value = st.todos
  streamingAssistantId.value = st.streamingAssistantId
  if (ctx) ctx.assistantMsgId = st.assistantMsgId
  const restore = () => {
    st.messages = messages.value
    st.sessionUsage = sessionUsage.value
    st.sessionDuration = sessionDuration.value
    st.iterationCount = iterationCount.value
    st.todos = currentTodos.value
    st.streamingAssistantId = streamingAssistantId.value
    if (ctx) st.assistantMsgId = ctx.assistantMsgId
    sessionStates.value = { ...sessionStates.value, [sid]: st }
    messages.value = saved.messages
    sessionUsage.value = saved.usage
    sessionDuration.value = saved.duration
    iterationCount.value = saved.iterations
    currentTodos.value = saved.todos
    streamingAssistantId.value = saved.streamingAssistantId
    if (ctx) ctx.assistantMsgId = saved.assistantMsgId
  }
  let result
  try {
    result = fn()
  } catch (e) {
    restore()
    throw e
  }
  // fn 可能返回 Promise（如流结束后的异步收尾）：等它 settle 后再还原，避免中途串台
  if (result && typeof result.then === 'function') {
    return result.finally(restore)
  }
  restore()
  return result
}

// 以服务端历史为准回填某会话：流式结束（列表"进行中"标记移除）后调用。
// 本地增量流状态在后台收尾/事件未命中时可能不完整，history 接口返回的是后端
// 完整落库消息，用它回填可保证"列表已完成"时页面渲染完整内容（无需刷新）。
async function syncSessionFromServer(sessionId) {
  if (!sessionId) return
  if (!sessions.value.some(s => s.session_id === sessionId)) return // 会话已删除/切换用户，跳过
  let history
  try {
    history = await getChatHistory(sessionId)
  } catch (e) {
    console.warn('回填会话历史失败:', e)
    return
  }
  // 拉取期间该会话若又开始了新一轮流式，别用旧历史覆盖
  if (isSessionStreaming(sessionId)) return

  const usage = history.usage || null
  if (currentSessionId.value === sessionId && loadedSessionId.value === sessionId) {
    messages.value = history.messages || []
    currentTodos.value = history.todos || []
    if (usage) {
      sessionUsage.value.input_tokens = usage.input_tokens || 0
      sessionUsage.value.output_tokens = usage.output_tokens || 0
      sessionUsage.value.reasoning_tokens = usage.reasoning_tokens || 0
      sessionUsage.value.context_tokens = usage.context_tokens || 0
      sessionDuration.value = usage.elapsed_time || 0
      iterationCount.value = usage.step_count || 0
    }
    if (history.context_length) sessionUsage.value.context_length = history.context_length
    scrollTrigger.value++
  } else {
    const st = ensureSessionState(sessionId)
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
    sessionStates.value = { ...sessionStates.value, [sessionId]: st }
  }
}

// HITL 历史恢复：从消息中重建待审批状态，使切换/重载会话后仍能显示审批按钮并继续执行。
// 历史消息无前端 id，按 thread_id（= `${session_id}-${message_id}`）还原后端 message_id
// 作为消息 id，保证恢复执行时记录使用一致的 message_id。监听 loadedSessionId 变化即可
// 覆盖所有历史加载路径（初始加载、切换会话、欢迎流程后加载、删除会话回退等）。
function reconstructPendingApproval() {
  // 流式进行中由 onApprovalRequired 管理审批状态，不重建以免覆盖
  if (isStreaming.value) return
  pendingApproval.value = null
  if (!messages.value || messages.value.length === 0) return
  const sid = currentSessionId.value || ''
  for (let i = messages.value.length - 1; i >= 0; i--) {
    const msg = messages.value[i]
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
      messages.value[i] = { ...msg, id: backendMsgId, blocks }
      if (hasPending) {
        pendingApproval.value = { threadId, assistantMsgId: backendMsgId }
      }
      break
    }
  }
}
watch(() => loadedSessionId.value, reconstructPendingApproval)
const isSidebarCollapsed = ref(false)
const isWorkspaceCollapsed = ref(true)
const showAssets = ref(false)
const showKnowledge = ref(false)
const showSkillCenter = ref(false)
const skillCenterTab = ref('experts')
const selectedExpert = ref(null)
const expertPrompt = ref('')
const showScheduledTasks = ref(false)
const showSettingsPanel = ref(false)
const showUserManagementPanel = ref(false)
const showWelcome = ref(false)
// 首屏引导中：认证 + 会话列表 + 首次历史加载完成前不渲染聊天区，
// 否则会先闪出空会话首页、会话时间下方那条分隔线也会闪一下。
const isBootstrapping = ref(true)
// 切换会话时正在从服务器拉取历史：期间不渲染空欢迎页，避免"先闪空会话页再出历史"
const sessionLoading = ref(false)
const scrollTrigger = ref(0)
const userProfile = ref({
  username: '',
  organization_id: '',
  email: ''
})

function toggleSidebar() {
  isSidebarCollapsed.value = !isSidebarCollapsed.value
}

function handleShowAssets() {
  showKnowledge.value = false
  showAssets.value = !showAssets.value
  showSkillCenter.value = false
  showScheduledTasks.value = false
}

function openSkillCenterPanel(tab) {
  if (tab === 'knowledge') return openKnowledgeWorkbench()
  showKnowledge.value = false
  // 「+」菜单入口：打开面板并直接定位到专家/技能/连接器
  skillCenterTab.value = tab
  showSkillCenter.value = true
}

// 知识库工作台：与聊天区互斥，打开时收起其他管理面板
function openKnowledgeWorkbench() {
  showKnowledge.value = true
  showAssets.value = false
  showSkillCenter.value = false
  showScheduledTasks.value = false
  showSettingsPanel.value = false
  showUserManagementPanel.value = false
}

function handleShowKnowledge() {
  showKnowledge.value = !showKnowledge.value
  showAssets.value = false
  showSkillCenter.value = false
  showScheduledTasks.value = false
  showSettingsPanel.value = false
  showUserManagementPanel.value = false
}

function handleShowSkillCenter() {
  showKnowledge.value = false
  showSkillCenter.value = !showSkillCenter.value
  showAssets.value = false
  showScheduledTasks.value = false
}

/**
 * 「专家 Tab -> 使用」（及任何来源的专家选中）：把专家挂到当前会话。
 *
 * 不强制新建会话：后端 thread_id = `{session_id}-{message_id}`，每条消息都是新线程，
 * 且 get_or_create_agent_for_session() 已按「专家是否变更」驱逐缓存，
 * 因此同一会话内切换专家能立即生效（详见 docs/expert-agent-solution.md）。
 */
function handleUseExpert(expert, prompt) {
  if (!expert || !expert.export_agent_id) return
  selectedExpert.value = {
    export_agent_id: expert.export_agent_id,
    export_name_cn: expert.export_name_cn,
    export_version: expert.export_version,
  }
  showSkillCenter.value = false
  // 详情弹窗点了示例问题：召唤专家的同时把问题回填到输入框（用户确认后发送）
  expertPrompt.value = typeof prompt === 'string' ? prompt : ''
}

function handleShowScheduledTasks() {
  showKnowledge.value = false
  showScheduledTasks.value = !showScheduledTasks.value
  showAssets.value = false
  showSkillCenter.value = false
}

function applyAgentConfig(configData) {
  if (!configData) return
  if (configData.context_length) {
    sessionUsage.value.context_length = configData.context_length
  }
  if (configData.preset_questions) {
    presetQuestions.value = configData.preset_questions
  }
  if (typeof configData.win === 'boolean') {
    appRuntime.win = configData.win
  }
  if (configData.agent_env) {
    appRuntime.agentEnv = configData.agent_env
  }
  if (typeof configData.app_welcome_title === 'string' && configData.app_welcome_title.trim()) {
    welcomeTitle.value = configData.app_welcome_title
  }
}

// 依据持久化的选择恢复初始会话视图：
//   - 上次所在会话（仍存在）→ 恢复该会话；
//   - '__new__' → 保持新会话首页（currentSessionId 为 null）；
//   - 无记录 / 会话已被删除 → 回退到列表第一个；列表为空则新会话首页。
// 加载对应历史后解除首屏门控（isBootstrapping），避免闪出空首页与顶部分隔线。
async function restoreInitialSession() {
  let stored = null
  try {
    stored = localStorage.getItem(ACTIVE_SESSION_KEY)
  } catch (_) { /* ignore */ }

  let initialSessionId = null
  if (stored === NEW_SESSION_MARK) {
    initialSessionId = null
  } else if (stored && sessions.value.some(s => s.session_id === stored)) {
    initialSessionId = stored
  } else if (sessions.value.length > 0) {
    initialSessionId = sessions.value[0].session_id
  }

  if (initialSessionId) {
    currentSessionId.value = initialSessionId
    if (!restoreSessionState(initialSessionId)) {
      try {
        const history = await getChatHistory(initialSessionId)
        // 加载期间用户可能已切换会话，丢弃过期响应（正常 bootstrap 期间 UI 门控，不会发生）
        if (currentSessionId.value === initialSessionId) {
          messages.value = history.messages || []
          currentTodos.value = history.todos || []
          if (history.usage) {
            sessionUsage.value.input_tokens = history.usage.input_tokens || 0
            sessionUsage.value.output_tokens = history.usage.output_tokens || 0
            sessionUsage.value.reasoning_tokens = history.usage.reasoning_tokens || 0
            sessionUsage.value.context_tokens = history.usage.context_tokens || 0
            sessionDuration.value = history.usage.elapsed_time || 0
            iterationCount.value = history.usage.step_count || 0
          }
          if (history.context_length) {
            sessionUsage.value.context_length = history.context_length
          }
          loadedSessionId.value = initialSessionId
        }
      } catch (e) {
        console.error('加载聊天历史失败:', e)
      }
    }
  } else {
    currentSessionId.value = null
    messages.value = []
    currentTodos.value = []
    loadedSessionId.value = null
  }

  hasBootstrapped.value = true
  try {
    localStorage.setItem(ACTIVE_SESSION_KEY, currentSessionId.value || NEW_SESSION_MARK)
  } catch (_) { /* ignore */ }
  isBootstrapping.value = false

  // 刷新前若正在流式，重新挂载并继续接收事件
  await tryAttachLiveStream()
}

async function handleWelcomeCompleted(profile) {
  isBootstrapping.value = true
  userProfile.value = profile
  showWelcome.value = false
  if (profile.context_length) {
    sessionUsage.value.context_length = profile.context_length
  }
  try {
    const configResp = await authFetch(`${API_BASE_URL}/agent/auth/config`)
    if (configResp.ok) {
      const configData = await configResp.json()
      applyAgentConfig(configData)
    }
  } catch (e) {
    console.warn('获取模型配置失败:', e)
  }
  loadModels()
  await loadSessions()
  await restoreInitialSession()
}

async function handleLogout() {
  // 通知后端记录登出（用户名/上次登录缓存时间/在线时长），best-effort。
  // 仅在仍持有 token 时通知：被动登出（401 被踢下线/过期）时 token 已被 clearAuth
  // 清除，再调登出接口会再次 401 触发 AUTH_EXPIRED 事件造成循环。
  if (getStoredToken()) {
    await notifyLogout()
  }
  apiLogout()
  sessions.value = []
  currentSessionId.value = null
  loadedSessionId.value = null
  sessionStates.value = {}
  messages.value = []
  streamingSessions.value = []
  lastStreamingSession.value = null
  attachedStreamingSessions.value = {}
  streamingAssistantId.value = null
  currentAbortController.value = null
  sessionStorage.removeItem(STREAM_STATE_KEY)
  sessionStorage.removeItem('easy_agent_input_draft')
  userProfile.value = {
    username: '',
    organization_id: '',
    email: ''
  }
  showKnowledge.value = false
  showAssets.value = false
  showSkillCenter.value = false
  selectedExpert.value = null
  hasBootstrapped.value = false
  clearActiveSession()
  showWelcome.value = true
}

async function handleUnregister() {
  sessions.value = []
  currentSessionId.value = null
  loadedSessionId.value = null
  sessionStates.value = {}
  messages.value = []
  showKnowledge.value = false
  userProfile.value = {
    username: '',
    organization_id: '',
    email: ''
  }
  hasBootstrapped.value = false
  clearActiveSession()
  showWelcome.value = true
}

async function loadUserProfile() {
  const storedToken = getStoredToken()
  const storedUsername = getStoredUsername()

  if (!storedToken || !storedUsername) {
    showWelcome.value = true
    return
  }

  try {
    const profile = await getUserProfile()
    if (!profile.username || profile.username === 'admin') {
      showWelcome.value = true
      return
    }
    userProfile.value = {
      username: profile.username || '',
      organization_id: profile.organization_id || '',
      email: profile.email || ''
    }
  } catch (e) {
    console.error('加载用户资料失败:', e)
    showWelcome.value = true
    return
  }

  try {
    const configResp = await authFetch(`${API_BASE_URL}/agent/auth/config`)
    if (configResp.ok) {
      const configData = await configResp.json()
      applyAgentConfig(configData)
    }
  } catch (e) {
    console.warn('获取模型配置失败:', e)
  }
}

// URL 免密直登：地址栏携带 ?username=xxx&user_id=yyy（user_id 可省略，默认 0）时
// 直接免密登录进入主界面，优先级高于已存储的登录态。
async function handlePasswordlessUrlLogin() {
  const params = new URLSearchParams(window.location.search)
  const username = params.get('username')
  if (!username) return false
  const userId = params.get('user_id') || '0'
  try {
    const data = await passwordlessLogin(username, userId)
    // 清除地址栏中的凭证参数，避免留在浏览器历史/后端访问日志
    window.history.replaceState({}, '', window.location.pathname)
    await handleWelcomeCompleted({
      username: data.username,
      context_length: data.context_length
    })
    return true
  } catch (e) {
    console.error('URL 免密登录失败:', e)
    return false
  }
}

async function loadSessions() {
  try {
    const data = await listSessions(userProfile.value.username || null)
    sessions.value = Array.isArray(data) ? data : (data.sessions || [])
  } catch (e) {
    console.error('加载会话列表失败:', e)
    error.value = '加载会话列表失败'
  }
}

async function ensureCurrentSession(initialTitle = '') {
  if (!currentSessionId.value) {
    const newSession = await createSession(initialTitle || '新会话', userProfile.value.username || null)
    currentSessionId.value = newSession.session_id
    const existingIndex = sessions.value.findIndex(s => s.session_id === newSession.session_id)
    if (existingIndex === -1) {
      const session = {
        session_id: newSession.session_id,
        title: newSession.title || initialTitle || '新会话',
        created_at: newSession.created_at || new Date().toISOString()
      }
      sessions.value = [session, ...sessions.value]
    }
  }
  return currentSessionId.value
}

/**
 * 技能中心的「创建技能」：预填内容已写入 sessionStorage，
 * 这里新建会话并关闭技能中心，ChatInput 随之重新挂载、自动恢复技能标签与提示词。
 */
async function handleSkillCreate() {
  await handleCreateSession()
}

/**
 * 专家面板「新建专家」：与「创建技能」完全一致 —— 预填草稿已由面板写入
 * sessionStorage，这里新开一个对话窗口即可（ChatInput 挂载时自动恢复草稿 + 技能标签）。
 */
async function handleExpertCreate() {
  await handleCreateSession()
}

/**
 * 专家面板卡片「编辑」：预填草稿已由面板写入 sessionStorage。
 *
 * 窗口选择规则（按需求）：
 * - 当前窗口就是**新会话**（无任何聊天记录）→ 直接把内容带入当前窗口，不新开；
 * - 当前窗口**已有聊天记录** → 新开一个会话窗口，避免把修改指令混进既有上下文。
 *
 * 两种情况都只需「关闭面板」：Chat 因 v-else-if 会重新挂载，
 * ChatInput 随之恢复草稿与技能标签。
 */
async function handleExpertEdit() {
  const isEmptySession = !currentSessionId.value || messages.value.length === 0
  if (isEmptySession) {
    showAssets.value = false
    showSkillCenter.value = false
    showScheduledTasks.value = false
    showSettingsPanel.value = false
    return
  }
  await handleCreateSession()
}

async function handleCreateSession() {
  saveCurrentSessionState()

  showKnowledge.value = false
  showAssets.value = false
  showSkillCenter.value = false
  selectedExpert.value = null
  showScheduledTasks.value = false
  showSettingsPanel.value = false
  currentSessionId.value = null
  loadedSessionId.value = null
  messages.value = []
  currentTodos.value = []
  // 注意：保留 context_length（全局上下文窗口，对所有会话通用），不重置为 null，
  // 否则 contextPercent 分母为 null 时会强制显示为 0%
  sessionUsage.value = { input_tokens: 0, output_tokens: 0, reasoning_tokens: 0, context_length: sessionUsage.value.context_length, auto_compress_tokens: null, context_tokens: 0 }
  sessionDuration.value = 0
  iterationCount.value = 0
  refreshSessionFiles(null)
}

async function handleSelectSession(sessionId) {
  // 保存当前会话状态
  saveCurrentSessionState()

  showKnowledge.value = false
  showAssets.value = false
  showSkillCenter.value = false
  // 切换会话时不沿用上一个会话的专家选择（选择是当前工作台的临时状态）
  selectedExpert.value = null
  showScheduledTasks.value = false
  currentSessionId.value = sessionId
  // 切换后按当前会话是否正在流式输出决定输入框状态：
  // 历史会话通常不是当前流式会话，应显示发送按钮而非停止按钮
  // 输入框状态由 isStreaming（当前会话是否在流式会话集合中）自动决定

  // 切换到进行中的会话：若本页尚未挂载其流式任务，懒挂载以继续接收事件
  // （刷新后恢复出的多个进行中会话，除当前展示外都走这里）
  if (isSessionStreaming(sessionId) && attachedStreamingSessions.value[sessionId] !== 'displayed') {
    attachToStreamingSession(sessionId, { switchTo: false, displayed: true })
    scrollTrigger.value++
    await refreshSessionFiles(sessionId)
    return
  }

  // 尝试从缓存恢复会话状态
  if (restoreSessionState(sessionId)) {
    scrollTrigger.value++
    await refreshSessionFiles(sessionId)
    return
  }

  // 缓存中没有，从服务器加载历史
  // 保留 context_length（全局上下文窗口），仅清空用量计数；
  // 若服务器返回了 context_length 则以其为准（见下方恢复逻辑）
  // sessionLoading 门控：加载期间不渲染空欢迎页，避免"先闪空会话页再出历史"
  sessionLoading.value = true
  sessionUsage.value = { input_tokens: 0, output_tokens: 0, reasoning_tokens: 0, context_length: sessionUsage.value.context_length, auto_compress_tokens: null, context_tokens: 0 }
  sessionDuration.value = 0
  iterationCount.value = 0

  try {
    const history = await getChatHistory(sessionId)
    // 加载期间用户可能又切换到了其他会话：丢弃过期响应，
    // 避免覆盖当前显示的数据（token 用量、消息等）
    if (currentSessionId.value !== sessionId) return
    messages.value = history.messages || []
    currentTodos.value = history.todos || []
    // 从服务器返回的 usage 数据恢复 token 用量、会话耗时和迭代次数
    if (history.usage) {
      sessionUsage.value.input_tokens = history.usage.input_tokens || 0
      sessionUsage.value.output_tokens = history.usage.output_tokens || 0
      sessionUsage.value.reasoning_tokens = history.usage.reasoning_tokens || 0
      sessionUsage.value.context_tokens = history.usage.context_tokens || 0
      sessionDuration.value = history.usage.elapsed_time || 0
      iterationCount.value = history.usage.step_count || 0
    }
    if (history.context_length) {
      sessionUsage.value.context_length = history.context_length
    }
    // 数据加载完成，界面数据现在归属于该会话
    loadedSessionId.value = sessionId
    scrollTrigger.value++

    await refreshSessionFiles(sessionId)
  } catch (e) {
    console.error('加载聊天历史失败:', e)
    if (currentSessionId.value !== sessionId) return
    error.value = '加载聊天历史失败'
    // 加载失败时界面数据处于不一致状态（消息还是旧会话的、用量已清零），
    // 置空 loadedSessionId 防止后续把错误数据写入缓存
    loadedSessionId.value = null
    refreshSessionFiles(sessionId)
  } finally {
    sessionLoading.value = false
  }
}

async function handleDeleteSession(sessionId) {
  try {
    await deleteSession(sessionId)
    sessions.value = sessions.value.filter(s => s.session_id !== sessionId)

    // 清除会话缓存
    delete sessionStates.value[sessionId]

    if (currentSessionId.value === sessionId) {
      // 先清空归属（避免把已删除会话的界面数据再存回缓存），再决定回退到哪个会话
      currentSessionId.value = null
      loadedSessionId.value = null
      messages.value = []
      const nextId = sessions.value[0]?.session_id || null
      if (nextId) {
        // 复用切会话逻辑：加载其历史（含 sessionLoading 骨架屏），而不是留一个空欢迎页
        await handleSelectSession(nextId)
      } else {
        // 没有剩余会话：回到新建会话首页
        await handleCreateSession()
      }
    } else if (loadedSessionId.value === sessionId) {
      loadedSessionId.value = null
    }
    // 删除进行中的会话：同步移除流式标记，避免列表残留"进行中"徽标
    unmarkStreaming(sessionId)
    delete attachedStreamingSessions.value[sessionId]
  } catch (e) {
    console.error('删除会话失败:', e)
    error.value = '删除会话失败'
  }
}

async function handleRenameSession(sessionId, newTitle) {
  try {
    await renameSession(sessionId, newTitle)
    const idx = sessions.value.findIndex(s => s.session_id === sessionId)
    if (idx !== -1) {
      sessions.value[idx] = { ...sessions.value[idx], title: newTitle }
    }
  } catch (e) {
    console.error('重命名会话失败:', e)
    error.value = '重命名会话失败'
  }
}

async function handleTogglePin(sessionId) {
  try {
    const result = await togglePinSession(sessionId)
    const idx = sessions.value.findIndex(s => s.session_id === sessionId)
    if (idx !== -1) {
      sessions.value[idx] = { ...sessions.value[idx], pinned: result.pinned }
    }
    // 重新排序：置顶在前
    sessions.value.sort((a, b) => (b.pinned || 0) - (a.pinned || 0) || new Date(b.updated_at) - new Date(a.updated_at))
  } catch (e) {
    console.error('置顶操作失败:', e)
    error.value = '置顶操作失败'
  }
}

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


async function handleSendMessage(message, files = [], signal, enableDeepThink = true, enableWebSearch = false, skills = [], expertEditId = '') {
  const userMsgId = `user-${Date.now()}`
  const preStreamUsage = { ...sessionUsage.value }
  // 记录本次请求开始前已累计的耗时和迭代次数，用于流式过程中实时累加
  const preStreamDuration = sessionDuration.value
  const preStreamIterationCount = iterationCount.value
  // 本次流所属的会话 ID（新会话在 start 事件后才有值）。
  // 用于用户中途切换会话时，把用量更新写入所属会话的缓存而非当前显示
  let streamSessionId = currentSessionId.value

  let contentWithFiles = message.trim().replace(/\s+/g, ' ')

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
  // 结构化技能目录名（与后端落库字段同形）：气泡按它渲染技能标签，不再依赖从消息
  // 文本里反扒 `/技能名`。没有选中技能时不写该字段——手打 `/技能名` 的正文仍按老规则
  // 从文本解析，行为和改造前一致。
  if (skills.length > 0) userMessage.skills = [...skills]

  // 专家模式：先在本轮消息上记下使用的专家（回答面板左上角「头像 + 名称」的数据来源）。
  // 内存态必须显式写：后端落库的用户消息要等刷新/历史加载才回灌，只依赖后端会出现
  // 「选中专家发送后左上角空白、刷新一下才出现」。（与 frontend-vue2 同构）
  const usedExpert = (selectedExpert.value && selectedExpert.value.export_agent_id)
    ? { id: selectedExpert.value.export_agent_id, name: selectedExpert.value.export_name_cn || '专家' }
    : null
  if (usedExpert) userMessage.expert = usedExpert

  messages.value.push(userMessage)

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
    loading: true,
    expert: usedExpert
  }
  messages.value.push(assistantPlaceholder)
  streamingAssistantId.value = assistantPlaceholderId

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
        if (assistantPlaceholderId && messages.value.find(m => m.id === assistantPlaceholderId)) {
          assistantMsgId = assistantPlaceholderId
        } else {
          assistantMsgId = `assistant-${Date.now()}`
          messages.value.push({
            id: assistantMsgId, role: 'assistant', content: '',
            created_at: null, thinking: '', tool_calls: [], blocks: [], loading: true,
            expert: usedExpert,
          })
        }
        streamingAssistantId.value = assistantMsgId
        assistantMessageCreated = true
      }
    },
    onApprovalRequired: (data) => {
      pendingApproval.value = { threadId: data.thread_id, assistantMsgId }
      if (assistantMsgId && data.action_requests) {
        const idx = messages.value.findIndex(m => m.id === assistantMsgId)
        if (idx !== -1) {
          messages.value[idx].pending_approval = { thread_id: data.thread_id, action_requests: data.action_requests }
          // 挂不上工具卡片的（子代理内部审批）由 ChatMessage 的独立审批面板展示
          applyApprovalRequests(messages.value[idx].blocks, data.action_requests)
          messages.value[idx] = { ...messages.value[idx], blocks: [...messages.value[idx].blocks] }
        }
      }
    },
  }
  streamCtx.runInSession = runInSession
  streamCtx.state = buildStreamState()
  const { onChunk } = createStreamChunkHandler(messages, streamCtx)

  try {
    markStreaming(currentSessionId.value)
    const controller = new AbortController()
    currentAbortController.value = controller
    const abortSignal = signal || controller.signal

    // 更新会话缓存状态
    if (currentSessionId.value) {
      sessionStates.value[currentSessionId.value] = {
        ...sessionStates.value[currentSessionId.value],
        isStreaming: true,
        abortController: controller
      }
    }

    // 专家模式：改走 /agent/stream_export_run，由后端按选中的专家动态构建 Agent
    // （加载该专家当前生效版本的提示词，未选中的情况退化为通用 Agent）。
    // SSE 事件格式与 /agent/chat/stream 完全一致，因此 onChunk 复用同一处理器。
    // 未选专家时保持原有链路，行为不变。
    if (selectedExpert.value && selectedExpert.value.export_agent_id) {
      await streamExportRun(
        {
          message,
          sessionId: currentSessionId.value,
          expertAgentId: selectedExpert.value.export_agent_id,
          model: selectedModel.value,
          enableDeepThink,
          enableWebSearch,
          files,
          // 已选技能：结构化字段（技能目录名），`/目录名` 由后端拼进模型输入
          skills,
        },
        onChunk,
        abortSignal
      )
    } else {
      await sendMessage(
        currentSessionId.value,
        message,
        onChunk,
        abortSignal,
        enableDeepThink,
        files,
        selectedModel.value,
        enableWebSearch,
        skills,
        // 待修改专家 ID（专家面板「编辑」带入，不进消息正文）：后端校验归属后
        // 绑定为 save_expert 的修改目标
        expertEditId
      )
    }

    await expandWorkspaceAfterSessionCompletion(streamSessionId)
  } catch (e) {
    if (e.name === 'AbortError') {
      // Mark assistant message as complete (loading=false) so spinners stop
      runInSession(streamSessionId, streamCtx, () => {
        if (assistantMsgId) {
          const idx = messages.value.findIndex(m => m.id === assistantMsgId)
          if (idx !== -1) {
            messages.value[idx].loading = false
            messages.value[idx].created_at = new Date().toISOString()
            messages.value[idx] = { ...messages.value[idx] }
          }
        }
      })
      return
    }
    console.error('发送消息失败:', e)
    runInSession(streamSessionId, streamCtx, () => {
      if (assistantMsgId) {
        const idx = messages.value.findIndex(m => m.id === assistantMsgId)
        if (idx !== -1) {
          messages.value[idx].loading = false
          messages.value[idx].error = e.message || '发送消息失败，请检查网络连接'
          messages.value[idx] = { ...messages.value[idx] }
        } else {
          // 消息不存在，添加一条错误消息
          messages.value.push({
            id: assistantMsgId,
            role: 'assistant',
            content: '',
            error: e.message || '发送消息失败，请检查网络连接',
            loading: false,
            created_at: new Date().toISOString(),
            blocks: [],
            expert: usedExpert
          })
        }
      }
    })
    // 仅当前展示会话的失败才提示到界面；后台会话失败不打扰当前会话
    if (!streamSessionId || streamSessionId === loadedSessionId.value) {
      error.value = e.message || '发送消息失败'
    }
  } finally {
    // HITL: 若有审批待处理，保持 isStreaming=true（用户需先审批）
    if (!pendingApproval.value) {
      const sid = streamSessionId
      const isDisplayed = !sid || sid === loadedSessionId.value
      // 兜底：流已结束（列表"进行中"标记即将移除），若该会话的消息仍标记 loading
      // 就强制收尾，避免"会话列表已显示完成、会话页仍显示执行中"。
      // done 事件未命中目标消息时会残留 loading=true，此处与其兜底一致。
      runInSession(sid, streamCtx, () => {
        let idx = assistantMsgId ? messages.value.findIndex(m => m.id === assistantMsgId) : -1
        if (idx === -1) {
          for (let i = messages.value.length - 1; i >= 0; i--) {
            if (messages.value[i].role === 'assistant' && messages.value[i].loading) { idx = i; break }
          }
        }
        if (idx !== -1 && messages.value[idx].loading) {
          messages.value[idx] = { ...messages.value[idx], loading: false }
        }
      })
      // 只有展示中的会话才清显示级状态；后台流结束不能动当前会话的停止按钮等
      if (isDisplayed) {
        currentAbortController.value = null
        streamingAssistantId.value = null
      }
      if (sid && sessionStates.value[sid]) {
        sessionStates.value[sid].isStreaming = false
        sessionStates.value[sid].abortController = null
        if (!isDisplayed) sessionStates.value[sid].streamingAssistantId = null
      }
      if (sid) delete attachedStreamingSessions.value[sid]
      unmarkStreaming(sid)
      // 列表"进行中"标记此时移除（显示已完成）：以服务端历史为准回填，保证完整渲染
      await syncSessionFromServer(sid)

      await loadSessions()
    }
  }
}

// HITL: 用户审批后恢复执行。target.interruptId = 用户点的那个中断（DA-02）：
// 只回答它，其余中断保持挂起，恢复流结束时后端会再次下发 approval_required。
async function handleToolApproval(decision, target) {
  if (!pendingApproval.value) return

  const currentApproval = pendingApproval.value
  const { threadId, assistantMsgId } = currentApproval
  const sessionId = currentSessionId.value

  const msgIdx = assistantMsgId ? messages.value.findIndex(m => m.id === assistantMsgId) : -1
  const pending = msgIdx !== -1 ? messages.value[msgIdx].pending_approval : null
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
    markApprovalDecision(messages.value[msgIdx].blocks, decision, actionRequests, interruptId)
    messages.value[msgIdx] = {
      ...messages.value[msgIdx],
      pending_approval: null,
      blocks: [...messages.value[msgIdx].blocks],
    }
  }

  const { onChunk } = createStreamChunkHandler(messages, {
    isResume: true,
    assistantMsgId,
    streamSessionId: sessionId,
    runInSession,
    state: buildStreamState(),
    initialBlockOrder: (() => {
      const i = messages.value.findIndex(m => m.id === assistantMsgId)
      return i !== -1 ? messages.value[i].blocks.length : 0
    })(),
    onApprovalRequired: (data) => {
      // 嵌套审批
      pendingApproval.value = {
        threadId: data.thread_id,
        assistantMsgId,
      }
      if (assistantMsgId && data.action_requests) {
        const idx = messages.value.findIndex(m => m.id === assistantMsgId)
        if (idx !== -1) {
          messages.value[idx].pending_approval = { thread_id: data.thread_id, action_requests: data.action_requests }
          // 用后端权威 blocks 刷新（含本轮已完成工具的结果）
          if (Array.isArray(data.blocks) && data.blocks.length > 0) {
            messages.value[idx].blocks = data.blocks.map(b => ({ ...b }))
          }
          // 防御：嵌套审批时本轮思考已完成，把残留无 duration 的思考块置 0，
          // 避免 block.duration==null && loading=true 误显示"正在思考"
          for (const b of messages.value[idx].blocks) {
            if (b.type === 'thinking' && (b.duration == null)) {
              b.duration = 0
            }
          }
          // 挂不上工具卡片的（子代理内部审批）由 ChatMessage 的独立审批面板展示
          applyApprovalRequests(messages.value[idx].blocks, data.action_requests)
          messages.value[idx] = { ...messages.value[idx], blocks: [...messages.value[idx].blocks] }
        }
      }
    },
  })

  try {
    const controller = new AbortController()
    currentAbortController.value = controller
    markStreaming(sessionId)
    streamingAssistantId.value = assistantMsgId

    // 恢复期间保持 loading=true，使思考 spinner 与工具"执行中"状态与正常流一致
    const _resumeIdx = messages.value.findIndex(m => m.id === assistantMsgId)
    if (_resumeIdx !== -1) {
      messages.value[_resumeIdx] = { ...messages.value[_resumeIdx], loading: true }
    }
    // 注意：人工介入（批准/拒绝）不再作为用户侧消息展示，
    // 直接在模型侧的 execute 工具上进行了 HITL 标注。
    await resumeStream(sessionId, threadId, decisions, onChunk, controller.signal, assistantMsgId)
    await expandWorkspaceAfterSessionCompletion(sessionId)

    await loadSessions()
  } catch (e) {
    if (e.name === 'AbortError') return
    console.error('恢复执行失败:', e)
    const idx = messages.value.findIndex(m => m.id === assistantMsgId)
    if (idx !== -1) {
      messages.value[idx].loading = false
      messages.value[idx].error = e.message || '恢复执行失败'
      messages.value[idx] = { ...messages.value[idx] }
    }
  } finally {
    // 如果 resumeStream 期间收到了新的 approval_required（onChunk 重新设置了
    // pendingApproval.value），则不清除，保持 isStreaming=true 等待用户审批
    const hasNewApproval = pendingApproval.value && pendingApproval.value !== currentApproval
    if (!hasNewApproval) {
      pendingApproval.value = null
      currentAbortController.value = null
      streamingAssistantId.value = null
      const sid = sessionId
      if (sid && sessionStates.value[sid]) {
        sessionStates.value[sid].isStreaming = false
        sessionStates.value[sid].abortController = null
      }
      unmarkStreaming(sid)
      saveCurrentSessionState()
      // 审批恢复流结束：以服务端历史为准回填，保证完整渲染
      await syncSessionFromServer(sid)
      await loadSessions()
    }
  }
}

function handleRetry(content) {
  // 直接重新发送消息内容，不经过输入框
  console.log('[handleRetry] content type:', typeof content, 'value:', content)
  
  if (content && typeof content === 'string' && content.trim()) {
    // 检查是否正在流式输出
    if (isStreaming.value) {
      error.value = '请等待当前消息完成'
      return
    }
    
    // 确保有当前会话
    if (!currentSessionId.value) {
      error.value = '请先创建会话'
      return
    }
    
    // 直接调用发送
    handleSendMessage(content.trim())
  }
}

function handleStop() {
  // Mark current assistant message as no longer loading (stops tool spinning)
  if (streamingAssistantId.value) {
    const idx = messages.value.findIndex(m => m.id === streamingAssistantId.value)
    if (idx !== -1) {
      messages.value[idx] = { ...messages.value[idx], loading: false }
    }
  }
  streamingAssistantId.value = null

  // 停止按钮仅在当前会话正在流式时显示，取消的会话即当前会话
  const sid = currentSessionId.value

  // 中止正在进行的 fetch 请求（关闭 SSE 连接）
  if (currentAbortController.value) {
    currentAbortController.value.abort()
    currentAbortController.value = null
  }

  // 更新会话缓存状态
  if (sid && sessionStates.value[sid]) {
    sessionStates.value[sid].isStreaming = false
    sessionStates.value[sid].abortController = null
  }
  unmarkStreaming(sid)
  delete attachedStreamingSessions.value[sid]

  // 通知后端取消正在运行的流式任务（中断 astream 执行）并清除 Agent 缓存
  if (sid) {
    authFetch(`${API_BASE_URL}/agent/chat/cancel?session_id=${encodeURIComponent(sid)}`, {
      method: 'POST',
    }).catch(() => {})
  }
  error.value = '已停止生成'
  setTimeout(() => {
    error.value = null
  }, 2000)
}

async function handleRemoveFile(message, messageIndex, file) {
  try {
    // 调用后端的删除文件接口
    await deleteFile(currentSessionId.value, file)
    
    // 更新前端的消息列表，移除已删除的文件
    if (messages.value[messageIndex]) {
      const updatedMessage = {
        ...messages.value[messageIndex],
        files: messages.value[messageIndex].files.filter(f => f.filename !== file.filename)
      }
      messages.value.splice(messageIndex, 1, updatedMessage)
    }
    
    // 显示删除成功的提示
    error.value = '文件删除成功'
    setTimeout(() => {
      error.value = null
    }, 2000)
  } catch (err) {
    console.error('文件删除失败:', err)
    error.value = '文件删除失败'
    setTimeout(() => {
      error.value = null
    }, 2000)
  }
}

onMounted(async () => {
  window.addEventListener(AUTH_EXPIRED_EVENT, handleLogout)
  try {
    // URL 免密直登（?username=xxx&user_id=yyy）：成功则直接进入主界面
    if (await handlePasswordlessUrlLogin()) return
    await loadUserProfile()
    if (!showWelcome.value) {
      // 拉取可选模型列表（不阻塞会话加载）
      loadModels()
      await loadSessions()
      await restoreInitialSession()
    }
  } finally {
    // 未登录时也解除首屏门控，让 Welcome 登录页正常显示
    isBootstrapping.value = false
  }
})
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
