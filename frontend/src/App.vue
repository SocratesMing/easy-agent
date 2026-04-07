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
        :hasFiles="currentSessionHasFiles"
        :isStreaming="isStreaming"
        :scrollTrigger="scrollTrigger"
        @sendMessage="handleSendMessage"
        @createSession="ensureCurrentSession"
        @removeFile="handleRemoveFile"
        @stop="handleStop"
      />
      
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
const isSidebarCollapsed = ref(false)
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

  function addBlock(type, data) {
    ensureAssistantMessage()
    if (!currentBlock || currentBlock.type !== type) {
      currentBlock = { type, content: '', ...data }
      const idx = messages.value.findIndex(m => m.id === assistantMsgId)
      if (idx !== -1) {
        messages.value[idx].blocks.push(currentBlock)
      }
    } else {
      currentBlock.content = (currentBlock.content || '') + (data.content || '')
      if (data.tool_name) currentBlock.tool_name = data.tool_name
      if (data.arguments) currentBlock.arguments = data.arguments
      if (data.result !== undefined) currentBlock.result = data.result
      if (data.success !== undefined) currentBlock.success = data.success
      if (data.duration !== undefined) currentBlock.duration = data.duration
    }
    const idx = messages.value.findIndex(m => m.id === assistantMsgId)
    if (idx !== -1) {
      messages.value[idx] = { ...messages.value[idx] }
    }
  }

  function updateThinkingDuration(duration) {
    const idx = messages.value.findIndex(m => m.id === assistantMsgId)
    if (idx !== -1) {
      const blockIdx = messages.value[idx].blocks.findIndex(b => b.type === 'thinking')
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
      console.log('Updated thinking duration:', duration)
    }
  }

  isStreaming.value = true

  try {
    await sendMessage(currentSessionId.value, message, (data) => {
      const eventType = data.type || ''
      if (eventType === 'knowledge_base_start') {
        currentBlock = null
        addBlock('knowledge_base', { docs: [] })
      } else if (eventType === 'knowledge_base') {
        if (data.content) {
          console.log('[knowledge_base] content:', data.content)
        }
        if (data.file_name && data.score !== undefined) {
          const idx = messages.value.findIndex(m => m.id === assistantMsgId)
          if (idx !== -1) {
            const kbBlock = messages.value[idx].blocks.find(b => b.type === 'knowledge_base')
            if (kbBlock) {
              if (!kbBlock.docs) kbBlock.docs = []
              kbBlock.docs.push({
                file_name: data.file_name,
                score: data.score
              })
              messages.value[idx] = { ...messages.value[idx] }
            }
          }
        }
      } else if (eventType === 'knowledge_base_end') {
        console.log('[knowledge_base_end] docs_count:', data.docs_count)
      } else if (eventType === 'error') {
        error.value = data.content || '发送消息失败'
      } else if (eventType === 'start') {
        console.log('[start] Received data:', JSON.stringify(data))
        if (data.session_id) {
          currentSessionId.value = data.session_id
          const existingIdx = sessions.value.findIndex(s => s.session_id === data.session_id)
          console.log('[start] session_id:', data.session_id, 'existingIdx:', existingIdx, 'title:', data.title)
          const sessionTitle = data.title || (message.substring(0, 12) + '...') || '新会话'
          if (existingIdx === -1) {
            const newSession = {
              session_id: data.session_id,
              title: sessionTitle,
              created_at: new Date().toISOString()
            }
            sessions.value = [newSession, ...sessions.value]
            console.log('[start] Added new session:', newSession, 'total sessions:', sessions.value.length)
          } else {
            sessions.value[existingIdx] = { ...sessions.value[existingIdx], title: sessionTitle }
            sessions.value = [...sessions.value]
            console.log('[start] Updated existing session title:', sessionTitle)
          }
        }
      } else if (eventType === 'thinking') {
        console.log('[thinking] 收到思考内容:', data.content)
        currentThinking += data.content || ''
        addBlock('thinking', { content: data.content || '' })
        const idx = messages.value.findIndex(m => m.id === assistantMsgId)
        if (idx !== -1) {
          messages.value[idx] = { ...messages.value[idx], thinking: currentThinking }
          console.log('[thinking] blocks:', messages.value[idx].blocks)
        }
      } else if (eventType === 'thinking_end') {
        console.log('Received thinking_end event:', JSON.stringify(data))
        if (data.duration !== undefined) {
          updateThinkingDuration(data.duration)
        } else {
          console.warn('thinking_end event has no duration:', data)
        }
      } else if (eventType === 'content') {
        currentContent += data.content || ''
        addBlock('content', { content: data.content || '' })
        const idx = messages.value.findIndex(m => m.id === assistantMsgId)
        if (idx !== -1) {
          messages.value[idx] = { ...messages.value[idx], content: currentContent }
        }
      } else if (eventType === 'tool_call') {
        const toolCall = {
          tool_name: data.tool_name || '',
          arguments: data.arguments || {},
          result: '',
          success: true
        }
        currentToolCalls.push(toolCall)
        addBlock('tool_call', { 
          tool_name: data.tool_name || '', 
          arguments: data.arguments || {},
          content: ''
        })
        const idx = messages.value.findIndex(m => m.id === assistantMsgId)
        if (idx !== -1) {
          messages.value[idx] = { ...messages.value[idx], tool_calls: [...currentToolCalls] }
        }
      } else if (eventType === 'tool_result') {
        if (currentToolCalls.length > 0) {
          const lastTool = currentToolCalls[currentToolCalls.length - 1]
          lastTool.result = data.result || ''
          lastTool.success = data.success !== false
          addBlock('tool_result', { 
            tool_name: data.tool_name || '',
            result: data.result || '',
            success: data.success !== false,
            duration: data.duration,
            content: data.result || ''
          })
          const idx = messages.value.findIndex(m => m.id === assistantMsgId)
          if (idx !== -1) {
            messages.value[idx] = { ...messages.value[idx], tool_calls: [...currentToolCalls] }
          }
        }
      } else if (eventType === 'done') {
        const finalContent = data.content || currentContent
        if (assistantMsgId) {
          const idx = messages.value.findIndex(m => m.id === assistantMsgId)
          if (idx !== -1) {
            const thinkingBlock = messages.value[idx].blocks.find(b => b.type === 'thinking')
            if (data.thinking_duration && thinkingBlock) {
              thinkingBlock.duration = data.thinking_duration
            }
            messages.value[idx] = {
              ...messages.value[idx],
              content: finalContent,
              created_at: new Date().toISOString(),
              loading: false,
              thinking_duration: data.thinking_duration || messages.value[idx].thinking_duration,
              generated_files: data.generated_files || []
            }
          }
        }
        if (data.session_id) {
          currentSessionId.value = data.session_id
          const existingIdx = sessions.value.findIndex(s => s.session_id === data.session_id)
          const sessionTitle = data.title || (message.substring(0, 12) + '...') || '新会话'
          if (existingIdx === -1) {
            const newSession = {
              session_id: data.session_id,
              title: sessionTitle,
              created_at: new Date().toISOString()
            }
            sessions.value = [newSession, ...sessions.value]
            console.log('[done] Added new session:', newSession)
          } else {
            sessions.value[existingIdx] = { ...sessions.value[existingIdx], title: sessionTitle }
            sessions.value = [...sessions.value]
            console.log('[done] Updated existing session title:', sessionTitle)
          }
        }
      }
    }, signal, enableDeepThink, files, enableKnowledgeBase)
    
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
    await loadSessions()
  }
}

function handleStop() {
  isStreaming.value = false
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
