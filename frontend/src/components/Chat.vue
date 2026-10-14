<template>
  <div class="chat-container">
    <div class="chat-main">
    <div class="chat-content" :class="composerMode === 'center' ? 'is-center' : 'is-bottom'">
      <!-- 顶部信息行：会话时间居中，操作区（任务规划 + 展开工作区）贴右。
           这三者此前分散在视口/聊天区右上角，位置互不相干；现在统一到同一行。 -->
      <div v-if="showTopbar" class="chat-topbar">
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
          @removeFile="(file) => handleRemoveFile(file, index)"
          @retry="handleRetry"
          @approve="handleApprove"
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
        @clearExpert="emit('clearExpert')"
        @selectExpert="(expert) => emit('selectExpert', expert)"
        @prefillApplied="emit('expertPromptApplied')"
      />

      <!-- 分类预置问题：仅在首页（居中模式）展示。每个分类一张卡片（分类名即卡片头部，
           不再单独放标签栏），最多展示三张；卡片内多个预置问题堆叠，
           鼠标滚轮（或左右方向键）在卡片上滑动即可来回切换子问题。 -->
      <div
        class="preset-area"
        v-if="composerMode === 'center' && displayedPresetGroups.length"
      >
        <div
          class="preset-cards"
          :class="'count-' + displayedPresetGroups.length"
          @mouseleave="onPresetLeave"
        >
          <div
            class="preset-deck"
            v-for="(group, gi) in displayedPresetGroups"
            :key="gi"
            :class="'accent-' + (gi % 3)"
            role="group"
            tabindex="0"
            :aria-label="group.category + ' 预置问题'"
            @mouseenter="onPresetEnter(gi)"
            @wheel="onPresetWheel($event, gi)"
            @keydown.left.prevent="stepPreset(gi, -1)"
            @keydown.right.prevent="stepPreset(gi, 1)"
            @keydown.enter.prevent="onPresetCardClick(gi)"
          >
            <div class="preset-deck-head">
              <span class="preset-deck-title">
                <span v-if="group.icon" class="preset-deck-title-icon">{{ group.icon }}</span>
                {{ group.category }}
              </span>
              <span class="preset-deck-count">
                {{ group.questions.length }} 条<template v-if="group.questions.length > 1"> · 滚动切换</template>
              </span>
            </div>
            <div class="preset-deck-stage">
              <div
                v-for="(q, i) in group.questions"
                :key="i"
                class="preset-card"
                :class="presetCardClass(gi, i)"
                :style="presetCardStyle(gi, i)"
                :aria-hidden="i !== presetIndex(gi)"
                @click="onPresetCardClick(gi)"
              >
                <span class="preset-card-text">{{ q }}</span>
              </div>
            </div>
            <div class="preset-deck-dots" v-if="group.questions.length > 1">
              <button
                v-for="(q, i) in group.questions"
                :key="i"
                type="button"
                class="preset-dot"
                :class="{ active: i === presetIndex(gi) }"
                :aria-label="'第 ' + (i + 1) + ' 条预置问题'"
                @click="setPresetQuestion(gi, i)"
              ></button>
            </div>
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
import { APP_WELCOME_TITLE } from '../config.js'
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

// 预置问题：每个分类 = 一张卡片（分类名直接做卡片头部），卡片内多个预置问题
// 堆叠呈现。最多展示 3 张卡片；每张卡片各自记住当前显示到第几个子问题。
const MAX_PRESET_CARDS = 3

// 只取前三个分类（后端可能传入更多，这里按需求截断）
const displayedPresetGroups = computed(() => (props.presetQuestions || []).slice(0, MAX_PRESET_CARDS))

// 每张卡片当前展示的子问题下标（按卡片序号索引）
const presetQuestionIndexes = ref([])

// 读取某张卡片当前子问题下标；数据变化导致越界时收敛到有效范围
function presetIndex(groupIndex) {
  const group = displayedPresetGroups.value[groupIndex]
  const count = group?.questions?.length || 0
  if (!count) return 0
  const cur = presetQuestionIndexes.value[groupIndex] || 0
  return Math.min(cur, count - 1)
}

function setPresetQuestion(groupIndex, questionIndex) {
  const group = displayedPresetGroups.value[groupIndex]
  const count = group?.questions?.length || 0
  if (!count) return
  const next = presetQuestionIndexes.value.slice()
  next[groupIndex] = Math.max(0, Math.min(questionIndex, count - 1))
  presetQuestionIndexes.value = next
  // 悬浮期间用滚轮/圆点切换子问题时，输入框预览实时跟随
  if (hoveredDeck.value === groupIndex) fillPresetPreview(groupIndex)
}

// 前后切换子问题；到端点后不再循环（停在端点，避免长列表滚动时跳回开头）
function stepPreset(groupIndex, delta) {
  setPresetQuestion(groupIndex, presetIndex(groupIndex) + delta)
}

// 鼠标滚轮在卡片上滑动切换子问题。仅在还有其它子问题时拦截滚动，
// 否则交还页面正常滚动，避免单条预置问题把整页滚动锁死。
function onPresetWheel(event, groupIndex) {
  const group = displayedPresetGroups.value[groupIndex]
  const count = group?.questions?.length || 0
  if (count <= 1) return
  const delta = event.deltaY !== 0 ? event.deltaY : event.deltaX
  if (!delta) return
  const current = presetIndex(groupIndex)
  const atStart = current <= 0
  const atEnd = current >= count - 1
  if ((delta < 0 && atStart) || (delta > 0 && atEnd)) return
  event.preventDefault()
  stepPreset(groupIndex, delta > 0 ? 1 : -1)
}

// 堆叠效果：当前卡在最前，其余卡依次露出一点，形成一摞
function presetCardClass(groupIndex, questionIndex) {
  const current = presetIndex(groupIndex)
  if (questionIndex === current) return 'is-front'
  return questionIndex < current ? 'is-before' : 'is-after'
}

function presetCardStyle(groupIndex, questionIndex) {
  // 只渲染当前问题；切换交给圆点/滚轮，配合 CSS 的 opacity 过渡淡入淡出
  return questionIndex === presetIndex(groupIndex)
    ? { zIndex: 30 }
    : { zIndex: 10, opacity: '0', pointerEvents: 'none' }
}

// 配置重新拉取后分类/子问题数量可能变化，收敛下标避免越界导致卡片空白
watch(displayedPresetGroups, (groups) => {
  const next = []
  groups.forEach((group, i) => {
    const count = group?.questions?.length || 0
    const cur = presetQuestionIndexes.value[i] || 0
    next[i] = count ? Math.min(cur, count - 1) : 0
  })
  presetQuestionIndexes.value = next
}, { immediate: true })
// 顶部信息行：会话时间（居中）+ 操作区（任务规划、展开工作区，贴右）。
// 三者中任意一个需要展示时，这一行才占位 —— 首页（无会话）保持干净，
// 否则会破坏 .chat-content.is-center 的垂直居中。
const showSessionTime = computed(() => !!props.sessionCreatedAt && props.messages.length > 0)
const showWorkspaceBtn = computed(() => !!props.currentSessionId && !props.workspaceExpanded)
const showTopbar = computed(() =>
  showSessionTime.value || props.todos.length > 0 || showWorkspaceBtn.value
)

const emit = defineEmits(['sendMessage', 'presetSend', 'stop', 'removeFile', 'createSession', 'approve', 'reject', 'update:selectedModel', 'toggleWorkspace', 'clearExpert', 'selectExpert', 'expertPromptApplied', 'openPanel'])
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

function handleSend(message, files, signal, enableDeepThink = true, enableWebSearch = false) {
  emit('sendMessage', message, files, signal, enableDeepThink, enableWebSearch)
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

// ========== 预置问题：悬浮预览 ==========
// 卡片上最多显示 3 行，长问题展示不全；悬浮时把完整文本实时填进输入框，
// 移出预置问题区后恢复悬浮前的内容（悬浮期间用户手动改过输入框则保留其修改）。
const hoveredDeck = ref(null)
const composerSnapshot = ref('')
const lastPreview = ref('')

function fillPresetPreview(groupIndex) {
  const group = displayedPresetGroups.value[groupIndex]
  const question = group?.questions?.[presetIndex(groupIndex)]
  if (question == null) return
  lastPreview.value = question
  composerRef.value?.setMessage(question)
}

function onPresetEnter(groupIndex) {
  if (hoveredDeck.value === null) {
    composerSnapshot.value = composerRef.value?.getMessage() ?? ''
  }
  hoveredDeck.value = groupIndex
  fillPresetPreview(groupIndex)
}

function onPresetLeave() {
  if (hoveredDeck.value === null) return
  hoveredDeck.value = null
  const current = composerRef.value?.getMessage() ?? ''
  const previewed = lastPreview.value
  lastPreview.value = ''
  if (!previewed || current === previewed) {
    composerRef.value?.setMessage(composerSnapshot.value)
  }
}

// 离开首页（开始对话）时复位悬浮状态
watch(composerMode, (mode) => {
  if (mode !== 'center') {
    hoveredDeck.value = null
    lastPreview.value = ''
  }
})

// ========== 预置问题：点击发送 ==========
// 点击卡片把该问题作为「新对话的第一条消息」发出：先复用悬浮预览的填入逻辑
// 把完整文本写进输入框，再触发输入框自身的发送（走上传等待、canSend 校验、
// 清空草稿等与手动发送完全一致的链路），不复制一份发送实现。
// presetSendPending 标记本次 send 来自预置问题卡片，onSend 里据此改走
// presetSend 事件，由 App 侧先落到全新会话再发送。
const presetSendPending = ref(false)

function onPresetCardClick(groupIndex) {
  const group = displayedPresetGroups.value[groupIndex]
  const question = group?.questions?.[presetIndex(groupIndex)]
  if (question == null) return
  // 点击即发送，悬浮预览状态不再需要（移出时的草稿恢复逻辑会干扰发送）
  hoveredDeck.value = null
  lastPreview.value = ''
  presetSendPending.value = true
  composerRef.value?.setMessage(question)
  composerRef.value?.triggerSend()
  // triggerSend 因 disabled/canSend 未真正发出时，复位标记避免影响后续手动发送
  if (composerRef.value?.getMessage() === question) {
    presetSendPending.value = false
  }
}

const composerRef = ref(null)

function onSend(message, files, signal, enableDeepThink, enableWebSearch) {
  composerMode.value = 'bottom'
  isAtBottom.value = true
  if (presetSendPending.value) {
    presetSendPending.value = false
    emit('presetSend', message, files, signal, enableDeepThink, enableWebSearch)
    return
  }
  handleSend(message, files, signal, enableDeepThink, enableWebSearch)
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

/* 预置问题卡片区：与输入框同宽（80% / 最大 900px），一行最多三张、每张卡自带分类头部 */
.preset-area {
  /* 与 .chat-input-container 相同的左右内边距与滚动条预留，
     使 80% 宽的卡片区与输入框左右边缘严格对齐 */
  padding: 0 24px;
  width: 100%;
  overflow-y: auto;
  scrollbar-gutter: stable;
}

.preset-cards {
  display: grid;
  gap: 14px;
  margin: 20px auto 0;
  width: 80%;
  max-width: 900px;
}

/* 卡片数量决定列数：1/2/3 张时列数自适应，宽度始终与输入框对齐 */
.preset-cards.count-1 {
  grid-template-columns: minmax(0, 1fr);
}

.preset-cards.count-2 {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.preset-cards.count-3 {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

@media (max-width: 1024px) {
  .preset-cards.count-3 {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 680px) {
  .preset-cards.count-2,
  .preset-cards.count-3 {
    grid-template-columns: minmax(0, 1fr);
  }
}

/* 单张卡片：无边框，用「分类色渐变底 + 白色浮层」分层表达；
   三张卡各带一种主题色（天蓝 / 紫 / 翠绿），角落再叠一团柔光增色 */
.preset-deck {
  position: relative;
  display: flex;
  flex-direction: column;
  padding: 14px 14px 12px;
  border-radius: 16px;
  border: none;
  overflow: hidden;
  background: linear-gradient(
    150deg,
    color-mix(in srgb, var(--preset-accent) 16%, var(--bg-secondary, #ffffff)) 0%,
    color-mix(in srgb, var(--preset-accent) 7%, var(--bg-secondary, #ffffff)) 48%,
    var(--bg-secondary, #ffffff) 100%
  );
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
  outline: none;
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
    color-mix(in srgb, var(--preset-accent) 24%, transparent) 0%,
    transparent 70%
  );
  pointer-events: none;
}

.preset-deck:hover,
.preset-deck:focus-visible {
  transform: translateY(-2px);
  box-shadow: 0 14px 30px -12px color-mix(in srgb, var(--preset-accent) 55%, transparent),
    0 2px 6px rgba(15, 23, 42, 0.05);
}

.preset-deck:focus-visible {
  outline: 2px solid var(--preset-accent, #0ea5e9);
  outline-offset: 2px;
}

/* 三张卡的主题色：天蓝 / 紫 / 翠绿 */
.preset-deck.accent-0 { --preset-accent: #0ea5e9; }
.preset-deck.accent-1 { --preset-accent: #8b5cf6; }
.preset-deck.accent-2 { --preset-accent: #10b981; }

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
  background: color-mix(in srgb, var(--preset-accent) 20%, #ffffff);
}

.preset-deck-title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 700;
  color: var(--text-primary, #1e293b);
}

.preset-deck-count {
  margin-left: auto;
  font-size: 12px;
  font-weight: 500;
  color: color-mix(in srgb, var(--preset-accent) 62%, var(--text-secondary, #64748b));
  flex-shrink: 0;
}

/* 卡片堆叠区：高度固定，子问题卡在内部绝对定位叠放 */
.preset-deck-stage {
  position: relative;
  z-index: 1;
  width: 100%;
  height: 132px;
}

/* 子问题卡：无边框、背景与分类卡融为一体，同一时刻只显示当前问题 */
.preset-card {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 10px;
  width: 100%;
  padding: 12px 14px;
  background: transparent;
  border: none;
  border-radius: 12px;
  text-align: left;
  cursor: pointer;
  transform-origin: center bottom;
  transition: transform 0.3s cubic-bezier(0.22, 1, 0.36, 1),
    opacity 0.3s ease, background-color 0.2s ease, box-shadow 0.2s ease;
}

/* 悬停时垫一层主题色浅底，作为「可点击」暗示 */
.preset-card.is-front:hover {
  background: color-mix(in srgb, var(--preset-accent) 9%, transparent);
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

.preset-deck-dots {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  margin-top: 10px;
}

.preset-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  border: none;
  padding: 0;
  background: color-mix(in srgb, var(--preset-accent) 24%, transparent);
  cursor: pointer;
  transition: all 0.2s ease;
}

.preset-dot:focus-visible {
  outline: 2px solid var(--preset-accent, #0ea5e9);
  outline-offset: 2px;
}

.preset-dot.active {
  width: 16px;
  border-radius: 3px;
  background: var(--preset-accent, #0ea5e9);
}

/* 深色主题：底色换成暗面分层，主题色透明度调高保持可辨 */
html[data-theme="dark"] .preset-deck {
  background: linear-gradient(
    150deg,
    color-mix(in srgb, var(--preset-accent) 30%, #0f172a) 0%,
    color-mix(in srgb, var(--preset-accent) 14%, #0f172a) 52%,
    #0f172a 100%
  );
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.45);
}

html[data-theme="dark"] .preset-deck-title-icon {
  background: color-mix(in srgb, var(--preset-accent) 32%, #0f172a);
}

html[data-theme="dark"] .preset-card.is-front:hover {
  background: color-mix(in srgb, var(--preset-accent) 20%, transparent);
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
