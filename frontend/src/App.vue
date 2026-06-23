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
      
      <UserProfile
        v-if="showUserProfile"
        @close="showUserProfile = false"
        @logout="handleLogout"
        @unregister="handleUnregister"
      />
      
      <SettingsPanel
        v-if="showSettingsPanel"
        @close="showSettingsPanel = false"
      />
      
      <Chat
        v-else-if="!showAssets && !showUserProfile && !showSkillCenter"
        :messages="messages"
        :currentSessionId="currentSessionId"
        :sessionCreatedAt="currentSessionCreatedAt"
        :isStreaming="isStreaming"
        :scrollTrigger="scrollTrigger"
        :sessionUsage="sessionUsage"
        :todos="currentTodos"
        :presetQuestions="presetQuestions"
        :workspaceExpanded="!isWorkspaceCollapsed"
        @sendMessage="handleSendMessage"
        @createSession="ensureCurrentSession"
        @removeFile="handleRemoveFile"
        @stop="handleStop"
        @retry="handleRetry"
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
        v-if="currentSessionId && isWorkspaceCollapsed && !showAssets && !showUserProfile && !showSkillCenter"
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

      <!-- 返回上一页按钮 -->
      <button 
        class="go-back-btn"
        @click="goBack"
        title="返回上一页"
      >
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M12 19V5M5 12l7-7 7 7"/>
        </svg>
      </button>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import SessionList from './components/SessionList.vue'
import Chat from './components/Chat.vue'
import AssetsPanel from './components/AssetsPanel.vue'
import SkillCenter from './components/SkillCenter.vue'
import UserProfile from './components/UserProfile.vue'
import Welcome from './components/Welcome.vue'
import WorkspacePanel from './components/WorkspacePanel.vue'
import SettingsPanel from './components/SettingsPanel.vue'
import { createSession, listSessions, getChatHistory, deleteSession, sendMessage, renameSession, togglePinSession } from './api/chat.js'
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
const sessionUsage = ref({ input_tokens: 0, output_tokens: 0, total_tokens: 0, max_input_tokens: null, auto_compress_tokens: null, context_tokens: 0 })
const currentTodos = ref([])
const presetQuestions = ref([])

// 保存当前会话状态到缓存
function saveCurrentSessionState() {
  if (!currentSessionId.value) return
  sessionStates.value[currentSessionId.value] = {
    messages: JSON.parse(JSON.stringify(messages.value)),
    isStreaming: isStreaming.value,
    sessionUsage: { ...sessionUsage.value },
    abortController: currentAbortController.value,
    todos: [...currentTodos.value]
  }
}

// 从缓存恢复会话状态
function restoreSessionState(sessionId) {
  const state = sessionStates.value[sessionId]
  if (state) {
    messages.value = state.messages
    isStreaming.value = state.isStreaming
    sessionUsage.value = { ...state.sessionUsage }
    currentAbortController.value = state.abortController
    currentTodos.value = state.todos || []
    return true
  }
  return false
}
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
const isSidebarCollapsed = ref(false)
const isWorkspaceCollapsed = ref(true)
const showAssets = ref(false)
const showSkillCenter = ref(false)
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

function handleShowAssets() {
  showAssets.value = !showAssets.value
  showSkillCenter.value = false
}

function handleShowSkillCenter() {
  showSkillCenter.value = !showSkillCenter.value
  showAssets.value = false
}

function handleShowProfile() {
  showUserProfile.value = true
  showAssets.value = false
  showSkillCenter.value = false
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
      } catch (e) {
        console.error('加载聊天历史失败:', e)
      }
    }
  }
}

function goBack() {
  if (showUserProfile.value) {
    showUserProfile.value = false
  } else if (showSkillCenter.value) {
    showSkillCenter.value = false
  } else if (showAssets.value) {
    showAssets.value = false
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
  refreshSessionFiles(null)
}

async function handleSelectSession(sessionId) {
  // 保存当前会话状态
  saveCurrentSessionState()

  showAssets.value = false
  showSkillCenter.value = false
  currentSessionId.value = sessionId

  // 尝试从缓存恢复会话状态
  if (restoreSessionState(sessionId)) {
    scrollTrigger.value++
    await refreshSessionFiles(sessionId)
    return
  }

  // 缓存中没有，从服务器加载历史
  sessionUsage.value = { input_tokens: 0, output_tokens: 0, total_tokens: 0, max_input_tokens: null, auto_compress_tokens: null, context_tokens: 0 }

  try {
    const history = await getChatHistory(sessionId)
    messages.value = history.messages || []
    currentTodos.value = history.todos || []
    // 从服务器返回的 usage 数据恢复 token 用量
    if (history.usage) {
      sessionUsage.value.input_tokens = history.usage.input_tokens || 0
      sessionUsage.value.output_tokens = history.usage.output_tokens || 0
      sessionUsage.value.total_tokens = history.usage.total_tokens || 0
      sessionUsage.value.context_tokens = history.usage.context_tokens || 0
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
    } else if (eventType === 'token_usage') {
      sessionUsage.value.input_tokens = data.input_tokens || 0
      sessionUsage.value.output_tokens = data.output_tokens || 0
      sessionUsage.value.total_tokens = data.session_estimate || data.total_tokens || 0
      sessionUsage.value.context_tokens = data.context_tokens || 0
      if (data.max_input_tokens) sessionUsage.value.max_input_tokens = data.max_input_tokens
      if (data.auto_compress_tokens) sessionUsage.value.auto_compress_tokens = data.auto_compress_tokens
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
    isStreaming.value = false
    currentAbortController.value = null

    // 更新正在流式输出的会话的缓存状态（可能已切换到其他会话）
    const sid = streamingSessionId.value
    if (sid && sessionStates.value[sid]) {
      sessionStates.value[sid].isStreaming = false
      sessionStates.value[sid].abortController = null
    }
    streamingSessionId.value = null

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
  // 中止正在进行的 fetch 请求
  if (currentAbortController.value) {
    currentAbortController.value.abort()
    currentAbortController.value = null
  }

  // 更新会话缓存状态
  const sid = streamingSessionId.value || currentSessionId.value
  if (sid && sessionStates.value[sid]) {
    sessionStates.value[sid].isStreaming = false
    sessionStates.value[sid].abortController = null
  }
  streamingSessionId.value = null

  // 通知后端清除agent缓存
  if (currentSessionId.value) {
    fetch(`${API_BASE_URL}/api/chat/session/${currentSessionId.value}/agent`, {
      method: 'DELETE',
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
</style>
