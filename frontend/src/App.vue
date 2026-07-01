<template>
  <div class="app-container">
    <Welcome 
      v-if="showWelcome" 
      @completed="handleWelcomeCompleted" 
    />
    
    <template v-else>
      <SessionList
        v-show="!isSidebarCollapsed"
        :sessions="sessions"
        :currentSessionId="currentSessionId"
        :username="userProfile.username"
        :organizationId="userProfile.organization_id"
        :email="userProfile.email"
        :showAssets="showAssets"
        @createSession="handleCreateSession"
        @selectSession="handleSelectSession"
        @deleteSession="handleDeleteSession"
        @renameSession="handleRenameSession"
        @togglePin="handleTogglePin"
        @toggleSidebar="toggleSidebar"
        @showAssets="handleShowAssets"
        @showSkillCenter="handleShowSkillCenter"
        @showScheduledTasks="handleShowScheduledTasks"
        @showProfile="handleShowProfile"
        @showSettings="showSettingsPanel = true"
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

      <SkillCenter v-if="showSkillCenter" @close="showSkillCenter = false" />

      <ScheduledTasksPanel v-if="showScheduledTasks" @close="showScheduledTasks = false" />
      
      <UserProfile
        v-if="showUserProfile"
        @close="showUserProfile = false"
        @logout="handleLogout"
        @unregister="handleUnregister"
      />
      
      <SettingsPanel
        v-if="showSettingsPanel"
        @close="showSettingsPanel = false"
        @toggle-theme="toggleTheme"
        :isDarkTheme="isDarkTheme"
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
        :workspaceExpanded="!isWorkspaceCollapsed"
        :sidebarCollapsed="isSidebarCollapsed"
        @sendMessage="handleSendMessage"
        @createSession="ensureCurrentSession"
        @removeFile="handleRemoveFile"
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
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import SessionList from './components/SessionList.vue'
import Chat from './components/Chat.vue'
import AssetsPanel from './components/AssetsPanel.vue'
import SkillCenter from './components/SkillCenter.vue'
import ScheduledTasksPanel from './components/ScheduledTasksPanel.vue'
import UserProfile from './components/UserProfile.vue'
import Welcome from './components/Welcome.vue'
import WorkspacePanel from './components/WorkspacePanel.vue'
import SettingsPanel from './components/SettingsPanel.vue'
import { createSession, listSessions, getChatHistory, deleteSession, sendMessage, resumeStream, renameSession, togglePinSession } from './api/chat.js'
import { uploadFile, deleteFile, getUserProfile, getSessionGeneratedFiles } from './api/files.js'
import { logout as apiLogout, getStoredToken, getStoredUsername, AUTH_EXPIRED_EVENT, authFetch } from './api/auth.js'

const sessions = ref([])
const currentSessionId = ref(null)
const currentSessionHasFiles = ref(false)
const currentSessionCreatedAt = computed(() => {
  if (!currentSessionId.value) return null
  const session = sessions.value.find(s => s.session_id === currentSessionId.value)
  return session?.created_at || null
})
let filesCheckTimer = null

// 会话状态缓存：为每个会话保存独立的流式状态
const sessionStates = ref({})

async function refreshSessionFiles(sessionId = null, delayMs = 0) {
  const targetId = sessionId || currentSessionId.value
  if (!targetId) {
    currentSessionHasFiles.value = false
    return
  }
  if (filesCheckTimer) {
    clearTimeout(filesCheckTimer)
    filesCheckTimer = null
  }
  const doCheck = async () => {
    try {
      const files = await getSessionGeneratedFiles(targetId)
      currentSessionHasFiles.value = Array.isArray(files) && files.length > 0
      console.log('[Files] Session', targetId, 'has files:', currentSessionHasFiles.value, 'count:', files?.length)
    } catch (e) {
      console.error('[Files] 检查会话文件失败:', e)
      currentSessionHasFiles.value = false
    }
  }
  if (delayMs > 0) {
    filesCheckTimer = setTimeout(doCheck, delayMs)
  } else {
    await doCheck()
  }
}
const messages = ref([])
const isStreaming = ref(false)
const streamingSessionId = ref(null) // 记录正在流式输出的会话 ID
const error = ref(null)
const currentAbortController = ref(null)
// HITL: 审批待处理状态，存储 { threadId, assistantMsgId }
const pendingApproval = ref(null)
const sessionUsage = ref({ input_tokens: 0, output_tokens: 0, total_tokens: 0, max_input_tokens: null, auto_compress_tokens: null, context_tokens: 0 })
// 当前会话累计耗时（秒），每次 AI 回复完成后累加
const sessionDuration = ref(0)
// 当前会话累计迭代次数（step 数），每次 AI 回复完成后累加
const iterationCount = ref(0)
const currentTodos = ref([])
const presetQuestions = ref([])

// 保存当前会话状态到缓存
function saveCurrentSessionState() {
  if (!currentSessionId.value) return
  sessionStates.value[currentSessionId.value] = {
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
    // isStreaming 以 streamingSessionId 为准：仅当前会话正是流式会话时才显示停止按钮
    // 避免切换到历史会话时残留的 isStreaming=true 导致停止按钮错误显示
    isStreaming.value = (sessionId === streamingSessionId.value)
    sessionUsage.value = { ...state.sessionUsage }
    sessionDuration.value = state.sessionDuration || 0
    iterationCount.value = state.iterationCount || 0
    currentAbortController.value = state.abortController
    currentTodos.value = state.todos || []
    return true
  }
  return false
}
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
const isSidebarCollapsed = ref(false)
const isWorkspaceCollapsed = ref(true)
const isDarkTheme = ref(localStorage.getItem('theme') === 'dark')
const showAssets = ref(false)
const showSkillCenter = ref(false)
const showScheduledTasks = ref(false)
const showUserProfile = ref(false)
const showSettingsPanel = ref(false)
const showWelcome = ref(false)
const scrollTrigger = ref(0)
const userProfile = ref({
  username: '',
  organization_id: '',
  email: ''
})

function toggleSidebar() {
  isSidebarCollapsed.value = !isSidebarCollapsed.value
}

function toggleTheme() {
  isDarkTheme.value = !isDarkTheme.value
  localStorage.setItem('theme', isDarkTheme.value ? 'dark' : 'light')
  document.documentElement.setAttribute('data-theme', isDarkTheme.value ? 'dark' : 'light')
}

// 初始化主题（确保 data-theme 属性始终存在，使 CSS 变量生效）
document.documentElement.setAttribute('data-theme', isDarkTheme.value ? 'dark' : 'light')

function handleShowAssets() {
  showAssets.value = !showAssets.value
  showSkillCenter.value = false
  showScheduledTasks.value = false
}

function handleShowSkillCenter() {
  showSkillCenter.value = !showSkillCenter.value
  showAssets.value = false
  showScheduledTasks.value = false
}

function handleShowScheduledTasks() {
  showScheduledTasks.value = !showScheduledTasks.value
  showAssets.value = false
  showSkillCenter.value = false
}

function handleShowProfile() {
  showUserProfile.value = true
  showAssets.value = false
  showSkillCenter.value = false
  showScheduledTasks.value = false
}

async function handleWelcomeCompleted(profile) {
  userProfile.value = profile
  showWelcome.value = false
  if (profile.max_input_tokens) {
    sessionUsage.value.max_input_tokens = profile.max_input_tokens
  }
  try {
    const configResp = await authFetch(`${API_BASE_URL}/api/auth/config`)
    if (configResp.ok) {
      const configData = await configResp.json()
      if (configData.max_input_tokens) {
        sessionUsage.value.max_input_tokens = configData.max_input_tokens
      }
      if (configData.preset_questions) {
        presetQuestions.value = configData.preset_questions
      }
    }
  } catch (e) {
    console.warn('获取模型配置失败:', e)
  }
  await loadSessions()
  if (sessions.value.length > 0) {
    currentSessionId.value = sessions.value[0].session_id
    // 尝试从缓存恢复，否则加载历史
    if (!restoreSessionState(currentSessionId.value)) {
      try {
        const history = await getChatHistory(currentSessionId.value)
        messages.value = history.messages || []
        currentTodos.value = history.todos || []
        if (history.usage) {
          sessionUsage.value.input_tokens = history.usage.input_tokens || 0
          sessionUsage.value.output_tokens = history.usage.output_tokens || 0
          sessionUsage.value.total_tokens = history.usage.total_tokens || 0
          sessionUsage.value.context_tokens = history.usage.context_tokens || 0
          sessionDuration.value = history.usage.elapsed_time || 0
          iterationCount.value = history.usage.step_count || 0
        }
        if (history.max_input_tokens) {
          sessionUsage.value.max_input_tokens = history.max_input_tokens
        }
      } catch (e) {
        console.error('加载聊天历史失败:', e)
      }
    }
  }
}

async function handleLogout() {
  apiLogout()
  sessions.value = []
  currentSessionId.value = null
  messages.value = []
  userProfile.value = {
    username: '',
    organization_id: '',
    email: ''
  }
  showUserProfile.value = false
  showAssets.value = false
  showSkillCenter.value = false
  showWelcome.value = true
}

async function handleUnregister() {
  sessions.value = []
  currentSessionId.value = null
  messages.value = []
  userProfile.value = {
    username: '',
    organization_id: '',
    email: ''
  }
  showUserProfile.value = false
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
    const configResp = await authFetch(`${API_BASE_URL}/api/auth/config`)
    if (configResp.ok) {
      const configData = await configResp.json()
      if (configData.max_input_tokens) {
        sessionUsage.value.max_input_tokens = configData.max_input_tokens
      }
      if (configData.preset_questions) {
        presetQuestions.value = configData.preset_questions
      }
    }
  } catch (e) {
    console.warn('获取模型配置失败:', e)
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

async function handleCreateSession() {
  saveCurrentSessionState()

  showAssets.value = false
  showSkillCenter.value = false
  currentSessionId.value = null
  messages.value = []
  currentTodos.value = []
  isStreaming.value = false
  sessionUsage.value = { input_tokens: 0, output_tokens: 0, total_tokens: 0, max_input_tokens: null, auto_compress_tokens: null, context_tokens: 0 }
  sessionDuration.value = 0
  iterationCount.value = 0
  refreshSessionFiles(null)
}

async function handleSelectSession(sessionId) {
  // 保存当前会话状态
  saveCurrentSessionState()

  showAssets.value = false
  showSkillCenter.value = false
  showScheduledTasks.value = false
  currentSessionId.value = sessionId
  // 切换后按当前会话是否正在流式输出决定输入框状态：
  // 历史会话通常不是当前流式会话，应显示发送按钮而非停止按钮
  isStreaming.value = (sessionId === streamingSessionId.value)

  // 尝试从缓存恢复会话状态
  if (restoreSessionState(sessionId)) {
    scrollTrigger.value++
    await refreshSessionFiles(sessionId)
    return
  }

  // 缓存中没有，从服务器加载历史
  sessionUsage.value = { input_tokens: 0, output_tokens: 0, total_tokens: 0, max_input_tokens: null, auto_compress_tokens: null, context_tokens: 0 }
  sessionDuration.value = 0
  iterationCount.value = 0

  try {
    const history = await getChatHistory(sessionId)
    messages.value = history.messages || []
    currentTodos.value = history.todos || []
    // 从服务器返回的 usage 数据恢复 token 用量、会话耗时和迭代次数
    if (history.usage) {
      sessionUsage.value.input_tokens = history.usage.input_tokens || 0
      sessionUsage.value.output_tokens = history.usage.output_tokens || 0
      sessionUsage.value.total_tokens = history.usage.total_tokens || 0
      sessionUsage.value.context_tokens = history.usage.context_tokens || 0
      sessionDuration.value = history.usage.elapsed_time || 0
      iterationCount.value = history.usage.step_count || 0
    }
    if (history.max_input_tokens) {
      sessionUsage.value.max_input_tokens = history.max_input_tokens
    }
    scrollTrigger.value++

    await refreshSessionFiles(sessionId)
  } catch (e) {
    console.error('加载聊天历史失败:', e)
    error.value = '加载聊天历史失败'
    refreshSessionFiles(sessionId)
  }
}

async function handleDeleteSession(sessionId) {
  try {
    await deleteSession(sessionId)
    sessions.value = sessions.value.filter(s => s.session_id !== sessionId)

    // 清除会话缓存
    delete sessionStates.value[sessionId]

    if (currentSessionId.value === sessionId) {
      currentSessionId.value = sessions.value[0]?.session_id || null
      if (currentSessionId.value && sessionStates.value[currentSessionId.value]) {
        restoreSessionState(currentSessionId.value)
      } else {
        messages.value = []
        isStreaming.value = false
      }
    }
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

async function handleSendMessage(message, files = [], signal, enableDeepThink = true, enableKnowledgeBase = false) {
  const userMsgId = `user-${Date.now()}`
  const preStreamUsage = { ...sessionUsage.value }
  // 记录本次请求开始前已累计的耗时和迭代次数，用于流式过程中实时累加
  const preStreamDuration = sessionDuration.value
  const preStreamIterationCount = iterationCount.value

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
    loading: true
  }
  messages.value.push(assistantPlaceholder)

  let assistantMsgId = null
  let assistantMessageCreated = false

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

  function ensureAssistantMessage() {
    if (!assistantMessageCreated) {
      // 复用已有的占位消息（在发送消息时已创建）
      if (assistantPlaceholderId && messages.value.find(m => m.id === assistantPlaceholderId)) {
        assistantMsgId = assistantPlaceholderId
      } else {
        assistantMsgId = `assistant-${Date.now()}`
        const assistantMessage = {
          id: assistantMsgId,
          role: 'assistant',
          content: '',
          created_at: null,
          thinking: '',
          tool_calls: [],
          blocks: [],
          loading: true
        }
        messages.value.push(assistantMessage)
      }
      assistantMessageCreated = true
    }
  }

  let currentThinking = ''
  let totalThinkingDuration = 0
  let currentContent = ''
  let currentToolCalls = []
  let currentBlock = null
  let blockOrderCounter = 0

  function addBlock(type, data, replace = false) {
    ensureAssistantMessage()
    const idx = messages.value.findIndex(m => m.id === assistantMsgId)
    if (idx === -1) return null

    // Dedup: for thinking blocks, reuse existing block for the same step
    if (type === 'thinking' && data.step !== undefined) {
      const existing = messages.value[idx].blocks.find(
        b => b.type === 'thinking' && b.step === data.step
      )
      if (existing) {
        currentBlock = existing
        return currentBlock
      }
    }

    blockOrderCounter++
    if (!currentBlock || currentBlock.type !== type) {
      currentBlock = { type, content: '', order: blockOrderCounter, ...data }
      messages.value[idx].blocks.push(currentBlock)
      messages.value[idx] = { ...messages.value[idx], blocks: [...messages.value[idx].blocks] }
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
      messages.value[idx] = { ...messages.value[idx] }
    }
    return currentBlock
  }

  function updateThinkingDuration(duration, step) {
    const idx = messages.value.findIndex(m => m.id === assistantMsgId)
    if (idx !== -1) {
      const blockIdx = messages.value[idx].blocks.findIndex(b => b.type === 'thinking' && b.step === step)
      if (blockIdx !== -1) {
        messages.value[idx].blocks[blockIdx] = {
          ...messages.value[idx].blocks[blockIdx],
          duration: duration
        }
      }
      messages.value[idx] = { 
        ...messages.value[idx], 
        thinking_duration: duration,
        blocks: [...messages.value[idx].blocks]
      }
    }
  }

  function onChunk(data) {
    const { type: eventType, content, thinking, tool_calls, duration, step, tool_name, tool_call_id: toolCallId, arguments: args, result, success, title } = data

    if (eventType === 'start') {
      // Reset todo list for new stream
      currentTodos.value = []
      // 后端返回的 session_id，用于更新当前会话 ID
      if (data.session_id && !currentSessionId.value) {
        currentSessionId.value = data.session_id
        // 新会话创建后立即刷新会话列表
        loadSessions()
      }
      // 同步 streamingSessionId：新会话场景下发送时 currentSessionId 为 null，
      // 此时才拿到真实 session_id，必须更新 streamingSessionId，否则切回该会话时
      // isStreaming 判断 (sessionId === streamingSessionId) 会因 streamingSessionId
      // 仍为 null 而错误显示发送按钮
      streamingSessionId.value = currentSessionId.value
    } else if (eventType === 'token_usage') {
      sessionUsage.value.input_tokens = data.input_tokens || 0
      sessionUsage.value.output_tokens = data.output_tokens || 0
      sessionUsage.value.total_tokens = data.session_estimate || data.total_tokens || 0
      sessionUsage.value.context_tokens = data.context_tokens || 0
      if (data.max_input_tokens) sessionUsage.value.max_input_tokens = data.max_input_tokens
      if (data.auto_compress_tokens) sessionUsage.value.auto_compress_tokens = data.auto_compress_tokens
      // 实时更新会话耗时和迭代次数（基于本次请求开始前的基线值累加）
      if (typeof data.elapsed_time === 'number') {
        sessionDuration.value = Math.round((preStreamDuration + data.elapsed_time) * 10) / 10
      }
      if (typeof data.step_count === 'number') {
        iterationCount.value = preStreamIterationCount + data.step_count
      }
    } else if (eventType === 'thinking_start') {
      // Always create a new thinking block for each step
      currentThinking = ''
      currentBlock = null
      addBlock('thinking', { content: '', step: step || 0 })
    } else if (eventType === 'thinking') {
      currentThinking += content || ''
      if (currentBlock && currentBlock.type === 'thinking') {
        currentBlock.content = currentThinking
        // Trigger reactivity immediately for each thinking chunk
        const idx = messages.value.findIndex(m => m.id === assistantMsgId)
        if (idx !== -1) {
          messages.value[idx] = { ...messages.value[idx] }
        }
      }
    } else if (eventType === 'thinking_end') {
      // Always trigger reactivity update on thinking end
      const idx = messages.value.findIndex(m => m.id === assistantMsgId)
      if (idx !== -1) {
        messages.value[idx] = { ...messages.value[idx], blocks: [...messages.value[idx].blocks] }
      }
      totalThinkingDuration += duration || 0
      updateThinkingDuration(duration || 0, step || 0)
      // Reset for next thinking step
      currentThinking = ''
      currentBlock = null
    } else if (eventType === 'content') {
      if (!currentBlock || currentBlock.type !== 'content') {
        currentContent = content || ''
        currentBlock = null
        addBlock('content', { content: currentContent })
      } else {
        currentContent += content || ''
        currentBlock.content = currentContent
        const idx = messages.value.findIndex(m => m.id === assistantMsgId)
        if (idx !== -1) {
          messages.value[idx] = { ...messages.value[idx], blocks: [...messages.value[idx].blocks] }
        }
      }
    } else if (eventType === 'content_end') {
      currentContent = ''
      currentBlock = null
    } else if (eventType === 'todo_list') {
      // Update todo list from write_todos tool call
      if (data.todos && Array.isArray(data.todos)) {
        currentTodos.value = data.todos
      }
    } else if (eventType === 'assistant_start') {
      ensureAssistantMessage()
      currentContent = ''
      currentThinking = ''
      currentToolCalls = []
      currentBlock = null
    } else if (eventType === 'user_input_required') {
      if (assistantMsgId) {
        const idx = messages.value.findIndex(m => m.id === assistantMsgId)
        if (idx !== -1) {
          messages.value[idx].loading = false
          messages.value[idx] = { ...messages.value[idx] }
        }
      }
    } else if (eventType === 'tool_call') {
      ensureAssistantMessage()
      const idx = messages.value.findIndex(m => m.id === assistantMsgId)
      if (idx !== -1) {
        const callId = toolCallId || `tool-${tool_name}`
        // Match by tool_call_id to distinguish repeated calls to the same tool
        const existingBlockIdx = messages.value[idx].blocks.findIndex(
          b => b.type === 'tool_call' && b.id === callId
        )

        if (existingBlockIdx !== -1) {
          // Only update arguments if the new value is non-empty
          const newArgs = (args !== undefined && args !== null && !(typeof args === 'object' && Object.keys(args).length === 0))
            ? args
            : messages.value[idx].blocks[existingBlockIdx].arguments
          messages.value[idx].blocks[existingBlockIdx] = {
            ...messages.value[idx].blocks[existingBlockIdx],
            arguments: newArgs
          }
          currentBlock = messages.value[idx].blocks[existingBlockIdx]
          messages.value[idx] = { ...messages.value[idx], blocks: [...messages.value[idx].blocks] }
        } else {
          // New tool call
          currentBlock = null
          const toolCall = {
            tool_call_id: callId,
            tool_name: tool_name || '',
            arguments: (args !== undefined && args !== null) ? args : {},
            result: '',
            success: true
          }
          currentToolCalls.push(toolCall)
          addBlock('tool_call', {
            id: callId,
            tool_name: tool_name || '',
            arguments: (args !== undefined && args !== null) ? args : {},
            result: '',
            success: true,
            step: step || 0
          })
        }
      }
    } else if (eventType === 'tool_result') {
      const callId = toolCallId || `tool-${tool_name}`
      const toolDuration = duration != null ? duration : 0
      console.log('[tool_result] event received:', { callId, tool_name, toolDuration, resultLen: (result || '').length, success, hasArgs: args !== undefined })

      if (currentToolCalls.length > 0) {
        const matchingCall = currentToolCalls.find(tc => tc.tool_call_id === callId)
        if (matchingCall) {
          matchingCall.result = result || ''
          matchingCall.success = success !== false
          matchingCall.duration = toolDuration
          if (args !== undefined && args !== null) matchingCall.arguments = args
        } else {
          const lastToolCall = currentToolCalls[currentToolCalls.length - 1]
          lastToolCall.result = result || ''
          lastToolCall.success = success !== false
          lastToolCall.duration = toolDuration
          if (args !== undefined && args !== null) lastToolCall.arguments = args
        }
      }

      const idx = messages.value.findIndex(m => m.id === assistantMsgId)
      if (idx !== -1) {
        // Helper: try to match a block for this tool result
        function findToolBlock(blocks, id, name) {
          // 1. Try exact id match
          const byId = blocks.findIndex(b => b.type === 'tool_call' && b.id === id)
          if (byId !== -1) return byId
          // 2. Try tool_name match
          if (name) {
            const byName = blocks.findIndex(b => b.type === 'tool_call' && b.tool_name === name)
            if (byName !== -1) return byName
            const byNameCI = blocks.findIndex(b => b.type === 'tool_call' && b.tool_name && b.tool_name.toLowerCase() === name.toLowerCase())
            if (byNameCI !== -1) return byNameCI
          }
          // 3. Try pending (no duration) block
          const byPending = blocks.findIndex(b => b.type === 'tool_call' && b.duration == null)
          if (byPending !== -1) return byPending
          return -1
        }

        const blockIdx = findToolBlock(messages.value[idx].blocks, callId, tool_name)
        if (blockIdx !== -1) {
          console.log('[tool_result] matched block at index:', blockIdx, 'tool_name:', messages.value[idx].blocks[blockIdx].tool_name)
          const newArgs = (args !== undefined && args !== null && !(typeof args === 'object' && Object.keys(args).length === 0))
            ? args
            : messages.value[idx].blocks[blockIdx].arguments
          // Parse MCP result format to extract clean text
          const cleanResult = parseMCPResult(result || '')
          messages.value[idx].blocks[blockIdx] = {
            ...messages.value[idx].blocks[blockIdx],
            arguments: newArgs,
            result: cleanResult,
            success: success !== false,
            duration: toolDuration
          }
          // Also set the block's id if it was a fallback match
          if (!messages.value[idx].blocks[blockIdx].id) {
            messages.value[idx].blocks[blockIdx].id = callId
          }
          messages.value[idx] = { ...messages.value[idx], blocks: [...messages.value[idx].blocks] }
        } else {
          console.warn('[tool_result] NO matching block found! callId:', callId, 'tool_name:', tool_name, 'blocks:', messages.value[idx].blocks.map(b => ({ type: b.type, id: b.id, tool_name: b.tool_name, duration: b.duration, resultLen: (b.result || '').length })))
        }
      }
      // Don't reset currentBlock if it's a content block, to avoid splitting the text stream
      if (!currentBlock || currentBlock.type !== 'content') {
        currentBlock = null
      }
    } else if (eventType === 'done') {
      const finalContent = data.content || currentContent
      if (assistantMsgId) {
        const idx = messages.value.findIndex(m => m.id === assistantMsgId)
        if (idx !== -1) {
          messages.value[idx].content = finalContent
          messages.value[idx].thinking = currentThinking
          messages.value[idx].tool_calls = currentToolCalls
          messages.value[idx].loading = false
          messages.value[idx].created_at = new Date().toISOString()
          if (data.usage) {
            messages.value[idx].usage = {
              input_tokens: data.usage.input_tokens || 0,
              output_tokens: data.usage.output_tokens || 0,
              total_tokens: data.usage.total_tokens || 0,
            }
          }
          messages.value[idx] = { ...messages.value[idx] }
        }
      }

      if (data.usage) {
        console.log('[Token Usage] Received usage:', data.usage, 'preStreamUsage:', preStreamUsage)
        sessionUsage.value.input_tokens = (preStreamUsage.input_tokens || 0) + (data.usage.input_tokens || 0)
        sessionUsage.value.output_tokens = (preStreamUsage.output_tokens || 0) + (data.usage.output_tokens || 0)
      sessionUsage.value.total_tokens = data.usage.session_estimate || (preStreamUsage.total_tokens || 0) + (data.usage.total_tokens || 0)
        sessionUsage.value.context_tokens = data.usage.context_tokens || 0
        if (data.usage.max_input_tokens) sessionUsage.value.max_input_tokens = data.usage.max_input_tokens
        if (data.usage.auto_compress_tokens) sessionUsage.value.auto_compress_tokens = data.usage.auto_compress_tokens
        console.log('[Token Usage] Updated sessionUsage:', sessionUsage.value)
      } else {
        console.log('[Token Usage] No usage data in done event')
      }

      // 基于preStream基线值设置最终耗时和迭代次数（与token_usage事件逻辑一致，幂等，避免双重计数）
      if (typeof data.elapsed_time === 'number') {
        sessionDuration.value = Math.round((preStreamDuration + data.elapsed_time) * 10) / 10
      }
      if (data.usage && typeof data.usage.step_count === 'number') {
        iterationCount.value = preStreamIterationCount + data.usage.step_count
      }

      if (title) {
        const sessionTitle = title
        const existingIdx = sessions.value.findIndex(s => s.session_id === currentSessionId.value)
        if (existingIdx === -1) {
          const newSession = {
            session_id: currentSessionId.value,
            title: sessionTitle,
            created_at: new Date().toISOString(),
            message_count: messages.value.length
          }
          sessions.value = [newSession, ...sessions.value]
        } else {
          sessions.value[existingIdx] = { ...sessions.value[existingIdx], title: sessionTitle }
          sessions.value = [...sessions.value]
        }
      }
    } else if (eventType === 'error') {
      if (is_in_thinking) {
        // thinking will be ended by backend, but ensure frontend state is clean
      }
      if (assistantMsgId) {
        const idx = messages.value.findIndex(m => m.id === assistantMsgId)
        if (idx !== -1) {
          messages.value[idx].loading = false
          messages.value[idx].error = content || '处理失败'
          for (const blk of messages.value[idx].blocks) {
            if (blk.type === 'thinking' && blk.duration == null) {
              blk.duration = 0
            }
            if (blk.type === 'tool_call' && blk.duration == null) {
              blk.duration = 0
              blk.success = false
              if (!blk.result) blk.result = content || '执行中断'
            }
          }
          messages.value[idx] = { ...messages.value[idx], blocks: [...messages.value[idx].blocks] }
        }
      }
      error.value = content || '处理失败'
    } else if (eventType === 'approval_required') {
      // HITL: 文件删除审批请求
      pendingApproval.value = {
        threadId: data.thread_id,
        assistantMsgId,
      }
      // 将对应 tool_call block 标记为 pending_approval
      if (assistantMsgId && data.action_requests) {
        const idx = messages.value.findIndex(m => m.id === assistantMsgId)
        if (idx !== -1) {
          for (const ar of data.action_requests) {
            const blk = messages.value[idx].blocks.find(
              b => b.type === 'tool_call' && (b.tool_call_id === ar.tool_call_id || b.id === ar.tool_call_id)
            )
            if (blk) {
              blk.pending_approval = true
              if (ar.file_paths && ar.file_paths.length > 0) {
                blk.file_paths = ar.file_paths
              }
            }
          }
          messages.value[idx] = { ...messages.value[idx], blocks: [...messages.value[idx].blocks] }
        }
      }
    }
  }

  try {
    isStreaming.value = true
    streamingSessionId.value = currentSessionId.value
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

    await sendMessage(currentSessionId.value, message, onChunk, abortSignal, enableDeepThink, files, enableKnowledgeBase)

    await refreshSessionFiles(null, 500)
  } catch (e) {
    if (e.name === 'AbortError') {
      // Mark assistant message as complete (loading=false) so spinners stop
      if (assistantMsgId) {
        const idx = messages.value.findIndex(m => m.id === assistantMsgId)
        if (idx !== -1) {
          messages.value[idx].loading = false
          messages.value[idx].content = messages.value[idx].content || currentContent || ''
          messages.value[idx].created_at = new Date().toISOString()
          messages.value[idx] = { ...messages.value[idx] }
        }
      }
      return
    }
    console.error('发送消息失败:', e)
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
          blocks: []
        })
      }
    }
    error.value = e.message || '发送消息失败'
  } finally {
    // HITL: 若有审批待处理，保持 isStreaming=true（用户需先审批）
    if (!pendingApproval.value) {
      isStreaming.value = false
      currentAbortController.value = null

      const sid = streamingSessionId.value
      if (sid && sessionStates.value[sid]) {
        sessionStates.value[sid].isStreaming = false
        sessionStates.value[sid].abortController = null
      }
      streamingSessionId.value = null

      await loadSessions()
    }
  }
}

// HITL: 用户审批文件删除操作后恢复执行
async function handleToolApproval(decision) {
  if (!pendingApproval.value) return

  const { threadId, assistantMsgId } = pendingApproval.value
  const sessionId = currentSessionId.value

  const decisions = decision === 'approve'
    ? [{ type: 'approve' }]
    : [{ type: 'reject', message: '用户拒绝了此操作，请勿重试此删除命令。' }]

  // 清除 tool_call block 的 pending_approval 状态
  if (assistantMsgId) {
    const idx = messages.value.findIndex(m => m.id === assistantMsgId)
    if (idx !== -1) {
      for (const blk of messages.value[idx].blocks) {
        if (blk.pending_approval) {
          blk.pending_approval = false
        }
      }
      messages.value[idx] = { ...messages.value[idx], blocks: [...messages.value[idx].blocks] }
    }
  }

  // resume 专用的 onChunk 处理器：追加到已有的 assistant 消息
  let currentContent = ''
  let currentThinking = ''
  let is_in_thinking = false
  let currentContentBlockIdx = null

  function onChunk(data) {
    const eventType = data.type

    if (eventType === 'thinking_start') {
      is_in_thinking = true
      const idx = messages.value.findIndex(m => m.id === assistantMsgId)
      if (idx !== -1) {
        const blk = { type: 'thinking', order: (messages.value[idx].blocks.length), content: '', step: data.step, duration: null }
        currentContentBlockIdx = blk.order
        messages.value[idx].blocks.push(blk)
        messages.value[idx] = { ...messages.value[idx], blocks: [...messages.value[idx].blocks] }
      }
    } else if (eventType === 'thinking') {
      const idx = messages.value.findIndex(m => m.id === assistantMsgId)
      if (idx !== -1 && currentContentBlockIdx !== null) {
        currentThinking += data.content
        messages.value[idx].blocks[currentContentBlockIdx].content = currentThinking
        messages.value[idx] = { ...messages.value[idx], blocks: [...messages.value[idx].blocks] }
      }
    } else if (eventType === 'thinking_end') {
      is_in_thinking = false
      const idx = messages.value.findIndex(m => m.id === assistantMsgId)
      if (idx !== -1 && currentContentBlockIdx !== null) {
        messages.value[idx].blocks[currentContentBlockIdx].duration = data.duration
        messages.value[idx] = { ...messages.value[idx], blocks: [...messages.value[idx].blocks] }
      }
      currentContentBlockIdx = null
    } else if (eventType === 'content_start') {
      if (is_in_thinking) {
        is_in_thinking = false
      }
      const idx = messages.value.findIndex(m => m.id === assistantMsgId)
      if (idx !== -1) {
        const blk = { type: 'content', order: messages.value[idx].blocks.length, content: '', step: data.step }
        currentContentBlockIdx = blk.order
        messages.value[idx].blocks.push(blk)
        messages.value[idx] = { ...messages.value[idx], blocks: [...messages.value[idx].blocks] }
      }
    } else if (eventType === 'content') {
      const idx = messages.value.findIndex(m => m.id === assistantMsgId)
      if (idx !== -1) {
        if (currentContentBlockIdx === null) {
          const blk = { type: 'content', order: messages.value[idx].blocks.length, content: '', step: data.step }
          currentContentBlockIdx = blk.order
          messages.value[idx].blocks.push(blk)
        }
        currentContent += data.content
        messages.value[idx].content = (messages.value[idx].content || '') + data.content
        messages.value[idx].blocks[currentContentBlockIdx].content = currentContent
        messages.value[idx] = { ...messages.value[idx], blocks: [...messages.value[idx].blocks] }
      }
    } else if (eventType === 'content_end') {
      currentContentBlockIdx = null
    } else if (eventType === 'tool_call') {
      currentContentBlockIdx = null
      const idx = messages.value.findIndex(m => m.id === assistantMsgId)
      if (idx !== -1) {
        const existing = messages.value[idx].blocks.find(
          b => b.type === 'tool_call' && (b.tool_call_id === data.tool_call_id || b.id === data.tool_call_id)
        )
        if (!existing) {
          messages.value[idx].blocks.push({
            type: 'tool_call',
            order: messages.value[idx].blocks.length,
            tool_name: data.tool_name,
            tool_call_id: data.tool_call_id,
            id: data.tool_call_id,
            arguments: data.arguments,
            result: '',
            success: true,
            duration: null,
            step: data.step,
          })
          messages.value[idx] = { ...messages.value[idx], blocks: [...messages.value[idx].blocks] }
        }
      }
    } else if (eventType === 'tool_result') {
      const idx = messages.value.findIndex(m => m.id === assistantMsgId)
      if (idx !== -1) {
        const blk = messages.value[idx].blocks.find(
          b => b.type === 'tool_call' && (b.tool_call_id === data.tool_call_id || b.id === data.tool_call_id)
        )
        if (blk) {
          blk.result = data.result
          blk.success = data.success
          blk.duration = data.duration
        }
        messages.value[idx] = { ...messages.value[idx], blocks: [...messages.value[idx].blocks] }
      }
    } else if (eventType === 'token_usage') {
      sessionUsage.value.input_tokens = data.input_tokens || 0
      sessionUsage.value.output_tokens = data.output_tokens || 0
      sessionUsage.value.total_tokens = data.session_estimate || data.total_tokens || 0
      sessionUsage.value.context_tokens = data.context_tokens || 0
      if (data.max_input_tokens) sessionUsage.value.max_input_tokens = data.max_input_tokens
      if (data.auto_compress_tokens) sessionUsage.value.auto_compress_tokens = data.auto_compress_tokens
      if (typeof data.elapsed_time === 'number') {
        sessionDuration.value = Math.round((sessionDuration.value + data.elapsed_time) * 10) / 10
      }
      if (typeof data.step_count === 'number') {
        iterationCount.value = data.step_count
      }
    } else if (eventType === 'approval_required') {
      // 嵌套审批
      pendingApproval.value = {
        threadId: data.thread_id,
        assistantMsgId,
      }
      if (assistantMsgId && data.action_requests) {
        const idx = messages.value.findIndex(m => m.id === assistantMsgId)
        if (idx !== -1) {
          for (const ar of data.action_requests) {
            const blk = messages.value[idx].blocks.find(
              b => b.type === 'tool_call' && (b.tool_call_id === ar.tool_call_id || b.id === ar.tool_call_id)
            )
            if (blk) {
              blk.pending_approval = true
              if (ar.file_paths && ar.file_paths.length > 0) {
                blk.file_paths = ar.file_paths
              }
            }
          }
          messages.value[idx] = { ...messages.value[idx], blocks: [...messages.value[idx].blocks] }
        }
      }
    } else if (eventType === 'done') {
      const idx = messages.value.findIndex(m => m.id === assistantMsgId)
      if (idx !== -1) {
        messages.value[idx].loading = false
        messages.value[idx].created_at = new Date().toISOString()
        if (data.usage) {
          if (typeof data.usage.elapsed_time === 'number') {
            sessionDuration.value = Math.round((sessionDuration.value + data.usage.elapsed_time) * 10) / 10
          }
          if (typeof data.usage.step_count === 'number') {
            iterationCount.value = data.usage.step_count
          }
        }
        messages.value[idx] = { ...messages.value[idx] }
      }
    } else if (eventType === 'error') {
      const idx = messages.value.findIndex(m => m.id === assistantMsgId)
      if (idx !== -1) {
        messages.value[idx].loading = false
        messages.value[idx].error = data.content || '处理失败'
        messages.value[idx] = { ...messages.value[idx] }
      }
      error.value = data.content || '处理失败'
    }
  }

  try {
    const controller = new AbortController()
    currentAbortController.value = controller

    await resumeStream(sessionId, threadId, decisions, onChunk, controller.signal)

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
    pendingApproval.value = null
    isStreaming.value = false
    currentAbortController.value = null
    const sid = streamingSessionId.value
    if (sid && sessionStates.value[sid]) {
      sessionStates.value[sid].isStreaming = false
      sessionStates.value[sid].abortController = null
    }
    streamingSessionId.value = null
    saveCurrentSessionState()
    await loadSessions()
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
  isStreaming.value = false
  // Mark current assistant message as no longer loading (stops tool spinning)
  if (assistantMsgId) {
    const idx = messages.value.findIndex(m => m.id === assistantMsgId)
    if (idx !== -1) {
      messages.value[idx] = { ...messages.value[idx], loading: false }
    }
  }

  // 记录正在流式输出的会话 ID（用于通知后端取消）
  const sid = streamingSessionId.value || currentSessionId.value

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
  streamingSessionId.value = null

  // 通知后端取消正在运行的流式任务（中断 astream 执行）并清除 Agent 缓存
  if (sid) {
    authFetch(`${API_BASE_URL}/api/chat/cancel?session_id=${encodeURIComponent(sid)}`, {
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
  await loadUserProfile()
  if (!showWelcome.value) {
    await loadSessions()
    if (sessions.value.length > 0) {
      currentSessionId.value = sessions.value[0].session_id
      // 加载初始会话的消息
      try {
        const history = await getChatHistory(currentSessionId.value)
        messages.value = history.messages || []
        if (history.usage) {
          sessionUsage.value.input_tokens = history.usage.input_tokens || 0
          sessionUsage.value.output_tokens = history.usage.output_tokens || 0
          sessionUsage.value.total_tokens = history.usage.total_tokens || 0
          sessionUsage.value.context_tokens = history.usage.context_tokens || 0
          sessionDuration.value = history.usage.elapsed_time || 0
          iterationCount.value = history.usage.step_count || 0
        }
        if (history.max_input_tokens) {
          sessionUsage.value.max_input_tokens = history.max_input_tokens
        }
      } catch (e) {
        console.error('加载聊天历史失败:', e)
      }
    }
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

<!-- 非 scoped 主题样式：:root 选择器在 scoped 中无法匹配 <html> 元素 -->
<style>
/* 深色主题 CSS 变量定义 */
:root[data-theme="dark"] {
  --bg-primary: #000000;
  --bg-secondary: #1a1a1a;
  --bg-tertiary: #2a2a2a;
  --bg-surface: #1a1a1a;
  --text-primary: #ffffff;
  --text-secondary: #ffffff;
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

html[data-theme="dark"] .knowledge-base-btn {
  background: var(--bg-tertiary) !important;
  border-color: var(--border-color) !important;
}

html[data-theme="dark"] .knowledge-base-btn svg {
  color: var(--text-secondary) !important;
}

html[data-theme="dark"] .knowledge-base-label {
  color: var(--text-secondary) !important;
}

html[data-theme="dark"] .knowledge-base-btn:hover:not(.disabled) {
  background: rgba(124, 106, 239, 0.15) !important;
  border-color: rgba(124, 106, 239, 0.4) !important;
}

html[data-theme="dark"] .knowledge-base-btn:hover:not(.disabled) svg,
html[data-theme="dark"] .knowledge-base-btn:hover:not(.disabled) .knowledge-base-label {
  color: #a78bfa !important;
}

html[data-theme="dark"] .knowledge-base-btn.active {
  background: linear-gradient(135deg, #7c6aef 0%, #6d28d9 100%) !important;
  border-color: transparent !important;
}

html[data-theme="dark"] .knowledge-base-btn.active svg,
html[data-theme="dark"] .knowledge-base-btn.active .knowledge-base-label {
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

html[data-theme="dark"] .wp-collapse-btn,
html[data-theme="dark"] .wp-refresh-btn {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .wp-collapse-btn:hover,
html[data-theme="dark"] .wp-refresh-btn:hover:not(:disabled) {
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
html[data-theme="dark"] .expand-workspace-btn,
html[data-theme="dark"] .expand-sidebar-btn {
  background: var(--bg-secondary) !important;
  border-color: var(--border-color) !important;
}

html[data-theme="dark"] .expand-workspace-btn svg,
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

html[data-theme="dark"] .knowledge-base-btn {
  background: var(--bg-tertiary) !important;
  border-color: var(--border-color) !important;
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .knowledge-base-btn svg,
html[data-theme="dark"] .knowledge-base-btn .knowledge-base-label {
  color: var(--text-primary) !important;
}

html[data-theme="dark"] .knowledge-base-btn.active {
  background: rgba(124, 106, 239, 0.15) !important;
  border-color: rgba(124, 106, 239, 0.3) !important;
  color: var(--accent-color) !important;
}

html[data-theme="dark"] .knowledge-base-btn.active svg,
html[data-theme="dark"] .knowledge-base-btn.active .knowledge-base-label {
  color: var(--accent-color) !important;
}

html[data-theme="dark"] .knowledge-base-btn.active {
  background: rgba(124, 106, 239, 0.15) !important;
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

/* 快捷卡片 */
html[data-theme="dark"] .quick-card {
  background: var(--bg-secondary) !important;
  border-color: var(--border-color) !important;
}

html[data-theme="dark"] .quick-card span {
  color: var(--text-primary) !important;
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

html[data-theme="dark"] .todo-content.line-through {
  color: var(--text-secondary) !important;
}

html[data-theme="dark"] .todo-item.completed .todo-content {
  color: var(--text-secondary) !important;
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
html[data-theme="dark"] .chat-input-container .input-box,
html[data-theme="dark"] .knowledge-base-btn,
html[data-theme="dark"] .upload-btn,
html[data-theme="dark"] .send-btn,
html[data-theme="dark"] .assets-header,
html[data-theme="dark"] .assets-panel .tabs,
html[data-theme="dark"] .assets-panel .tab,
html[data-theme="dark"] .assets-panel .upload-btn,
html[data-theme="dark"] .assets-panel .refresh-btn,
html[data-theme="dark"] .assets-panel .file-card,
html[data-theme="dark"] .assets-panel .dropdown-menu,
html[data-theme="dark"] .skill-center-header,
html[data-theme="dark"] .skill-center .tabs,
html[data-theme="dark"] .skill-center .tab,
html[data-theme="dark"] .skill-card,
html[data-theme="dark"] .add-icon-btn,
html[data-theme="dark"] .popover-card,
html[data-theme="dark"] .popover-btn,
html[data-theme="dark"] .workspace-panel,
html[data-theme="dark"] .wp-header,
html[data-theme="dark"] .mcp-card,
html[data-theme="dark"] .prompt-content,
html[data-theme="dark"] .settings-modal,
html[data-theme="dark"] .todo-panel,
html[data-theme="dark"] .todo-badge,
html[data-theme="dark"] .file-card,
html[data-theme="dark"] .quick-card,
html[data-theme="dark"] .scroll-btn {
  border-color: var(--border-color) !important;
}
</style>
