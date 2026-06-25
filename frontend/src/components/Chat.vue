<template>
  <div class="chat-container">
    <div class="chat-main">
    <TodoListPanel
      v-if="!sidebarCollapsed"
      :todos="todos"
    />
    <div class="chat-messages" ref="messagesRef">
      <div v-if="sessionCreatedAt && messages.length > 0" class="session-created-time">
        {{ formatSessionTime(sessionCreatedAt) }}
      </div>
      <div v-if="messages.length === 0" class="welcome-screen">
        <div class="welcome-icon">
          <EasyLogo :size="64" />
        </div>
        <h2>{{ displayedTitle }}</h2>
        <p>{{ displayedSubtitle }}</p>
        <div class="quick-actions">
          <div
            v-for="(question, index) in presetQuestions"
            :key="index"
            class="quick-card"
            :title="question"
            @click="handleQuickAction(question)"
          >
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
              <polyline points="14 2 14 8 20 8"></polyline>
              <line x1="16" y1="13" x2="8" y2="13"></line>
              <line x1="16" y1="17" x2="8" y2="17"></line>
              <polyline points="10 9 9 9 8 9"></polyline>
            </svg>
            <span class="quick-card-text">{{ question }}</span>
          </div>
        </div>
      </div>
      
      <div
      v-for="(msg, index) in messages"
      :key="msg.id"
      :ref="el => setMessageRef(el, index)"
      class="message-wrapper"
      :class="msg.role"
    >
      <ChatMessage
        :message="msg"
        @removeFile="(file) => handleRemoveFile(file, index)"
        @retry="handleRetry"
      />
    </div>
    </div>
    
    <button v-if="canGoToNextUserMessage" @click="goToNextUserMessage" class="scroll-btn next" :class="{ shifted: props.workspaceExpanded }" title="回到下一个用户问题">
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <polyline points="6 9 12 15 18 9"></polyline>
      </svg>
    </button>
    <button v-if="canGoToPrevUserMessage" @click="goToPrevUserMessage" class="scroll-btn prev" :class="{ shifted: props.workspaceExpanded }" title="回到上一个用户问题">
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <polyline points="18 15 12 9 6 15"></polyline>
      </svg>
    </button>

    <ChatInput
      @send="handleSend"
      :disabled="isStreaming"
      :isStreaming="isStreaming"
      :session-id="currentSessionId"
      :sessionUsage="sessionUsage"
      @stop="handleStop"
      @createSession="handleCreateSession"
    />
    </div>
  </div>
</template>

<script setup>
import { ref, watch, nextTick, computed, onMounted, onUnmounted } from 'vue'
import ChatMessage from './ChatMessage.vue'
import ChatInput from './ChatInput.vue'
import TodoListPanel from './TodoListPanel.vue'
import EasyLogo from './EasyLogo.vue'

const welcomeTitle = '我是 Easy Agent'
const welcomeSubtitle = '简单易用的智能助手，有什么可以帮您？'
const displayedTitle = ref('')
const displayedSubtitle = ref('')
let titleTimer = null
let subtitleTimer = null

function startTypingEffect() {
  if (titleTimer) {
    clearInterval(titleTimer)
    titleTimer = null
  }
  if (subtitleTimer) {
    clearInterval(subtitleTimer)
    subtitleTimer = null
  }

  displayedTitle.value = welcomeTitle
  displayedSubtitle.value = ''

  let subtitleIndex = 0

  subtitleTimer = setInterval(() => {
    if (subtitleIndex < welcomeSubtitle.length) {
      displayedSubtitle.value += welcomeSubtitle[subtitleIndex]
      subtitleIndex++
    } else {
      clearInterval(subtitleTimer)
      subtitleTimer = null
    }
  }, 50)
}

onMounted(() => {
  startTypingEffect()
})

onUnmounted(() => {
  if (titleTimer) clearInterval(titleTimer)
  if (subtitleTimer) clearInterval(subtitleTimer)
})

const props = defineProps({
  messages: {
    type: Array,
    default: () => []
  },
  currentSessionId: {
    type: String,
    default: null
  },
  sessionCreatedAt: {
    type: String,
    default: null
  },
  isStreaming: {
    type: Boolean,
    default: false
  },
  scrollTrigger: {
    type: Number,
    default: 0
  },
  sessionUsage: {
    type: Object,
    default: () => ({ input_tokens: 0, output_tokens: 0, total_tokens: 0 })
  },
  todos: {
    type: Array,
    default: () => []
  },
  presetQuestions: {
    type: Array,
    default: () => []
  },
  workspaceExpanded: {
    type: Boolean,
    default: false
  },
  sidebarCollapsed: {
    type: Boolean,
    default: false
  },
})

watch(() => props.messages, (newMessages) => {
  if (newMessages.length === 0) {
    startTypingEffect()
  }
})

const emit = defineEmits(['sendMessage', 'stop', 'removeFile', 'createSession'])
const messagesRef = ref(null)

function formatSessionTime(isoStr) {
  if (!isoStr) return ''
  const d = new Date(isoStr)
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  const h = String(d.getHours()).padStart(2, '0')
  const min = String(d.getMinutes()).padStart(2, '0')
  return `${y}-${m}-${day} ${h}:${min}`
}
const messageRefs = ref({})
const currentUserMessageIndex = ref(-1)
const userMessageIndices = ref([])

function setMessageRef(el, index) {
  if (el) {
    messageRefs.value[index] = el
  }
}

function updateUserMessageIndices() {
  userMessageIndices.value = props.messages
    .map((msg, index) => msg.role === 'user' ? index : -1)
    .filter(index => index !== -1)
    .reverse()
  currentUserMessageIndex.value = -1
}

const canGoToPrevUserMessage = computed(() => {
  return userMessageIndices.value.length > 0 && currentUserMessageIndex.value < userMessageIndices.value.length - 1
})

const canGoToNextUserMessage = computed(() => {
  return currentUserMessageIndex.value > 0
})

onMounted(() => {
  updateUserMessageIndices()
})

function goToPrevUserMessage() {
  if (userMessageIndices.value.length === 0) return
  
  if (currentUserMessageIndex.value === -1) {
    currentUserMessageIndex.value = 0
  } else if (currentUserMessageIndex.value < userMessageIndices.value.length - 1) {
    currentUserMessageIndex.value++
  }
  
  const targetIndex = userMessageIndices.value[currentUserMessageIndex.value]
  
  if (targetIndex !== undefined && messageRefs.value[targetIndex]) {
    messageRefs.value[targetIndex].scrollIntoView({ behavior: 'smooth', block: 'center' })
  }
}

function goToNextUserMessage() {
  if (currentUserMessageIndex.value > 0) {
    currentUserMessageIndex.value--
    const targetIndex = userMessageIndices.value[currentUserMessageIndex.value]
    if (targetIndex !== undefined && messageRefs.value[targetIndex]) {
      messageRefs.value[targetIndex].scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }
}

function handleSend(message, files, signal, enableDeepThink = true, enableKnowledgeBase = false) {
  emit('sendMessage', message, files, signal, enableDeepThink, enableKnowledgeBase)
}

function handleRemoveFile(file, messageIndex) {
  // 从事件参数中获取file，然后从messages中获取对应的message
  const message = props.messages[messageIndex]
  emit('removeFile', message, messageIndex, file)
}

function handleRetry(content) {
  // 向上传递重试事件
  emit('retry', content)
}

function handleStop() {
  emit('stop')
}

function handleCreateSession() {
  emit('createSession')
}

function handleQuickAction(message) {
  emit('sendMessage', message, [], null, true, false)
}

function scrollToBottom() {
  nextTick(() => {
    if (messagesRef.value) {
      messagesRef.value.scrollTop = messagesRef.value.scrollHeight
    }
  })
}

watch(() => props.messages, (newMessages, oldMessages) => {
  if (props.isStreaming) {
    scrollToBottom()
  }
  nextTick(() => {
    updateUserMessageIndices()
  })
}, { deep: true })

watch(() => props.scrollTrigger, () => {
  scrollToBottom()
})
</script>

<style scoped>
.chat-container {
  flex: 1;
  display: flex;
  flex-direction: row;
  background: #ffffff;
  overflow: hidden;
}

.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-width: 0;
  position: relative;
}

.chat-header {
  padding: 12px 24px;
  border-bottom: 1px solid #e5e7eb;
  background: #f9fafb;
}

.generated-files-header-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  background: #f0fdf4;
  border: 1px solid #86efac;
  border-radius: 8px;
  color: #166534;
  font-size: 14px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.generated-files-header-btn:hover {
  background: #dcfce7;
  border-color: #4ade80;
}

.generated-files-header-btn svg {
  width: 18px;
  height: 18px;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
  scroll-behavior: smooth;
  display: flex;
  flex-direction: column;
}

.session-created-time {
  text-align: center;
  font-size: 12px;
  color: #94a3b8;
  padding: 4px 0 16px 0;
  user-select: none;
}

.welcome-screen {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #64748b;
}

.welcome-icon {
  margin-bottom: 20px;
}

.welcome-screen h2 {
  font-size: 24px;
  font-weight: 600;
  color: #1e293b;
  margin: 0 0 8px 0;
}

.welcome-screen p {
  font-size: 14px;
  color: #94a3b8;
  margin: 0;
}

.quick-actions {
  display: flex;
  gap: 16px;
  margin-top: 32px;
}

.quick-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px 20px;
  background: white;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  cursor: pointer;
  transition: all 0.2s ease;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
  width: 280px;
  min-width: 280px;
  max-width: 280px;
}

.quick-card:hover {
  border-color: #0ea5e9;
  background: #f0f9ff;
  box-shadow: 0 4px 12px rgba(14, 165, 233, 0.15);
  transform: translateY(-2px);
}

.quick-card svg {
  width: 24px;
  height: 24px;
  color: #0ea5e9;
  flex-shrink: 0;
}

.quick-card span {
  font-size: 14px;
  color: #334155;
  line-height: 1.5;
}

.quick-card-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.scroll-btn {
  position: fixed;
  right: 24px;
  width: 40px;
  height: 40px;
  border: none;
  background: white;
  border-radius: 50%;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.2s;
  z-index: 10;
}

.scroll-btn.prev {
  bottom: 120px;
}

.scroll-btn.next {
  bottom: 170px;
}


.scroll-btn.shifted {
  right: 284px; /* 260px panel + 24px original right */
}
.scroll-btn:hover {
  background: #f1f5f9;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}

.scroll-btn svg {
  width: 20px;
  height: 20px;
  color: #64748b;
}

.scroll-btn:hover svg {
  color: #0ea5e9;
}

.loading-container {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  justify-content: center;
  padding: 12px 0;
  gap: 8px;
  width: 80%;
  margin: 0 auto;
}

.loading-animation {
  display: flex;
  gap: 4px;
  align-items: center;
}

.loading-dot {
  width: 6px;
  height: 6px;
  background: linear-gradient(135deg, #0ea5e9, #06b6d4);
  border-radius: 50%;
  animation: bounce 1.4s infinite ease-in-out;
}

.loading-dot:nth-child(1) {
  animation-delay: -0.32s;
}

.loading-dot:nth-child(2) {
  animation-delay: -0.16s;
}

.loading-dot:nth-child(3) {
  animation-delay: 0s;
}

@keyframes bounce {
  0%, 80%, 100% {
    transform: scale(0);
    opacity: 0.5;
  }
  40% {
    transform: scale(1);
    opacity: 1;
  }
}

.loading-text {
  font-size: 12px;
  color: #94a3b8;
  animation: pulse 2s infinite;
}

@keyframes pulse {
  0%, 100% {
    opacity: 1;
  }
  50% {
    opacity: 0.5;
  }
}
</style>
