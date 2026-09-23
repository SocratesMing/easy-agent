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
            @click="$emit('toggle-workspace')"
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
        <div
          v-if="sessionLoading && messages.length === 0"
          class="session-loading-skeleton"
          aria-busy="true"
          aria-label="加载会话"
        >
          <div class="skeleton-block" v-for="i in 3" :key="i"></div>
        </div>

        <div
        v-for="(msg, index) in messages"
        :key="msg.id"
        ref="messageEls"
        class="message-wrapper"
        :class="msg.role"
      >
        <ChatMessage
          :message="msg"
          @remove-file="(file) => handleRemoveFile(file, index)"
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
        @update:selectedModel="$emit('update:selectedModel', $event)"
        @stop="handleStop"
        @create-session="handleCreateSession"
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
  </div>
</template>

<script>
import ChatMessage from './ChatMessage.vue'
import ChatInput from './ChatInput.vue'
import TodoListPanel from './TodoListPanel.vue'
// 首页欢迎语：直接定义在前端（原 src/config.js 已移除）
const APP_WELCOME_TITLE = 'Easy Agent，让工作化繁为简'
export default {
  components: { ChatInput, ChatMessage, TodoListPanel },
  props: {
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
  }
},
  data() {
    return {
      // 首页布局模式：center=空会话时输入框居中，bottom=对话中输入框贴底
      composerMode: 'center',
      deckTop: 0,
      deckVisibleCount: 3, // keep
      currentUserMessageIndex: -1,
      userMessageIndices: [],
      // 是否贴底：用户位于滚动容器底部时为 true，向上滚动查看历史时为 false。
      // 流式更新仅在贴底时自动滚动，避免抢占用户阅读上方内容；用户滚回底部后自动恢复跟随。
      isAtBottom: true,
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

function handleQuickAction(message, index) {
  // 兼容旧引用（已无 deck），直接走预设点击
  onPresetClick(message)
}

function onPresetClick(message) {
  composerMode.value = 'bottom'
  isAtBottom.value = true
  emit('sendMessage', message, [], null, true, false)
}

function onSend(message, files, signal, enableDeepThink, enableWebSearch) {
  composerMode.value = 'bottom'
  isAtBottom.value = true
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
  },
  watch: {
    messages: [
      {
        immediate: true,
        handler(newMessages) {
          // 空会话显示居中输入框；有消息时输入框贴底。
          // 正在拉取历史时按「有会话」处理（贴底），避免先居中再跳到贴底。
          this.composerMode = (newMessages.length === 0 && !this.sessionLoading) ? 'center' : 'bottom'
        }
      },
      {
        deep: true,
        handler(newMessages, oldMessages) {
          // 流式更新或新增消息时，仅在用户贴底时自动滚动；用户向上查看历史时不打断
          if (this.isStreaming || (newMessages?.length || 0) > (oldMessages?.length || 0)) {
            this.scrollToBottom()
          }
          this.$nextTick(() => {
            this.updateUserMessageIndices()
          })
        }
      }
    ],
    sessionLoading(loading) {
      // 加载历史期间贴底，避免居中布局随后跳动
      this.composerMode = (this.messages.length === 0 && !loading) ? 'center' : 'bottom'
    },
    scrollTrigger() {
      // 切换/加载会话时强制贴底
      this.isAtBottom = true
      this.scrollToBottom(true)
    }
  },
  mounted() {
    this.updateUserMessageIndices()
  },
  beforeDestroy() {
  },
  methods: {
    formatSessionTime(isoStr) {
      if (!isoStr) return ''
      const d = new Date(isoStr)
      const y = d.getFullYear()
      const m = String(d.getMonth() + 1).padStart(2, '0')
      const day = String(d.getDate()).padStart(2, '0')
      const h = String(d.getHours()).padStart(2, '0')
      const min = String(d.getMinutes()).padStart(2, '0')
      return `${y}-${m}-${day} ${h}:${min}`
    },
    updateUserMessageIndices() {
      this.userMessageIndices = this.messages
        .map((msg, index) => msg.role === 'user' ? index : -1)
        .filter(index => index !== -1)
        .reverse()
      this.currentUserMessageIndex = -1
    },
    goToPrevUserMessage() {
      if (this.userMessageIndices.length === 0) return

      if (this.currentUserMessageIndex === -1) {
        this.currentUserMessageIndex = 0
      } else if (this.currentUserMessageIndex < this.userMessageIndices.length - 1) {
        this.currentUserMessageIndex++
      }

      const targetIndex = this.userMessageIndices[this.currentUserMessageIndex]
      const els = this.$refs.messageEls || []

      if (targetIndex !== undefined && els[targetIndex]) {
        els[targetIndex].scrollIntoView({ behavior: 'smooth', block: 'center' })
      }
    },
    goToNextUserMessage() {
      if (this.currentUserMessageIndex > 0) {
        // 还有更靠后的用户问题：逐条向下跳转
        this.currentUserMessageIndex--
        const targetIndex = this.userMessageIndices[this.currentUserMessageIndex]
        const els = this.$refs.messageEls || []
        if (targetIndex !== undefined && els[targetIndex]) {
          els[targetIndex].scrollIntoView({ behavior: 'smooth', block: 'center' })
        }
      } else {
        // 已到最后一个用户问题（或未经过导航）：直接滚动到会话底部
        this.currentUserMessageIndex = -1
        this.$nextTick(() => {
          const el = this.$refs.messagesRef
          if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
        })
      }
    },
    handleSend(message, files, signal, enableDeepThink = true, enableWebSearch = false) {
      this.$emit('send-message', message, files, signal, enableDeepThink, enableWebSearch)
    },
    handleRemoveFile(file, messageIndex) {
      // 从事件参数中获取file，然后从messages中获取对应的message
      const message = this.messages[messageIndex]
      this.$emit('remove-file', message, messageIndex, file)
    },
    handleRetry(content) {
      // 向上传递重试事件
      this.$emit('retry', content)
    },
    handleApprove() {
      this.$emit('approve')
    },
    handleReject() {
      this.$emit('reject')
    },
    handleStop() {
      this.$emit('stop')
    },
    handleCreateSession() {
      this.$emit('create-session')
    },
    handleQuickAction(message, index) {
      // 兼容旧引用（已无 deck），直接走预设点击
      this.onPresetClick(message)
    },
    onPresetClick(message) {
      this.composerMode = 'bottom'
      this.isAtBottom = true
      this.$emit('send-message', message, [], null, true, false)
    },
    onSend(message, files, signal, enableDeepThink, enableWebSearch) {
      this.composerMode = 'bottom'
      this.isAtBottom = true
      this.handleSend(message, files, signal, enableDeepThink, enableWebSearch)
    },
    handleScroll() {
      const el = this.$refs.messagesRef
      if (!el) return
      const threshold = 80
      const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < threshold
      this.isAtBottom = atBottom
      // 手动滚动回到最底部时，同步复位「上一个/下一个问题」导航索引：
      // 此前索引停留在历史问题位置，会导致回到底部后「回到上一个问题」按钮
      // 按旧索引计算而消失（按钮状态与真实滚动位置脱节）。
      if (atBottom && this.currentUserMessageIndex !== -1) {
        this.currentUserMessageIndex = -1
      }
    },
    scrollToBottom(force = false) {
      this.$nextTick(() => {
        const el = this.$refs.messagesRef
        if (!el) return
        if (force || this.isAtBottom) {
          el.scrollTop = el.scrollHeight
        }
      })
    }
  }
}
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
