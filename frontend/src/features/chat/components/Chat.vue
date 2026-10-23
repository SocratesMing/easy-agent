<template>
  <div class="chat-container">
    <div class="chat-main">
    <div class="chat-content" :class="composerMode === 'center' ? 'is-center' : 'is-bottom'">
      <!-- 顶部信息行：会话时间居中，操作区（任务规划 + 展开工作区）贴右。
           这三者此前分散在视口/聊天区右上角，位置互不相干；现在统一到同一行。
           空会话（无消息、无任务）时只有右侧一个工作区按钮，这一行改为浮层不占位，
           否则会在欢迎页上方压出一条 44px 白带 + 分隔线，破坏垂直居中。 -->
      <div v-if="showTopbar" class="chat-topbar" :class="{ 'is-float': topbarFloating }">
        <div v-if="showSessionTime" class="session-created-time">
          <span class="session-time-pill">
            <svg class="session-time-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="10"></circle>
              <polyline points="12 6 12 12 16 14"></polyline>
            </svg>
            {{ formatSessionTime(sessionCreatedAt) }}
          </span>
        </div>
        <div class="chat-topbar-actions">
          <!-- 任务规划：只由 todos 决定显隐，不再依赖 sidebarCollapsed
               （那会让折叠会话列表时任务胶囊一起消失）。 -->
          <TodoListPanel :todos="todos" />
          <button
            v-if="currentSessionId && !workspaceExpanded"
            class="expand-workspace-inline"
            @click="$emit('toggleWorkspace')"
            title="展开工作区"
          >
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
              <line x1="9" y1="3" x2="9" y2="21"></line>
            </svg>
          </button>
        </div>
      </div>
       <div class="chat-messages" ref="messagesRef" @scroll="handleScroll">
        <div v-if="messages.length === 0 && !sessionLoading" class="welcome-screen">
          <h2>{{ welcomeTitle }}</h2>
        </div>
        <!-- 切换会话拉取历史期间：显示骨架，避免先闪出空欢迎页 -->
        <div v-else-if="sessionLoading && messages.length === 0" class="session-loading-skeleton" aria-busy="true" aria-label="加载会话">
          <div class="skeleton-block" v-for="i in 3" :key="i"></div>
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
          :user-expert="userExpertFor(index)"
          @removeFile="(file) => handleRemoveFile(file, index)"
          @retry="handleRetry"
          @approve="handleApprove"
          @edit="handleEdit"
          @reject="handleReject"
        />
      </div>
      </div>
      <button v-if="canGoToNextUserMessage" @click="goToNextUserMessage" class="scroll-btn next" title="回到下一个用户问题">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="6 9 12 15 18 9"></polyline>
        </svg>
      </button>
      <button v-if="canGoToPrevUserMessage" @click="goToPrevUserMessage" class="scroll-btn prev" title="回到上一个用户问题">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="18 15 12 9 6 15"></polyline>
        </svg>
      </button>

      <!-- 预置问题卡片：仅在首页（居中模式）展示，位置在欢迎语与输入框之间。
           一张卡片一条预置问题，问题内容直接展示在卡片里，不堆叠、不悬浮切换；
           配置了多条时依次排列（最多 MAX_PRESET_CARDS 张）。
           点击卡片把该问题填进输入框（不直接发送），用户可改完自己发。 -->
      <div
        class="preset-area"
        v-if="composerMode === 'center' && displayedPresetCards.length"
      >
        <div class="preset-cards" :class="'count-' + displayedPresetCards.length">
          <div
            class="preset-deck"
            v-for="(card, ci) in displayedPresetCards"
            :key="ci"
            :class="'accent-' + (ci % 3)"
            role="button"
            tabindex="0"
            :title="card.question"
            @click="onPresetCardClick(ci)"
            @keydown.enter.prevent="onPresetCardClick(ci)"
          >
            <div class="preset-deck-head">
              <span class="preset-deck-title">
                <span v-if="card.icon" class="preset-deck-title-icon">{{ card.icon }}</span>
                {{ card.category }}
              </span>
            </div>
            <div class="preset-deck-stage">
              <div class="preset-card">
                <span class="preset-card-text">{{ card.question }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <ChatInput
        ref="composerRef"
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
        :activeExpert="activeExpert"
        :prefillText="expertPrompt"
        @update:selectedModel="(v) => emit('update:selectedModel', v)"
        @stop="handleStop"
        @createSession="handleCreateSession"
        @openPanel="(tab) => emit('openPanel', tab)"
        @openKnowledge="emit('openKnowledge')"
        @clearExpert="emit('clearExpert')"
        @selectExpert="(expert) => emit('selectExpert', expert)"
        @prefillApplied="emit('expertPromptApplied')"
      />
    </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, nextTick, computed, onMounted, onUnmounted } from 'vue'
import ChatMessage from './ChatMessage.vue'
import ChatInput from './ChatInput.vue'
import TodoListPanel from './TodoListPanel.vue'
import { APP_WELCOME_TITLE } from '../../../config.js'
// 首页布局模式：center=空会话时输入框居中，bottom=对话中输入框贴底
const composerMode = ref('center')

const deckTop = ref(0)
const deckVisibleCount = 3 // keep

const props = defineProps({
  messages: {
    type: Array,
    default: () => []
  },
  sessionLoading: {
    type: Boolean,
    default: false
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
    default: () => ({ input_tokens: 0, output_tokens: 0, reasoning_tokens: 0 })
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
  },
  welcomeTitle: {
    type: String,
    default: APP_WELCOME_TITLE
  },
  // 当前会话选中的专家 Agent（{export_agent_id, export_name_cn} 或 null）。
  // 非空时输入框工具栏显示专家胶囊，且本会话消息走 /agent/stream_export_run。
  activeExpert: {
    type: Object,
    default: null
  },
  // 待回填到输入框的问题（专家详情弹窗点「专家帮你做」示例），写入后由
  // expertPromptApplied 事件通知父组件清空。
  expertPrompt: {
    type: String,
    default: ''
  }
})

watch([() => props.messages, () => props.messages.length, () => props.sessionLoading], ([newMessages, , loading]) => {
  // 空会话显示居中输入框；有消息时输入框贴底。
  // 正在拉取历史时按「有会话」处理（贴底），避免先居中再跳到贴底的闪动。
  // 额外追踪 messages.length：发送流程中 App 会先置空 messages（新引用，watch 触发
  // 回 'center'）再对同一数组 push 消息（引用不变，仅靠引用比较不会再次触发），
  // 导致点击预置问题卡片后卡片不消失、输入框不贴底。length 变化补上这次触发。
  composerMode.value = (newMessages.length === 0 && !loading) ? 'center' : 'bottom'
}, { immediate: true })

// 预置问题卡片最多展示几张（一张卡片一条问题，多配的部分按顺序截断）
const MAX_PRESET_CARDS = 6

// 预置问题卡片：一张卡片一条问题（后端按分类配置，同分类下配了多条就依次展开成多张卡），
// 依次排列且最多 MAX_PRESET_CARDS 张；空问题跳过，不占卡片位
const displayedPresetCards = computed(() => {
  const cards = []
  for (const group of (props.presetQuestions || [])) {
    const questions = group && group.questions ? group.questions : []
    for (const question of questions) {
      if (cards.length >= MAX_PRESET_CARDS) return cards
      if (question == null || !String(question).trim()) continue
      cards.push({
        category: (group && group.category) || '预设问题',
        icon: (group && group.icon) || '',
        question: String(question)
      })
    }
  }
  return cards
})
// 顶部信息行：会话时间（居中）+ 操作区（任务规划、展开工作区，贴右）。
// 三者中任意一个需要展示时，这一行才占位 —— 首页（无会话）保持干净，
// 否则会破坏 .chat-content.is-center 的垂直居中。
const showSessionTime = computed(() => !!props.sessionCreatedAt && props.messages.length > 0)
const showWorkspaceBtn = computed(() => !!props.currentSessionId && !props.workspaceExpanded)
const showTopbar = computed(() =>
  showSessionTime.value || props.todos.length > 0 || showWorkspaceBtn.value
)
// 这一行只剩「展开工作区」按钮（空会话：没有会话时间、没有任务）时改为浮层不占位：
// 有会话时间或任务胶囊的行才是真顶栏，需要占住 44px 与工作区头部对齐。
const topbarFloating = computed(
  () => !showSessionTime.value && props.todos.length === 0 && showWorkspaceBtn.value
)

const emit = defineEmits(['sendMessage', 'stop', 'removeFile', 'createSession', 'approve', 'reject', 'edit', 'update:selectedModel', 'toggleWorkspace', 'clearExpert', 'selectExpert', 'expertPromptApplied', 'openPanel', 'openKnowledge'])
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
  // 未滚动到底部时持续显示“回到下一个用户问题”按钮，
  // 让用户能逐条向下跳转，最后再回到会话底部
  return userMessageIndices.value.length > 0 && !isAtBottom.value
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
    // 还有更靠后的用户问题：逐条向下跳转
    currentUserMessageIndex.value--
    const targetIndex = userMessageIndices.value[currentUserMessageIndex.value]
    if (targetIndex !== undefined && messageRefs.value[targetIndex]) {
      messageRefs.value[targetIndex].scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  } else {
    // 已到最后一个用户问题（或未经过导航）：直接滚动到会话底部
    currentUserMessageIndex.value = -1
    nextTick(() => {
      const el = messagesRef.value
      if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
    })
  }
}

function handleSend(message, files, signal, enableDeepThink = true, enableWebSearch = false, skills = [], expertEditId = '') {
  emit('sendMessage', message, files, signal, enableDeepThink, enableWebSearch, skills, expertEditId)
}

function handleRemoveFile(file, messageIndex) {
  // 从事件参数中获取file，然后从messages中获取对应的message
  const message = props.messages[messageIndex]
  emit('removeFile', message, messageIndex, file)
}

/**
 * 该条 assistant 消息对应哪一轮的专家（回答面板左上角的「头像 + 名称」）。
 *
 * 数据来源：后端把本轮专家写进了**用户消息**的 expert 字段（随消息落库），
 * 因此这里从当前消息向前找最近的一条用户消息取它的 expert 即可 ——
 * 刷新/历史加载后不丢，同一会话不同轮次选了不同专家也能各自对应。
 * 内存中的占位消息自带 expert（ChatMessage 内优先用消息自身的），此处为兜底。
 */
function userExpertFor(index) {
  for (let i = index - 1; i >= 0; i -= 1) {
    const msg = props.messages[i]
    if (!msg) continue
    if (msg.role === 'user') return msg.expert || null
  }
  return null
}

function handleRetry(content) {
  // 向上传递重试事件
  emit('retry', content)
}

// payload: { interruptId }——回答哪一个待审批中断（DA-02），原样上抛
function handleEdit(payload) {
  emit('edit', payload)
}
function handleApprove(payload) {
  emit('approve', payload)
}

function handleReject(payload) {
  emit('reject', payload)
}

function handleStop() {
  emit('stop')
}

function handleCreateSession() {
  emit('createSession')
}

// ========== 预置问题：点击填入 ==========
// 一张卡片一条问题，问题内容直接展示在卡片上；点击（或聚焦后回车）只把该问题的
// 完整文本填进输入框并把焦点交给它（卡片上最多显示 3 行，长问题展示不全）：
// 发不发、要不要先改一改，由用户自己决定，不自动发送。
function onPresetCardClick(cardIndex) {
  const card = displayedPresetCards.value[cardIndex]
  const question = card ? card.question : null
  if (question == null) return
  composerRef.value?.setMessage(question)
}

const composerRef = ref(null)

function onSend(message, files, signal, enableDeepThink, enableWebSearch, skills, expertEditId = '') {
  composerMode.value = 'bottom'
  isAtBottom.value = true
  handleSend(message, files, signal, enableDeepThink, enableWebSearch, skills, expertEditId)
}

// 是否贴底：用户位于滚动容器底部时为 true，向上滚动查看历史时为 false。
// 流式更新仅在贴底时自动滚动，避免抢占用户阅读上方内容；用户滚回底部后自动恢复跟随。
const isAtBottom = ref(true)

function handleScroll() {
  const el = messagesRef.value
  if (!el) return
  const threshold = 80
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < threshold
  isAtBottom.value = atBottom
  // 手动滚动回到最底部时，同步复位「上一个/下一个问题」导航索引：
  // 此前索引停留在历史问题位置，会导致回到底部后「回到上一个问题」按钮
  // 按旧索引计算而消失（按钮状态与真实滚动位置脱节）。
  if (atBottom && currentUserMessageIndex.value !== -1) {
    currentUserMessageIndex.value = -1
  }
}

function scrollToBottom(force = false) {
  nextTick(() => {
    const el = messagesRef.value
    if (!el) return
    if (force || isAtBottom.value) {
      el.scrollTop = el.scrollHeight
    }
  })
}

watch(() => props.messages, (newMessages, oldMessages) => {
  // 流式更新或新增消息时，仅在用户贴底时自动滚动；用户向上查看历史时不打断
  if (props.isStreaming || (newMessages?.length || 0) > (oldMessages?.length || 0)) {
    scrollToBottom()
  }
  nextTick(() => {
    updateUserMessageIndices()
  })
}, { deep: true })

watch(() => props.scrollTrigger, () => {
  // 切换/加载会话时强制贴底
  isAtBottom.value = true
  scrollToBottom(true)
})
</script>

<style scoped>
.chat-container {
  flex: 1;
  /* flex 项默认 min-width:auto 会阻止收缩，工作区展开时聊天区不会让位 */
  min-width: 0;
  /* 内部浮层按钮（.scroll-btn）改为相对本容器定位，不再依赖视口/面板宽度 */
  position: relative;
  display: flex;
  flex-direction: row;
  /* 聊天区表面色：会话时间吸顶栏等需要"与容器同色"的元素统一引用本变量，
     避免各自硬编码导致色差（此前吸顶栏用 --bg-primary 而这里是 #ffffff）。 */
  --chat-surface: #ffffff;
  background: var(--chat-surface);
  overflow: hidden;
}

/* 聊天区骨架：单列纵向。任务规划改为放在 .chat-content 顶部的可折叠条，
   不再占用横向空间（此前是 260px 左栏，会挤压会话宽度）。 */
.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-width: 0;
  position: relative;
}

/* 内容列：任务条 + 消息区 + 滚动按钮 + 输入框 + 预设问题。
   position: relative 让 .scroll-btn 相对本列定位（含任务条之外的消息区域）。 */
.chat-content {
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
  /* 上下 padding 归零：sticky 子元素（如执行过程头部）被约束在父元素 content box 内，
     容器的上下 padding 会让它们的冻结位置整体下移 24px —— 冻结时贴不住顶部、
     会与上方滚过来的内容糊在一起。改由首尾伪元素提供间距，吸附点即等于容器顶部。 */
  padding: 0 24px;
  display: flex;
  flex-direction: column;
  /* 与输入框区保持相同的内容宽度基准：预留滚动条 gutter，避免滚动条出现/消失
     导致消息容器（含「处理过程」）与输入框左右错位、宽度跳动。 */
  scrollbar-gutter: stable;
}

/* 首尾间距（替代被移除的上下 padding）：
   伪元素只是普通 flex item，不会成为 sticky 子元素的吸附参照。 */
.chat-messages::before,
.chat-messages::after {
  content: '';
  display: block;
  flex: 0 0 24px;
}

/* 空会话：欢迎区与输入框整体垂直居中 */
.chat-content.is-center {
  justify-content: center;
}

.chat-content.is-center .chat-messages {
  flex: 0 1 auto;
  overflow: visible;
}

/* 回车发送后，输入框平滑下移到底部 */
.chat-content.is-bottom .composer {
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

/* 顶部信息行：会话时间居中，操作区（任务规划胶囊 + 展开工作区）贴右。
   border-bottom 画在这一行上，同时充当消息区的顶部分隔线。 */
.chat-topbar {
  position: relative;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  /* 与工作区头部 .wp-header 严格等高（44px，含 1px 底边框），
     工作区展开时两区横线才能对齐；用固定高度而非 padding 推导。 */
  height: 44px;
  padding: 0 24px;
  box-sizing: border-box;
  background: var(--chat-surface, #ffffff);
  border-bottom: 1px solid var(--border-color, #e2e8f0);
}

/* 空会话：这一行只有右侧工作区按钮，不参与布局（否则欢迎页被顶下去 44px）。
   浮层本身不吃点击，按钮单独放行，保证工作区入口仍可用。 */
.chat-topbar.is-float {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 44px;
  background: transparent;
  border-bottom: none;
  pointer-events: none;
}

.chat-topbar.is-float .chat-topbar-actions {
  pointer-events: auto;
}

/* 右侧操作区：绝对定位以免影响时间的真正居中。
   z-index 让它（及其内部的浮层）压在消息区之上。 */
.chat-topbar-actions {
  position: absolute;
  right: 24px;
  top: 50%;
  transform: translateY(-50%);
  z-index: 40;
  display: flex;
  align-items: center;
  gap: 8px;
}

/* 当前专家标识：绝对定位在 topbar 左侧（操作区在右），与会话时间的居中互不干扰。 */
/* 会话时间只作为行内内容；条状背景与分隔线已上移到 .chat-topbar */
.session-created-time {
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none;
  user-select: none;
}

/* 展开工作区：与任务规划胶囊同排、位于最右。
   原先 fixed 在视口右上角，与时间行不在同一区域（工作区展开时会浮在工作区面板上）。 */
.expand-workspace-inline {
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 999px;
  /* 图标为展开箭头（‹），与工作区头部的收起箭头（›）成对；
     配色改用中性灰，不再沿袭原先文件夹图标的黄色 hover。 */
  color: #64748b;
  cursor: pointer;
  transition: background 0.2s ease, border-color 0.2s ease, color 0.2s ease;
}

.expand-workspace-inline:hover {
  background: #eef2f7;
  border-color: #cbd5e1;
  color: #1f2937;
}

.expand-workspace-inline svg {
  width: 14px;
  height: 14px;
}

.session-time-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 11px 3px 9px;
  border-radius: 999px;
  font-size: 12px;
  line-height: 1.6;
  font-weight: 500;
  letter-spacing: 0.01em;
  color: var(--text-secondary, #64748b);
  /* 底色用 --bg-primary（#f8fafc 浅灰蓝）而不是 --bg-secondary（#ffffff）。
     容器本身已是白色，胶囊再用白色就完全看不见了 —— 需要一个明确低于
     容器明度的表面色才能形成层次。 */
  background: var(--bg-primary, #f8fafc);
  border: 1px solid var(--border-color, #e2e8f0);
  white-space: nowrap;
}

.session-time-icon {
  width: 12px;
  height: 12px;
  flex-shrink: 0;
  opacity: 0.7;
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

/* 切会话拉历史时的骨架屏：替代空欢迎页，避免"先闪空页面" */
.session-loading-skeleton {
  width: 100%;
  max-width: 760px;
  margin: 24px auto;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.session-loading-skeleton .skeleton-block {
  height: 14px;
  border-radius: 6px;
  background: var(--border-color, #e2e8f0);
  animation: skeleton-pulse 1.4s ease-in-out infinite;
}

.session-loading-skeleton .skeleton-block:nth-child(1) { width: 60%; }
.session-loading-skeleton .skeleton-block:nth-child(2) { width: 88%; }
.session-loading-skeleton .skeleton-block:nth-child(3) { width: 72%; }

@keyframes skeleton-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.45; }
}

/* 预置问题卡片区：夹在欢迎语与输入框之间，与输入框同宽（80% / 最大 900px），
   一行最多三张、每张卡自带分类头部；上间距由 .chat-messages 的 24px 间距伪元素给出，
   下间距用 8px 补足到与上侧相当的 24px（输入框自己还有 16px 上内边距）。 */
.preset-area {
  /* 与 .chat-input-container 相同的左右内边距与滚动条预留，
     使 80% 宽的卡片区与输入框左右边缘严格对齐 */
  padding: 0 24px 8px;
  width: 100%;
  overflow-y: auto;
  scrollbar-gutter: stable;
}

.preset-cards {
  display: grid;
  gap: 14px;
  margin: 0 auto;
  width: 80%;
  max-width: 900px;
}

/* 卡片数量决定列数：1/2/3 张时列数自适应，4 张及以上固定三列、多余换行排列，
   宽度始终与输入框对齐 */
.preset-cards.count-1 {
  grid-template-columns: minmax(0, 1fr);
}

.preset-cards.count-2 {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.preset-cards.count-3,
.preset-cards.count-4,
.preset-cards.count-5,
.preset-cards.count-6 {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

@media (max-width: 1024px) {
  .preset-cards.count-3,
  .preset-cards.count-4,
  .preset-cards.count-5,
  .preset-cards.count-6 {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 680px) {
  .preset-cards.count-2,
  .preset-cards.count-3,
  .preset-cards.count-4,
  .preset-cards.count-5,
  .preset-cards.count-6 {
    grid-template-columns: minmax(0, 1fr);
  }
}

/* 单张卡片：主页面主色只做少量强调，底色交给语义化表面层，
   用细边框和柔和投影区分层级；各分组不再各用一套艳丽色相 */
.preset-deck {
  position: relative;
  display: flex;
  flex-direction: column;
  padding: 14px 14px 12px;
  border-radius: 16px;
  border: 1px solid var(--border-color, #e2e8f0);
  overflow: hidden;
  background: var(--bg-secondary, #ffffff);
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.028);
  outline: none;
  cursor: pointer;
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}

.preset-deck::after {
  content: '';
  position: absolute;
  top: -34px;
  right: -34px;
  width: 96px;
  height: 96px;
  border-radius: 50%;
  background: radial-gradient(
    circle,
    color-mix(in srgb, var(--preset-accent) 8%, transparent) 0%,
    transparent 68%
  );
  pointer-events: none;
}

.preset-deck:hover,
.preset-deck:focus-visible {
  transform: translateY(-2px);
  box-shadow: 0 10px 24px -14px color-mix(in srgb, var(--preset-accent) 32%, transparent),
    0 2px 6px rgba(15, 23, 42, 0.05);
}

.preset-deck:focus-visible {
  outline: 2px solid var(--preset-accent, #0ea5e9);
  outline-offset: 2px;
}

/* 三张卡使用同一主色，避免首页出现过多高饱和分类色 */
.preset-deck.accent-0 { --preset-accent: var(--accent-color, #0ea5e9); }
.preset-deck.accent-1 { --preset-accent: var(--accent-color, #0ea5e9); }
.preset-deck.accent-2 { --preset-accent: var(--accent-color, #0ea5e9); }

.preset-deck-head {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

/* 分类图标放进主题色圆片（替代原先的细色条） */
.preset-deck-title-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: 9px;
  font-size: 14px;
  background: color-mix(in srgb, var(--preset-accent) 12%, var(--bg-tertiary, #f1f5f9));
}

.preset-deck-title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 700;
  color: var(--text-primary, #1e293b);
}

/* 问题区：高度固定，问题长短不一也保证卡片大小一致 */
.preset-deck-stage {
  position: relative;
  z-index: 1;
  width: 100%;
  height: 132px;
}

/* 问题内容：无边框、背景与分类卡融为一体 */
.preset-card {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  padding: 12px 14px;
  background: transparent;
  border: none;
  border-radius: 12px;
  text-align: left;
  transition: background-color 0.2s ease;
}

/* 悬停时垫一层主题色浅底，作为「可点击」暗示（点击区域是整张卡片） */
.preset-deck:hover .preset-card {
  background: color-mix(in srgb, var(--preset-accent) 5%, transparent);
}

.preset-card-text {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  line-height: 1.55;
  color: var(--text-primary, #1e293b);
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
}

html[data-theme="dark"] .preset-deck:hover .preset-card {
  background: color-mix(in srgb, var(--preset-accent) 10%, transparent);
}

/* 相对聊天容器定位（而非视口）：工作区分栏后聊天区会收窄，用 fixed 会飘到
   工作区上面；用 absolute 则始终贴聊天区右边缘，工作区拖拽改宽时无需避让。 */
.scroll-btn {
  position: absolute;
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



/* 不再需要按面板宽度做避让（原 260px 硬编码在可拖拽后必然错位）：
   按钮已改为相对 .chat-container 定位，聊天区收窄时自动跟随。 */
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
