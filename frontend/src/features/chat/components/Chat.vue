<template>
  <div class="chat-container" :class="{ 'is-sidebar': isSidebar }" @ea-visual-prompt="onVisualPrompt"
    @mouseover="previewCitation" @mouseout="leaveCitation" @focusin="previewCitation" @focusout="leaveCitation">
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
            v-if="showWorkspaceBtn"
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
          <slot name="welcome"><h2>{{ welcomeTitle }}</h2></slot>
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
        :data-message-index="index"
        :class="msg.role"
      >
        <ChatMessage
          :message="msg"
          :citation-presentation="isSidebar ? 'inline' : 'popover'"
          :user-expert="userExpertFor(index)"
          @removeFile="(file) => handleRemoveFile(file, index)"
          @retry="handleRetry"
          @approve="handleApprove"
          @edit="handleEdit"
          @reject="handleReject"
        />
        <div v-for="record in compactionsAt(index)" :key="record.id" class="compaction-notice" role="status" :title="record.message">
          <svg class="compaction-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m4 5 6 6-6 6M12 19h8" /></svg>
          <span>{{ compactionNoticeText(record) }}</span>
        </div>
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
        v-if="composerMode === 'center' && displayedPresetCards.length && !isSidebar"
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

      <slot name="composer-prefix"></slot>
      <ChatInput
        ref="composerRef"
        class="composer"
        :draft-key="draftKey"
        :consume-pending-skill="!isSidebar"
        :compact="isSidebar"
        :show-manage-entry="showManageEntry"
        @send="onSend"
        @webSearchChange="emit('webSearchChange', $event)"
        :disabled="isStreaming || compactBusy"
        :isStreaming="isStreaming"
        :session-id="currentSessionId"
        :sessionUsage="compactUsage"
        :compact-busy="compactBusy"
        :compact-reason="compactReason"
        @compact-context="runCompact"
        :sessionDuration="sessionDuration"
        :iterationCount="iterationCount"
        :models="models"
        :selectedModel="selectedModel"
        :showFooter="composerMode === 'bottom' && !isSidebar"
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
    <CitationPopover v-if="citationPreview" ref="citationPopover" :item="citationPreview.item" :number="citationPreview.number"
      :popover-id="citationPopoverId" :position="citationStyle" @keep="keepCitation" @leave="deferCitationClose" @resize="positionCitation" />
  </div>
</template>

<script>
import citationPopoverMixin from '../../../shared/citations/popover.js'
import CitationPopover from './CitationPopover.vue'
import contextCompactionMixin from '../../../shared/compaction/mixin.js'
export default { components: { CitationPopover }, mixins: [contextCompactionMixin, citationPopoverMixin], beforeUnmount() { this.disposeCompaction(); this.disposeCitations() } }
</script>

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
  messages: { type: Array, default: () => [] },
  sessionLoading: { type: Boolean, default: false },
  currentSessionId: { type: String, default: null },
  sessionCreatedAt: { type: String, default: null },
  isStreaming: { type: Boolean, default: false },
  scrollTrigger: { type: Number, default: 0 },
  sessionUsage: { type: Object, default: () => ({ input_tokens: 0, output_tokens: 0, reasoning_tokens: 0 }) },
  sessionDuration: { type: Number, default: 0 },
  iterationCount: { type: Number, default: 0 },
  todos: { type: Array, default: () => [] },
  presetQuestions: { type: Array, default: () => [] },
  workspaceExpanded: { type: Boolean, default: false },
  sidebarCollapsed: { type: Boolean, default: false },
  models: { type: Array, default: () => [] },
  selectedModel: { type: String, default: null },
  welcomeTitle: { type: String, default: APP_WELCOME_TITLE },
  // 当前会话选中的专家 Agent（{export_agent_id, export_name_cn} 或 null）。
  // 非空时输入框工具栏显示专家胶囊，且本会话消息走 /agent/chat/stream（带 expert_agent_id）。
  activeExpert: { type: Object, default: null },
  // 待回填到输入框的问题（专家详情弹窗点「专家帮你做」示例），写入后由
  // expertPromptApplied 事件通知父组件清空。
  expertPrompt: { type: String, default: '' },
  // variant=sidebar：AI 侧边栏窄栏（输入框始终贴底、无工作区入口）；draftKey 让侧边栏草稿与主页面互不覆盖
  variant: { type: String, default: 'main' },
  draftKey: { type: String, default: 'easy_agent_input_draft' },
  showManageEntry: { type: Boolean, default: true }
})
const isSidebar = computed(() => props.variant === 'sidebar')

watch([() => props.messages, () => props.messages.length, () => props.sessionLoading], ([newMessages, , loading]) => {
  // 空会话显示居中输入框；有消息时输入框贴底。
  // 正在拉取历史时按「有会话」处理（贴底），避免先居中再跳到贴底的闪动。
  // 额外追踪 messages.length：发送流程中 App 会先置空 messages（新引用，watch 触发
  // 回 'center'）再对同一数组 push 消息（引用不变，仅靠引用比较不会再次触发），
  // 导致点击预置问题卡片后卡片不消失、输入框不贴底。length 变化补上这次触发。
  composerMode.value = (newMessages.length === 0 && !loading && !isSidebar.value) ? 'center' : 'bottom'
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
const showSessionTime = computed(() => !isSidebar.value && !!props.sessionCreatedAt && props.messages.length > 0)
const showWorkspaceBtn = computed(() => !isSidebar.value && !!props.currentSessionId && !props.workspaceExpanded)
const showTopbar = computed(() =>
  showSessionTime.value || props.todos.length > 0 || showWorkspaceBtn.value
)
// 这一行只剩「展开工作区」按钮（空会话：没有会话时间、没有任务）时改为浮层不占位：
// 有会话时间或任务胶囊的行才是真顶栏，需要占住 44px 与工作区头部对齐。
const topbarFloating = computed(
  () => !showSessionTime.value && props.todos.length === 0 && showWorkspaceBtn.value
)

const emit = defineEmits(['sendMessage', 'stop', 'removeFile', 'createSession', 'approve', 'reject', 'edit', 'update:selectedModel', 'toggleWorkspace', 'clearExpert', 'selectExpert', 'expertPromptApplied', 'openPanel', 'openKnowledge', 'webSearchChange'])
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

function handleSend(message, files, signal, enableDeepThink = true, enableWebSearch = false, skills = [], expertEditId = '', knowledgeBaseIds = []) {
  emit('sendMessage', message, files, signal, enableDeepThink, enableWebSearch, skills, expertEditId, knowledgeBaseIds)
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

// 交互组件里的 sendPrompt(text)（shared/visual/mount.js 派发的冒泡事件）：写进输入框，不自动发送
function onVisualPrompt(event) {
  const text = event && event.detail ? String(event.detail.text || '') : ''
  if (text) composerRef.value?.setMessage(text)
}

function onSend(message, files, signal, enableDeepThink, enableWebSearch, skills, expertEditId = '', knowledgeBaseIds = []) {
  composerMode.value = 'bottom'
  isAtBottom.value = true
  handleSend(message, files, signal, enableDeepThink, enableWebSearch, skills, expertEditId, knowledgeBaseIds)
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

defineExpose({
  focusComposer: () => composerRef.value?.focusInput(),
  setComposerText: (text) => composerRef.value?.setDraft(text),
})
</script>

<style scoped src="../styles/chat.css"></style>

<style scoped>
.compaction-notice {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 80%;
  max-width: 900px;
  box-sizing: border-box;
  padding-inline: var(--chat-message-content-inset, 16px);
  margin: 8px auto;
  color: #64748b;
  font-size: 12px;
  line-height: 20px;
}
.compaction-icon { width: 14px; height: 14px; flex: none; }
</style>
