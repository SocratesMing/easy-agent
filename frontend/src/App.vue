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
        @toggleSidebar="toggleSidebar"
        @showAssets="handleShowAssets"
        @showProfile="handleShowProfile"
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
      
      <UserProfile
        v-if="showUserProfile"
        @close="showUserProfile = false"
        @logout="handleLogout"
        @unregister="handleUnregister"
      />
      
      <Chat
        v-else-if="!showAssets && !showUserProfile"
        :messages="messages"
        :currentSessionId="currentSessionId"
        :isStreaming="isStreaming"
        :scrollTrigger="scrollTrigger"
        @sendMessage="handleSendMessage"
        @createSession="ensureCurrentSession"
        @removeFile="handleRemoveFile"
        @stop="handleStop"
        @retry="handleRetry"
      />

      <WorkspacePanel
        v-if="!showAssets && !showUserProfile"
        :username="userProfile.username"
        :currentSessionId="currentSessionId"
        :isStreaming="isStreaming"
        :visible="!isWorkspaceCollapsed"
        @toggle="isWorkspaceCollapsed = !isWorkspaceCollapsed"
      />

      <button
        v-if="isWorkspaceCollapsed && !showAssets && !showUserProfile"
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
import { ref, onMounted } from 'vue'
import SessionList from './components/SessionList.vue'
import Chat from './components/Chat.vue'
import AssetsPanel from './components/AssetsPanel.vue'
import UserProfile from './components/UserProfile.vue'
import Welcome from './components/Welcome.vue'
import WorkspacePanel from './components/WorkspacePanel.vue'
import { createSession, listSessions, getChatHistory, deleteSession, sendMessage, renameSession } from './api/chat.js'
import { uploadFile, deleteFile, getUserProfile, getSessionGeneratedFiles } from './api/files.js'
import { logout as apiLogout, getStoredToken, getStoredUsername, AUTH_EXPIRED_EVENT } from './api/auth.js'

const sessions = ref([])
const currentSessionId = ref(null)
const currentSessionHasFiles = ref(false)
let filesCheckTimer = null

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
const error = ref(null)
const currentAbortController = ref(null)
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
const isSidebarCollapsed = ref(false)
const isWorkspaceCollapsed = ref(true)
const showAssets = ref(false)
const showUserProfile = ref(false)
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
}

function handleShowProfile() {
  showUserProfile.value = true
  showAssets.value = false
}

async function handleWelcomeCompleted(profile) {
  userProfile.value = profile
  showWelcome.value = false
  await loadSessions()
  if (sessions.value.length > 0) {
    currentSessionId.value = sessions.value[0].session_id
  }
}

function goBack() {
  if (showUserProfile.value) {
    showUserProfile.value = false
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
  showAssets.value = false
  currentSessionId.value = null
  messages.value = []
  refreshSessionFiles(null)
}

async function handleSelectSession(sessionId) {
  showAssets.value = false
  currentSessionId.value = sessionId
  
  try {
    const history = await getChatHistory(sessionId)
    messages.value = history.messages || []
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
    
    if (currentSessionId.value === sessionId) {
      currentSessionId.value = sessions.value[0]?.session_id || null
      messages.value = []
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

async function handleSendMessage(message, files = [], signal, enableDeepThink = true, enableKnowledgeBase = false) {
  const userMsgId = `user-${Date.now()}`

  let contentWithFiles = message.trim().replace(/\s+/g, ' ')

  // 清除前一条助手消息的 user_input_required 状态
  if (messages.value.length > 0) {
    const lastMsg = messages.value[messages.value.length - 1]
    if (lastMsg.role === 'assistant' && lastMsg.user_input_required) {
      lastMsg.user_input_required = false
    }
  }

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

  let assistantMsgId = null
  let assistantMessageCreated = false

  function ensureAssistantMessage() {
    if (!assistantMessageCreated) {
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
      assistantMessageCreated = true
    }
  }

  let currentThinking = ''
  let currentContent = ''
  let currentToolCalls = []
  let currentBlock = null
  let blockOrderCounter = 0

  function addBlock(type, data, replace = false) {
    ensureAssistantMessage()
    blockOrderCounter++
    if (!currentBlock || currentBlock.type !== type) {
      currentBlock = { type, content: '', order: blockOrderCounter, ...data }
      const idx = messages.value.findIndex(m => m.id === assistantMsgId)
      if (idx !== -1) {
        messages.value[idx].blocks.push(currentBlock)
        messages.value[idx] = { ...messages.value[idx], blocks: [...messages.value[idx].blocks] }
      }
    } else {
      if (replace) {
        currentBlock.content = data.content || ''
      } else {
        currentBlock.content = (currentBlock.content || '') + (data.content || '')
      }
      if (data.tool_name) currentBlock.tool_name = data.tool_name
      if (data.arguments) currentBlock.arguments = data.arguments
      if (data.result !== undefined) currentBlock.result = data.result
      if (data.success !== undefined) currentBlock.success = data.success
      if (data.duration !== undefined) currentBlock.duration = data.duration
      if (data.step !== undefined) currentBlock.step = data.step
      const idx = messages.value.findIndex(m => m.id === assistantMsgId)
      if (idx !== -1) {
        messages.value[idx] = { ...messages.value[idx] }
      }
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
    const { type: eventType, content, thinking, tool_calls, duration, step, tool_name, arguments: args, result, success, title } = data

    if (eventType === 'thinking_start') {
      currentBlock = null
      addBlock('thinking', { content: '', step: step || 0 })
    } else if (eventType === 'thinking') {
      currentThinking += content || ''
      if (currentBlock && currentBlock.type === 'thinking') {
        currentBlock.content = currentThinking
        const idx = messages.value.findIndex(m => m.id === assistantMsgId)
        if (idx !== -1) {
          messages.value[idx] = { ...messages.value[idx], blocks: [...messages.value[idx].blocks] }
        }
      }
    } else if (eventType === 'thinking_end') {
      updateThinkingDuration(duration || 0, step || 0)
      currentThinking = ''
    } else if (eventType === 'content') {
      currentContent += content || ''
      
      // 更新或创建 content block，传入累积的完整内容（使用 replace 模式避免重复）
      addBlock('content', { content: currentContent }, true)
      
      const idx = messages.value.findIndex(m => m.id === assistantMsgId)
      if (idx !== -1) {
        const msg = messages.value[idx]
        messages.value[idx] = {
          ...messages.value[idx],
          content: currentContent
        }
        
        if (!currentBlock || currentBlock.type !== 'content') {
          currentBlock = null
          addBlock('content', { content: currentContent })
        } else {
          currentBlock.content = currentContent
          messages.value[idx] = { ...messages.value[idx], blocks: [...messages.value[idx].blocks] }
        }
      }
    } else if (eventType === 'content_end') {
      currentContent = ''
    } else if (eventType === 'user_input_required') {
      // 等待用户输入：消息完成但需要用户确认/选择
      ensureAssistantMessage()
      const idx = messages.value.findIndex(m => m.id === assistantMsgId)
      if (idx !== -1) {
        messages.value[idx].content = currentContent
        messages.value[idx].loading = false
        messages.value[idx].user_input_required = true
        messages.value[idx].user_input_question = content || currentContent
        messages.value[idx].created_at = new Date().toISOString()
        messages.value[idx] = { ...messages.value[idx] }
      }
      isStreaming.value = false
    } else if (eventType === 'tool_call') {
      const idx = messages.value.findIndex(m => m.id === assistantMsgId)
      if (idx !== -1) {
        // 查找已存在的同名 tool_call block
        const existingBlockIdx = messages.value[idx].blocks.findIndex(
          b => b.type === 'tool_call' && b.tool_name === tool_name
        )
        
        if (existingBlockIdx !== -1) {
          // 更新已存在的 block
          const existingBlock = messages.value[idx].blocks[existingBlockIdx]
          existingBlock.arguments = args || {}
          currentBlock = existingBlock
          messages.value[idx] = { ...messages.value[idx], blocks: [...messages.value[idx].blocks] }
          
          // 同时更新 currentToolCalls 中的最后一个匹配项
          if (currentToolCalls.length > 0) {
            const lastToolCallIdx = [...currentToolCalls].reverse().findIndex(tc => tc.tool_name === tool_name)
            if (lastToolCallIdx !== -1) {
              const actualIdx = currentToolCalls.length - 1 - lastToolCallIdx
              currentToolCalls[actualIdx].arguments = args || {}
            }
          }
        } else {
          // 创建新 block
          currentBlock = null
          const toolCall = {
            tool_name: tool_name || '',
            arguments: args || {},
            result: '',
            success: true
          }
          currentToolCalls.push(toolCall)
          const toolCallId = `tool-${Date.now()}-${tool_name}`
          addBlock('tool_call', { 
            id: toolCallId,
            tool_name: tool_name || '', 
            arguments: args || {},
            result: '',
            success: true,
            step: step || 0
          })
        }
      }
    } else if (eventType === 'tool_result') {
      if (currentToolCalls.length > 0) {
        const lastToolCall = currentToolCalls[currentToolCalls.length - 1]
        lastToolCall.arguments = arguments || {}
        lastToolCall.result = result || ''
        lastToolCall.success = success !== false
      }
      
      currentBlock = null
      addBlock('tool_result', {
        tool_name: tool_name || '',
        arguments: arguments || {},
        result: result || '',
        success: success !== false,
        duration: duration,
        step: step || 0
      })
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
          messages.value[idx] = { ...messages.value[idx] }
        }
      }

      if (title) {
        const sessionTitle = title
        const existingIdx = sessions.value.findIndex(s => s.id === currentSessionId.value)
        if (existingIdx === -1) {
          const newSession = {
            id: currentSessionId.value,
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
    }
  }

  try {
    isStreaming.value = true
    const controller = new AbortController()
    currentAbortController.value = controller
    const abortSignal = signal || controller.signal
    await sendMessage(currentSessionId.value, message, onChunk, abortSignal, enableDeepThink, files, enableKnowledgeBase)

    await refreshSessionFiles(null, 500)
  } catch (e) {
    if (e.name === 'AbortError') {
      return
    }
    console.error('发送消息失败:', e)
    if (assistantMsgId) {
      messages.value = messages.value.filter(m => m.id !== assistantMsgId)
    }
    error.value = e.message || '发送消息失败'
  } finally {
    isStreaming.value = false
    currentAbortController.value = null
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
  // 中止正在进行的 fetch 请求
  if (currentAbortController) {
    currentAbortController.abort()
    currentAbortController = null
  }
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
  box-shadow: 0 4px 12px rgba(220, 38, 38, 0.2);
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
