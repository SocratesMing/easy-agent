<template>
  <div class="chat-container">
    <div class="chat-main" :class="composerMode === 'center' ? 'is-center' : 'is-bottom'">
    <TodoListPanel
      v-if="!sidebarCollapsed"
      :todos="todos"
    />
    <div class="chat-messages" ref="messagesRef">
      <div v-if="sessionCreatedAt && messages.length > 0" class="session-created-time">
        {{ formatSessionTime(sessionCreatedAt) }}
      </div>
      <div v-if="messages.length === 0" class="welcome-screen">
        <h2>{{ welcomeTitle }}</h2>
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
        @approve="handleApprove"
        @reject="handleReject"
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
      class="composer"
      @send="onSend"
      :disabled="isStreaming"
      :isStreaming="isStreaming"
      :session-id="currentSessionId"
      :sessionUsage="sessionUsage"
      :sessionDuration="sessionDuration"
      :iterationCount="iterationCount"
      :models="models"
      :selectedModel="selectedModel"
      :showFooter="composerMode === 'bottom'"
      @update:selectedModel="(v) => emit('update:selectedModel', v)"
      @stop="handleStop"
      @createSession="handleCreateSession"
    />

    <!-- 分类预设问题：仅在首页（居中模式）展示，置于输入框下方，悬浮展开 -->
    <div class="preset-categories" v-if="composerMode === 'center' && presetQuestions.length">
      <div class="preset-category-tabs">
        <div
          class="preset-category"
          v-for="(group, gi) in presetQuestions"
          :key="gi"
        >
          <button type="button" class="preset-category-tab">
            <span v-if="group.icon" class="preset-category-icon">{{ group.icon }}</span>
            <span>{{ group.category }}</span>
          </button>
          <div class="preset-category-panel">
            <button
              v-for="(q, i) in group.questions"
              :key="i"
              type="button"
              class="preset-chip"
              @click="onPresetClick(q)"
            >{{ q }}</button>
          </div>
        </div>
      </div>
    </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, nextTick, computed, onMounted, onUnmounted } from 'vue'
import ChatMessage from './ChatMessage.vue'
import ChatInput from './ChatInput.vue'
import TodoListPanel from './TodoListPanel.vue'

const welcomeTitle = 'Easy Agent，让工作更简单'
// 首页布局模式：center=空会话时输入框居中，bottom=对话中输入框贴底
const composerMode = ref('center')

const deckTop = ref(0)
const deckVisibleCount = 3 // keep

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
  sessionDuration: {
    type: Number,
    default: 0
  },
  iterationCount: {
    type: Number,
    default: 0
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
  models: {
    type: Array,
    default: () => []
  },
  selectedModel: {
    type: String,
    default: null
  }
})

watch(() => props.messages, (newMessages) => {
  // 空会话显示居中输入框；有消息时输入框贴底
  composerMode.value = newMessages.length === 0 ? 'center' : 'bottom'
}, { immediate: true })

const emit = defineEmits(['sendMessage', 'stop', 'removeFile', 'createSession', 'approve', 'reject', 'update:selectedModel'])
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

function handleSend(message, files, signal, enableDeepThink = true) {
  emit('sendMessage', message, files, signal, enableDeepThink)
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

function handleApprove() {
  emit('approve')
}

function handleReject() {
  emit('reject')
}

function handleStop() {
  emit('stop')
}

function handleCreateSession() {
  emit('createSession')
}

function handleQuickAction(message, index) {
  // 兼容旧引用（已无 deck），直接走预设点击
  onPresetClick(message)
}

function onPresetClick(message) {
  composerMode.value = 'bottom'
  emit('sendMessage', message, [], null, true, false)
}

function onSend(message, files, signal, enableDeepThink) {
  composerMode.value = 'bottom'
  handleSend(message, files, signal, enableDeepThink)
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

/* 空会话：欢迎区与输入框整体垂直居中 */
.chat-main.is-center {
  justify-content: center;
}

.chat-main.is-center .chat-messages {
  flex: 0 1 auto;
  overflow: visible;
}

/* 回车发送后，输入框平滑下移到底部 */
.chat-main.is-bottom .composer {
  animation: composerDropIn 0.45s cubic-bezier(0.22, 1, 0.36, 1);
}

@keyframes composerDropIn {
  from {
    transform: translateY(-40px);
    opacity: 0;
  }
  to {
    transform: translateY(0);
    opacity: 1;
  }
}

.session-created-time {
  position: sticky;
  top: -24px;
  z-index: 10;
  text-align: center;
  font-size: 12px;
  color: #94a3b8;
  padding: 8px 0;
  margin: -24px -24px 12px;
  background: #ffffff;
  user-select: none;
}

.welcome-screen {
  display: flex;
  flex-direction: column;
  align-items: center;
  color: #64748b;
}

.welcome-screen h2 {
  font-size: 32px;
  font-weight: 600;
  color: #1e293b;
  margin: 0;
}

.preset-categories {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
  margin: 24px auto 0;
  width: 100%;
  max-width: 720px;
}

.preset-category-tabs {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  width: 100%;
  gap: 10px;
}

.preset-category-tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 999px;
  font-size: 13px;
  font-weight: 600;
  color: #475569;
  cursor: pointer;
  transition: all 0.2s ease;
}

.preset-category-tab:hover {
  border-color: #bae6fd;
  color: #0284c7;
}

.preset-category-tab.active {
  background: #e0f2fe;
  border-color: #7dd3fc;
  color: #0369a1;
}

.preset-category-icon {
  font-size: 14px;
}

.preset-category {
  position: relative;
}

.preset-category-panel {
  position: absolute;
  top: 100%;
  left: 50%;
  transform: translateX(-50%) translateY(-6px);
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 10px;
  width: 360px;
  max-width: 82vw;
  max-height: 0;
  opacity: 0;
  overflow: hidden;
  pointer-events: none;
  background: linear-gradient(180deg, #f8fafc 0%, #f0f9ff 100%);
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 0 12px;
  box-shadow: 0 10px 30px rgba(15, 23, 42, 0.10);
  transition: max-height 0.35s ease, opacity 0.3s ease,
    transform 0.3s ease, padding 0.3s ease;
  z-index: 20;
}

.preset-category:hover .preset-category-panel,
.preset-category:focus-within .preset-category-panel {
  max-height: 320px;
  opacity: 1;
  transform: translateX(-50%) translateY(0);
  pointer-events: auto;
  padding: 12px;
}

.preset-chip {
  width: 100%;
  padding: 12px 16px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  font-size: 13px;
  color: #475569;
  cursor: pointer;
  transition: all 0.2s ease;
  text-align: center;
}

.preset-chip:hover {
  background: #f0f9ff;
  border-color: #bae6fd;
  color: #0284c7;
  transform: translateY(-1px);
}

.preset-deck-stage {
  position: relative;
  width: 440px;
  max-width: 92vw;
  height: 188px;
}

.preset-card {
  position: absolute;
  top: 0;
  left: 50%;
  width: 100%;
  min-height: 150px;
  padding: 20px 22px;
  background: white;
  border: 1px solid #e2e8f0;
  border-radius: 18px;
  box-shadow: 0 6px 20px rgba(15, 23, 42, 0.08);
  cursor: pointer;
  transition: transform 0.4s cubic-bezier(0.22, 1, 0.36, 1),
    opacity 0.4s ease, box-shadow 0.25s ease;
  display: flex;
  flex-direction: column;
  gap: 12px;
  text-align: left;
  transform-origin: center top;
}

.preset-card.is-front {
  box-shadow: 0 10px 30px rgba(14, 165, 233, 0.18);
  border-color: #bae6fd;
}

.preset-card.is-front:hover {
  box-shadow: 0 14px 38px rgba(14, 165, 233, 0.28);
}

.preset-card.is-back {
  cursor: pointer;
}

.preset-card-top {
  display: flex;
  align-items: center;
  gap: 8px;
}

.preset-card-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 9px;
  background: linear-gradient(135deg, #e0f2fe, #f0f9ff);
  color: #0ea5e9;
  flex-shrink: 0;
}

.preset-card-icon svg {
  width: 17px;
  height: 17px;
}

.preset-card-tag {
  font-size: 12px;
  font-weight: 600;
  color: #0ea5e9;
  letter-spacing: 0.05em;
}

.preset-card-index {
  margin-left: auto;
  font-size: 12px;
  color: #94a3b8;
}

.preset-card-text {
  flex: 1;
  margin: 0;
  font-size: 17px;
  font-weight: 600;
  line-height: 1.5;
  color: #1e293b;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.preset-card-foot {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
  font-size: 13px;
  color: #64748b;
}

.preset-send-arrow {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: #0ea5e9;
  color: white;
  font-size: 14px;
  transition: transform 0.2s ease;
}

.preset-card.is-front:hover .preset-send-arrow {
  transform: translateX(3px);
}

.preset-nav {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-top: 26px;
}

.preset-nav-btn {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  border: 1px solid #e2e8f0;
  background: white;
  color: #475569;
  font-size: 18px;
  line-height: 1;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s ease;
}

.preset-nav-btn:hover {
  border-color: #0ea5e9;
  color: #0ea5e9;
  background: #f0f9ff;
}

.preset-dots {
  display: flex;
  align-items: center;
  gap: 8px;
}

.preset-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #cbd5e1;
  cursor: pointer;
  transition: all 0.2s ease;
}

.preset-dot.active {
  width: 22px;
  border-radius: 4px;
  background: #0ea5e9;
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
