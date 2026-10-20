<template>
  <div
    class="ais-root"
    :class="{ 'is-docked': isDockedOpen, 'is-embedded': embedded }"
    :style="rootStyle"
  >
    <!-- 唤起按钮：贴右边缘，可上下拖动（位置持久化），Ctrl/⌘ + J 同样可唤起 -->
    <button
      v-show="!open && feat.launcher"
      ref="launcherRef"
      type="button"
      class="ais-launcher"
      :class="{ 'is-dragging': launcherDragging, 'has-unread': unread, 'is-busy': anySidebarStreaming }"
      :style="{ top: launcherTop + 'px' }"
      :title="`AI 助手（${shortcutLabel}）`"
      aria-label="打开 AI 助手"
      @mousedown="startLauncherDrag"
      @click="onLauncherClick"
    >
      <svg class="ais-launcher-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3z"></path>
        <path d="M18.5 15.5l.7 1.9 1.9.7-1.9.7-.7 1.9-.7-1.9-1.9-.7 1.9-.7.7-1.9z"></path>
      </svg>
      <span class="ais-launcher-label">{{ launcherLabel }}</span>
      <span v-if="unread || anySidebarStreaming" class="ais-launcher-dot"></span>
    </button>

    <!-- 划词提问：页面中选中文本后出现，点击带着选中内容打开侧边栏 -->
    <button
      v-if="selectionAsk.visible"
      type="button"
      class="ais-selection-ask"
      :style="{ left: selectionAsk.x + 'px', top: selectionAsk.y + 'px' }"
      @mousedown.prevent
      @click="askWithSelection"
    >
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3z"></path>
      </svg>
      问 AI
    </button>

    <transition name="ais-slide">
      <aside
        v-show="open"
        ref="panelRef"
        class="ais-panel"
        :class="{
          'is-floating': !isDocked,
          'is-docked': isDocked,
          'is-collapsed': collapsed,
          'is-resizing': resizing,
        }"
        :style="panelStyle"
        role="complementary"
        aria-label="AI 助手侧边栏"
      >
        <div
          v-if="!collapsed"
          class="ais-resizer"
          title="拖动调整宽度，双击恢复默认"
          @mousedown.prevent="startResize"
          @dblclick="resetWidth"
        ></div>

        <!-- 折叠态：窄条，仅保留常用入口 -->
        <div v-if="collapsed" class="ais-rail">
          <button type="button" class="ais-icon-btn" title="展开" @click="expand">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"></polyline></svg>
          </button>
          <span class="ais-rail-mark" :class="{ 'is-busy': isStreaming }" :title="isStreaming ? '正在生成…' : 'AI 助手'">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3z"></path></svg>
          </span>
          <button type="button" class="ais-icon-btn" title="新建会话" @click="expand(); startNewChat()">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
          </button>
          <button type="button" class="ais-icon-btn" title="历史会话" @click="expand(); showHistory = true">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"></circle><polyline points="12 7 12 12 15 14"></polyline></svg>
          </button>
          <span v-if="unread" class="ais-rail-unread" title="有新回复"></span>
          <div class="ais-rail-spacer"></div>
          <button type="button" class="ais-icon-btn" title="关闭" @click="close">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
          </button>
        </div>

        <!-- 折叠时只隐藏不卸载：保留滚动位置、输入草稿与进行中的流式渲染 -->
        <div v-show="!collapsed" class="ais-main">
          <header class="ais-header">
            <button
              type="button"
              class="ais-title"
              :class="{ active: showHistory }"
              :title="showHistory ? '收起历史会话' : '查看历史会话'"
              @click="showHistory = !showHistory"
            >
              <span class="ais-title-mark" :class="{ 'is-busy': isStreaming }">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3z"></path></svg>
              </span>
              <span class="ais-title-text">{{ currentTitle }}</span>
              <svg class="ais-title-arrow" :class="{ open: showHistory }" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg>
            </button>
            <div class="ais-header-actions">
              <button type="button" class="ais-icon-btn" title="新建会话" @click="startNewChat">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
              </button>
              <button
                v-if="feat.openInMain"
                type="button"
                class="ais-icon-btn"
                title="在主页面打开此会话"
                :disabled="!currentSessionId"
                @click="openInMain"
              >
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line></svg>
              </button>
              <button
                v-if="feat.dock"
                type="button"
                class="ais-icon-btn"
                :class="{ active: isDocked }"
                :title="isDocked ? '切换为悬浮（覆盖在页面上方）' : '切换为固定（与页面并排）'"
                :disabled="isNarrowViewport"
                @click="toggleMode"
              >
                <svg v-if="isDocked" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"></rect><line x1="15" y1="3" x2="15" y2="21"></line></svg>
                <svg v-else xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"></rect><rect x="12" y="7" width="6" height="10" rx="1"></rect></svg>
              </button>
              <button type="button" class="ais-icon-btn" title="折叠" @click="collapse">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="13 17 18 12 13 7"></polyline><polyline points="6 17 11 12 6 7"></polyline></svg>
              </button>
              <button type="button" class="ais-icon-btn" :title="`关闭（${shortcutLabel}）`" @click="close">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
              </button>
            </div>
          </header>

          <div class="ais-body">
            <!-- 历史会话：与主页面共用同一份会话列表 -->
            <transition name="ais-fade">
              <div v-if="showHistory" class="ais-history">
                <div class="ais-history-search">
                  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
                  <input
                    ref="historySearchRef"
                    v-model="historyQuery"
                    type="text"
                    placeholder="搜索会话"
                    @keydown.esc="showHistory = false"
                  />
                </div>
                <div class="ais-history-list">
                  <div v-if="historyGroups.length === 0" class="ais-history-empty">
                    {{ historyQuery ? '没有匹配的会话' : '还没有会话' }}
                  </div>
                  <div v-for="group in historyGroups" :key="group.key" class="ais-history-group">
                    <div class="ais-history-group-title">{{ group.label }}</div>
                    <div
                      v-for="s in group.items"
                      :key="group.key + s.session_id"
                      class="ais-history-item"
                      :class="{ active: s.session_id === currentSessionId }"
                      @click="selectFromHistory(s.session_id)"
                    >
                      <span v-if="isSessionStreaming(s.session_id)" class="ais-history-live" title="进行中"></span>
                      <input
                        v-if="renamingId === s.session_id"
                        ref="renameInputRef"
                        v-model="renameText"
                        class="ais-history-rename"
                        @click.stop
                        @keydown.enter.prevent="commitRename"
                        @keydown.esc.prevent="renamingId = null"
                        @blur="commitRename"
                      />
                      <span v-else class="ais-history-name" :title="sessionLabel(s)" @dblclick.stop="beginRename(s)">{{ sessionLabel(s) }}</span>
                      <span class="ais-history-time">{{ formatRelative(s.updated_at || s.created_at) }}</span>
                      <span class="ais-history-ops" @click.stop>
                        <button type="button" :title="s.pinned ? '取消置顶' : '置顶'" :class="{ on: s.pinned }" @click="handleTogglePin(s.session_id)">
                          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="17" x2="12" y2="22"></line><path d="M5 17h14v-1.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V6h1a2 2 0 0 0 0-4H8a2 2 0 0 0 0 4h1v4.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24z"></path></svg>
                        </button>
                        <button type="button" title="重命名" @click="beginRename(s)">
                          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"></path><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"></path></svg>
                        </button>
                        <button
                          type="button"
                          :title="confirmDeleteId === s.session_id ? '再次点击确认删除' : '删除'"
                          class="danger"
                          :class="{ confirming: confirmDeleteId === s.session_id }"
                          @click="requestDelete(s.session_id)"
                        >
                          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"></path><path d="M10 11v6"></path><path d="M14 11v6"></path></svg>
                        </button>
                      </span>
                    </div>
                  </div>
                </div>
              </div>
            </transition>

            <Chat
              ref="chatRef"
              variant="sidebar"
              draft-key="easy_agent_sidebar_draft"
              :messages="messages"
              :sessionLoading="sessionLoading"
              :currentSessionId="currentSessionId"
              :isStreaming="isStreaming"
              :scrollTrigger="scrollTrigger"
              :sessionUsage="sessionUsage"
              :sessionDuration="sessionDuration"
              :iterationCount="iterationCount"
              :todos="currentTodos"
              :workspaceExpanded="true"
              :show-manage-entry="feat.openInMain"
              :models="models"
              :selectedModel="selectedModel"
              @update:selectedModel="selectedModel = $event"
              @send-message="handleSendMessage"
              @create-session="ensureCurrentSession"
              @remove-file="handleRemoveFile"
              @stop="handleStop"
              @retry="handleRetry"
              @approve="handleToolApproval('approve', $event)"
              @reject="handleToolApproval('reject', $event)"
              @open-panel="openManagePanel"
              @open-knowledge="openManagePanel('knowledge')"
            >
              <template #welcome>
                <div class="ais-welcome">
                  <div class="ais-welcome-mark">
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3z"></path><path d="M18.5 15.5l.7 1.9 1.9.7-1.9.7-.7 1.9-.7-1.9-1.9-.7 1.9-.7.7-1.9z"></path></svg>
                  </div>
                  <h3>{{ welcomeTitle }}</h3>
                  <p class="ais-welcome-sub">
                    已了解你正在
                    <strong>{{ pageContext ? pageContext.title : '当前页面' }}</strong>，
                    输入 <kbd>/</kbd> 可调用技能
                  </p>
                  <div v-if="suggestions.length" class="ais-suggestions">
                    <button
                      v-for="(q, i) in suggestions"
                      :key="i"
                      type="button"
                      class="ais-suggestion"
                      @click="sendQuick(q)"
                    >
                      <span>{{ q }}</span>
                      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"></line><polyline points="12 5 19 12 12 19"></polyline></svg>
                    </button>
                  </div>
                </div>
              </template>

              <template #composer-prefix>
                <div v-if="error" class="ais-error" role="alert">
                  <span>{{ error }}</span>
                  <button type="button" aria-label="关闭提示" @click="error = null">×</button>
                </div>
                <div class="ais-context-bar">
                  <span
                    v-if="pageContext && includeContext"
                    class="ais-chip"
                    :class="{ 'is-sent': contextAlreadySent }"
                    :title="contextPreview"
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"></path><circle cx="12" cy="10" r="3"></circle></svg>
                    <span class="ais-chip-text">{{ pageContext.title }}</span>
                    <span v-if="contextAlreadySent" class="ais-chip-note">已提供</span>
                    <button type="button" class="ais-chip-x" title="不附带页面上下文" @click="includeContext = false">×</button>
                  </span>
                  <button
                    v-else-if="pageContext"
                    type="button"
                    class="ais-chip is-ghost"
                    title="把当前页面信息提供给 AI"
                    @click="includeContext = true"
                  >
                    + 附带页面上下文
                  </button>
                  <span v-if="quote" class="ais-chip is-quote" :title="quote">
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor"><path d="M7 7h4v4H8c0 2 1 3 3 3v2c-3 0-5-2-5-5V7zm8 0h4v4h-3c0 2 1 3 3 3v2c-3 0-5-2-5-5V7z"></path></svg>
                    <span class="ais-chip-text">{{ quote }}</span>
                    <button type="button" class="ais-chip-x" title="移除引用" @click="quote = ''">×</button>
                  </span>
                  <template v-if="quote && !isStreaming">
                    <button
                      v-for="a in quoteActions"
                      :key="a"
                      type="button"
                      class="ais-chip is-action"
                      @click="sendQuick(a)"
                    >{{ a }}</button>
                  </template>
                </div>
              </template>
            </Chat>
          </div>
        </div>
      </aside>
    </transition>

    <!-- 拖拽改宽期间盖住整个页面：避免 iframe（PDF/Office 预览）吞掉 mousemove -->
    <div v-if="resizing || launcherDragging" class="ais-drag-mask" :class="{ 'is-col': resizing }"></div>
  </div>
</template>

<script>
import Chat from './Chat.vue'
import chatSessionMixin from '../mixins/chatSession.js'
import { chatStore } from '../store/chatStore.js'
import { ensureSkillDirectoryLoaded, skillFreeTitle } from '../../skills/store/skillDirectoryStore.js'
import { formatPageContext, setExternalPageContext } from '../../../utils/pageContext.js'
import { formatRelativeTime } from '../../../utils/relativeTime.js'
import { eventTarget } from '../../../utils/domEvent.js'

const PREFS_KEY = 'easy_agent_ai_sidebar_prefs'
const PAGE_SESSIONS_KEY = 'easy_agent_ai_page_sessions'
const DEFAULT_WIDTH = 440
const MIN_WIDTH = 340
const RAIL_WIDTH = 52
// 小于该宽度的视口强制悬浮全宽显示
const NARROW_VIEWPORT = 768
const MAX_PAGE_SESSIONS = 20
const QUOTE_MAX = 2000
const DEFAULT_FEATURES = {
  launcher: true,
  selectionAsk: true,
  shortcut: true,
  dock: true,
  openInMain: true,
}

function readJson(storage, key, fallback) {
  try {
    const raw = storage.getItem(key)
    return raw ? JSON.parse(raw) : fallback
  } catch (_) {
    return fallback
  }
}

function writeJson(storage, key, value) {
  try {
    storage.setItem(key, JSON.stringify(value))
  } catch (_) { /* 存储不可用时忽略 */ }
}

export default {
  name: 'AiSidebar',
  components: { Chat },
  mixins: [chatSessionMixin],
  props: {
    // 当前页面上下文（App 根据所在页面生成），见 utils/pageContext.js
    pageContext: {
      type: Object,
      default: null,
    },
    // 嵌入第三方页面（SDK）：根节点 fixed 定位，固定模式由宿主据 layout-change 让出空间
    embedded: {
      type: Boolean,
      default: false,
    },
    // 功能开关，未传的项取 DEFAULT_FEATURES
    features: {
      type: Object,
      default: () => ({}),
    },
    // 是否挂载 window.EasyAgentAI（SDK 由实例方法代替，避免多实例互相覆盖）
    exposeGlobal: {
      type: Boolean,
      default: true,
    },
    // 用户未切换过模式时的初始模式：float | dock
    defaultMode: {
      type: String,
      default: 'float',
    },
    launcherLabel: {
      type: String,
      default: '问 AI',
    },
    welcomeTitle: {
      type: String,
      default: '有什么可以帮你？',
    },
  },
  data() {
    const prefs = readJson(localStorage, PREFS_KEY, {})
    const savedMode = prefs.mode === 'dock' || prefs.mode === 'float' ? prefs.mode : this.defaultMode
    return {
      open: false,
      collapsed: false,
      mode: savedMode === 'dock' ? 'dock' : 'float',
      width: Number(prefs.width) || DEFAULT_WIDTH,
      launcherTop: Number(prefs.launcherTop) || Math.round(window.innerHeight * 0.62),
      viewportWidth: window.innerWidth,
      resizing: false,
      launcherDragging: false,
      launcherMoved: false,
      selectedModel: chatStore.activeModel,
      showHistory: false,
      historyQuery: '',
      renamingId: null,
      renameText: '',
      confirmDeleteId: null,
      includeContext: true,
      // 每个会话最近一次发送给模型的上下文：内容未变化时不重复附带
      sentContexts: {},
      quote: '',
      quoteActions: ['解释这段内容', '总结要点', '翻译成英文'],
      selectionAsk: { visible: false, x: 0, y: 0, text: '' },
      unread: false,
      pageSessions: {},
    }
  },
  computed: {
    models() {
      return chatStore.models
    },
    feat() {
      return { ...DEFAULT_FEATURES, ...this.features }
    },
    isNarrowViewport() {
      return this.viewportWidth < NARROW_VIEWPORT
    },
    isDocked() {
      return this.feat.dock && this.mode === 'dock' && !this.isNarrowViewport
    },
    isDockedOpen() {
      return this.open && this.isDocked
    },
    maxWidth() {
      return Math.max(MIN_WIDTH, Math.min(960, this.viewportWidth - 360))
    },
    effectiveWidth() {
      if (this.collapsed) return RAIL_WIDTH
      if (this.isNarrowViewport) return this.viewportWidth
      return Math.min(Math.max(this.width, MIN_WIDTH), this.maxWidth)
    },
    // 固定模式下根节点参与 App 的 flex 分栏（占位推开页面内容）；悬浮模式不占位
    rootStyle() {
      if (this.embedded) return null
      return { width: this.isDockedOpen ? this.effectiveWidth + 'px' : '0px' }
    },
    // 固定模式下需要宿主页面让出的宽度（嵌入模式通过 layout-change 通知宿主）
    dockWidth() {
      return this.isDockedOpen ? this.effectiveWidth : 0
    },
    panelStyle() {
      return { width: this.effectiveWidth + 'px' }
    },
    shortcutLabel() {
      return /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘ J' : 'Ctrl + J'
    },
    currentTitle() {
      if (!this.currentSessionId) return '新对话'
      const s = this.sessions.find(x => x.session_id === this.currentSessionId)
      return s ? this.sessionLabel(s) : '新会话'
    },
    anySidebarStreaming() {
      return Object.keys(this.attachedStreamingSessions).some(sid => this.isSessionStreaming(sid)) || this.isStreaming
    },
    suggestions() {
      return (this.pageContext && this.pageContext.suggestions) || []
    },
    pageKey() {
      return (this.pageContext && this.pageContext.key) || 'chat'
    },
    contextText() {
      return this.pageContext ? formatPageContext(this.pageContext) : ''
    },
    contextPreview() {
      return this.contextText ? `将随消息提供给 AI：\n${this.contextText}` : ''
    },
    contextAlreadySent() {
      const sid = this.currentSessionId
      return !!sid && this.sentContexts[sid] === this.contextText
    },
    pageSessionsStorageKey() {
      return `${PAGE_SESSIONS_KEY}:${chatStore.username || 'default'}`
    },
    // 历史分组：置顶 / 当前页面 / 今天 / 近 7 天 / 更早
    historyGroups() {
      const q = this.historyQuery.trim().toLowerCase()
      // 检索按展示用标题匹配（已去掉 /技能名 前缀），保证「看到什么就能搜到什么」
      const list = this.sessions.filter(s => !q || this.sessionLabel(s).toLowerCase().includes(q))
      const pageIds = (this.pageSessions[this.pageKey] || []).filter(id => list.some(s => s.session_id === id))
      const groups = []
      const pinned = list.filter(s => s.pinned)
      if (pinned.length) groups.push({ key: 'pinned', label: '置顶', items: pinned })
      if (pageIds.length) {
        const items = pageIds.map(id => list.find(s => s.session_id === id)).filter(Boolean)
        groups.push({ key: 'page', label: `当前页面 · ${this.pageContext ? this.pageContext.title : ''}`, items })
      }
      const now = new Date()
      const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
      const weekAgo = startOfToday - 6 * 86400000
      const today = []
      const week = []
      const older = []
      for (const s of list) {
        if (s.pinned) continue
        const t = new Date(s.updated_at || s.created_at || 0).getTime()
        if (t >= startOfToday) today.push(s)
        else if (t >= weekAgo) week.push(s)
        else older.push(s)
      }
      if (today.length) groups.push({ key: 'today', label: '今天', items: today })
      if (week.length) groups.push({ key: 'week', label: '近 7 天', items: week })
      if (older.length) groups.push({ key: 'older', label: '更早', items: older })
      return groups
    },
  },
  watch: {
    currentSessionId(sid, oldSid) {
      if (!sid) return
      // 新会话首条消息附带的上下文：会话 id 由 start 事件分配后归到该会话名下
      if (!oldSid && this.sentContexts.__new__ !== undefined) {
        this.$set(this.sentContexts, sid, this.sentContexts.__new__)
        this.$delete(this.sentContexts, '__new__')
      }
      this.rememberPageSession(sid)
    },
    isStreaming(now, before) {
      if (before && !now && (!this.open || this.collapsed)) this.unread = true
    },
    showHistory(v) {
      this.confirmDeleteId = null
      if (v) this.$nextTick(() => this.$refs.historySearchRef && this.$refs.historySearchRef.focus())
    },
    pageContext(next, prev) {
      // 页面变化后重新默认附带上下文
      if (!prev || !next || next.key !== prev.key) this.includeContext = true
    },
    open(v) {
      this.$emit('open-change', v)
    },
    dockWidth: {
      immediate: true,
      handler(width) {
        this.$emit('layout-change', { dockWidth: width })
      },
    },
  },
  created() {
    // 技能目录：历史会话标题里的 /技能名 前缀要按目录名反查才能去掉
    ensureSkillDirectoryLoaded()
    this.unwatchContextLength = this.$watch(
      () => chatStore.contextLength,
      (len) => {
        if (len && !this.sessionUsage.context_length) this.sessionUsage.context_length = len
      },
      { immediate: true }
    )
    this.unwatchActiveModel = this.$watch(
      () => chatStore.activeModel,
      (name) => {
        if (!this.selectedModel && name) this.selectedModel = name
      }
    )
    this.pageSessions = readJson(localStorage, this.pageSessionsStorageKey, {})
  },
  mounted() {
    window.addEventListener('keydown', this.onGlobalKeydown)
    window.addEventListener('resize', this.onWindowResize)
    document.addEventListener('mouseup', this.onDocumentMouseUp)
    document.addEventListener('mousedown', this.onDocumentMouseDown)
    window.addEventListener('scroll', this.hideSelectionAsk, true)
    this.clampLauncher()
    // 供宿主页面调用：window.EasyAgentAI.open({ prompt, context, newSession })
    if (this.exposeGlobal) {
      window.EasyAgentAI = {
        open: (opts) => this.openSidebar(opts || {}),
        close: () => this.close(),
        toggle: () => this.toggle(),
        setContext: (text) => setExternalPageContext(text),
      }
    }
  },
  beforeDestroy() {
    window.removeEventListener('keydown', this.onGlobalKeydown)
    window.removeEventListener('resize', this.onWindowResize)
    document.removeEventListener('mouseup', this.onDocumentMouseUp)
    document.removeEventListener('mousedown', this.onDocumentMouseDown)
    window.removeEventListener('scroll', this.hideSelectionAsk, true)
    this.stopResize()
    this.stopLauncherDrag()
    if (this.unwatchContextLength) this.unwatchContextLength()
    if (this.unwatchActiveModel) this.unwatchActiveModel()
    if (this.currentAbortController) this.currentAbortController.abort()
    if (this.exposeGlobal && window.EasyAgentAI) delete window.EasyAgentAI
  },
  methods: {
    // ── chatSessionMixin 钩子 ─────────────────────────────────────────
    beforeSessionChange() {
      this.showHistory = false
    },
    getPageContextForSend() {
      const quote = this.quote
      this.quote = ''
      if (!this.includeContext || !this.pageContext) {
        return quote ? formatPageContext({ title: '页面选中内容', description: '' }, { quote }) : null
      }
      const text = this.contextText
      const key = this.currentSessionId || '__new__'
      if (!quote && this.sentContexts[key] === text) return null
      this.$set(this.sentContexts, key, text)
      return quote ? formatPageContext(this.pageContext, { quote }) : text
    },
    // 侧边栏里删除了正在展示的会话：回到新对话，而不是跳到列表第一个会话
    async fallbackAfterSessionRemoved() {
      this.currentSessionId = null
      this.loadedSessionId = null
      this.messages = []
      await this.handleCreateSession()
    },

    // ── 打开 / 关闭 / 折叠 ────────────────────────────────────────────
    openSidebar(opts = {}) {
      const wasOpen = this.open
      this.open = true
      this.collapsed = false
      this.unread = false
      this.hideSelectionAsk()
      if (opts.context) setExternalPageContext(opts.context)
      if (opts.newSession) {
        this.startNewChat()
      } else if (!wasOpen) {
        this.preferPageSession()
      }
      this.$nextTick(() => {
        // 面板隐藏期间（display: none）无法滚动，打开后补一次贴底
        if (!wasOpen) this.scrollTrigger++
        if (opts.prompt) this.setComposer(opts.prompt)
        else this.focusComposer()
      })
    },
    close() {
      this.open = false
      this.showHistory = false
    },
    toggle() {
      if (this.open) this.close()
      else this.openSidebar()
    },
    collapse() {
      this.collapsed = true
      this.showHistory = false
    },
    expand() {
      this.collapsed = false
      this.unread = false
      this.$nextTick(() => {
        this.scrollTrigger++
        this.focusComposer()
      })
    },
    toggleMode() {
      this.mode = this.mode === 'dock' ? 'float' : 'dock'
      this.savePrefs()
    },
    startNewChat() {
      if (!this.currentSessionId && this.messages.length === 0) {
        this.focusComposer()
        return
      }
      this.handleCreateSession()
      this.includeContext = true
      this.$nextTick(() => this.focusComposer())
    },
    selectFromHistory(sessionId) {
      if (this.renamingId) return
      if (sessionId !== this.currentSessionId) this.handleSelectSession(sessionId)
      this.showHistory = false
    },
    // 供 SDK 调用：打开侧边栏并直接发送一条消息
    async sendFromHost(text) {
      if (!text) return
      this.openSidebar()
      if (this.isStreaming) {
        this.setComposer(text)
        return
      }
      await this.handleSendMessage(text)
    },
    openInMain() {
      if (!this.currentSessionId) return
      this.$emit('open-in-main', this.currentSessionId)
      if (!this.isDocked) this.collapse()
    },
    // 「+」菜单二级面板的「在管理面板中打开」：tab = experts / skills / connectors / knowledge
    openManagePanel(tab) {
      this.$emit('open-panel', tab)
      if (!this.isDocked) this.collapse()
    },
    focusComposer() {
      const chat = this.$refs.chatRef
      if (chat) chat.focusComposer()
    },
    setComposer(text) {
      const chat = this.$refs.chatRef
      if (chat) chat.setComposerText(text)
    },
    sendQuick(text) {
      if (this.isStreaming) return
      this.handleSendMessage(text)
    },

    // ── 当前页面会话：同一页面再次唤起时优先回到该页面最近的会话 ─────────
    preferPageSession() {
      if (this.isStreaming) return
      const exists = id => this.sessions.some(s => s.session_id === id)
      const ids = (this.pageSessions[this.pageKey] || []).filter(exists)
      const target = ids[0]
      if (target) {
        if (target !== this.currentSessionId) this.handleSelectSession(target)
      } else if (this.currentSessionId || this.messages.length) {
        // 该页面还没有会话：开启一个新会话，首条消息附带本页面上下文
        this.handleCreateSession()
        this.includeContext = true
      }
    },
    rememberPageSession(sid) {
      const key = this.pageKey
      const list = (this.pageSessions[key] || []).filter(id => id !== sid)
      list.unshift(sid)
      this.$set(this.pageSessions, key, list.slice(0, MAX_PAGE_SESSIONS))
      writeJson(localStorage, this.pageSessionsStorageKey, this.pageSessions)
    },

    /**
     * 会话展示用标题：历史会话标题是首条消息原文，带技能时会形如
     * `/demo-skill 测试对话`，这里去掉技能前缀只留用户真正输入的内容。
     */
    sessionLabel(session) {
      return skillFreeTitle(session && session.title) || '新会话'
    },

    // ── 历史会话操作 ──────────────────────────────────────────────────
    beginRename(session) {
      this.renamingId = session.session_id
      this.renameText = this.sessionLabel(session)
      this.$nextTick(() => {
        const refs = this.$refs.renameInputRef
        const el = Array.isArray(refs) ? refs[0] : refs
        if (el) { el.focus(); el.select() }
      })
    },
    async commitRename() {
      const id = this.renamingId
      if (!id) return
      this.renamingId = null
      const title = this.renameText.trim()
      const s = this.sessions.find(x => x.session_id === id)
      if (title && s && title !== s.title) await this.handleRenameSession(id, title)
    },
    requestDelete(sessionId) {
      if (this.confirmDeleteId === sessionId) {
        this.confirmDeleteId = null
        this.handleDeleteSession(sessionId)
        return
      }
      this.confirmDeleteId = sessionId
      clearTimeout(this.confirmTimer)
      this.confirmTimer = setTimeout(() => { this.confirmDeleteId = null }, 3000)
    },
    formatRelative(iso) {
      return formatRelativeTime(iso)
    },

    // ── 拖动分隔栏改变宽度 ────────────────────────────────────────────
    startResize(e) {
      if (this.isNarrowViewport) return
      this.resizing = true
      this.resizeStartX = e.clientX
      this.resizeStartWidth = this.effectiveWidth
      document.addEventListener('mousemove', this.onResizeMove)
      document.addEventListener('mouseup', this.stopResize)
    },
    onResizeMove(e) {
      if (!this.resizing) return
      const next = this.resizeStartWidth + (this.resizeStartX - e.clientX)
      this.width = Math.min(Math.max(next, MIN_WIDTH), this.maxWidth)
    },
    stopResize() {
      document.removeEventListener('mousemove', this.onResizeMove)
      document.removeEventListener('mouseup', this.stopResize)
      if (this.resizing) {
        this.resizing = false
        this.savePrefs()
      }
    },
    resetWidth() {
      this.width = DEFAULT_WIDTH
      this.savePrefs()
    },

    // ── 唤起按钮：上下拖动 ────────────────────────────────────────────
    startLauncherDrag(e) {
      if (e.button !== 0) return
      this.launcherMoved = false
      this.dragStartY = e.clientY
      this.dragStartTop = this.launcherTop
      document.addEventListener('mousemove', this.onLauncherMove)
      document.addEventListener('mouseup', this.stopLauncherDrag)
    },
    onLauncherMove(e) {
      const dy = e.clientY - this.dragStartY
      if (!this.launcherMoved && Math.abs(dy) < 4) return
      this.launcherMoved = true
      this.launcherDragging = true
      this.launcherTop = this.dragStartTop + dy
      this.clampLauncher()
    },
    stopLauncherDrag() {
      document.removeEventListener('mousemove', this.onLauncherMove)
      document.removeEventListener('mouseup', this.stopLauncherDrag)
      if (this.launcherDragging) {
        this.launcherDragging = false
        this.savePrefs()
      }
    },
    onLauncherClick() {
      // 拖动结束时浏览器仍会派发 click：拖动过则不当作点击
      if (this.launcherMoved) {
        this.launcherMoved = false
        return
      }
      this.openSidebar()
    },
    clampLauncher() {
      const max = window.innerHeight - 56
      this.launcherTop = Math.min(Math.max(this.launcherTop, 12), Math.max(12, max))
    },

    // ── 划词提问 ──────────────────────────────────────────────────────
    onDocumentMouseDown(e) {
      const target = eventTarget(e)
      if (this.selectionAsk.visible && !(target && target.closest && target.closest('.ais-selection-ask'))) {
        this.hideSelectionAsk()
      }
    },
    onDocumentMouseUp(e) {
      if (!this.feat.selectionAsk) return
      const target = eventTarget(e)
      if (!target || !target.closest) return
      if (target.closest('.ais-panel, .ais-launcher, .ais-selection-ask, input, textarea, [contenteditable="true"]')) return
      // 等浏览器完成选区更新后再读取
      setTimeout(() => {
        const sel = window.getSelection()
        const text = sel ? sel.toString().trim() : ''
        if (!sel || sel.rangeCount === 0 || text.length < 2) return
        const rect = sel.getRangeAt(0).getBoundingClientRect()
        if (!rect || (!rect.width && !rect.height)) return
        this.selectionAsk = {
          visible: true,
          x: Math.min(rect.right + 6, window.innerWidth - 90),
          y: Math.min(rect.bottom + 6, window.innerHeight - 40),
          text: text.slice(0, QUOTE_MAX),
        }
      }, 0)
    },
    hideSelectionAsk() {
      if (this.selectionAsk.visible) this.selectionAsk = { visible: false, x: 0, y: 0, text: '' }
    },
    askWithSelection() {
      const text = this.selectionAsk.text
      this.hideSelectionAsk()
      if (window.getSelection) window.getSelection().removeAllRanges()
      this.openSidebar()
      this.quote = text
    },

    // ── 全局事件 ──────────────────────────────────────────────────────
    onGlobalKeydown(e) {
      if (!this.feat.shortcut) return
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && (e.key === 'j' || e.key === 'J')) {
        e.preventDefault()
        if (this.open && this.collapsed) this.expand()
        else this.toggle()
      }
    },
    onWindowResize() {
      this.viewportWidth = window.innerWidth
      this.clampLauncher()
    },
    savePrefs() {
      writeJson(localStorage, PREFS_KEY, {
        mode: this.mode,
        width: Math.round(this.width),
        launcherTop: Math.round(this.launcherTop),
      })
    },
  },
}
</script>

<style scoped>
/* 根节点：固定模式下作为 App flex 分栏的一列（宽度 = 面板宽度），悬浮模式宽度为 0 */
.ais-root {
  position: relative;
  flex-shrink: 0;
  height: 100%;
  transition: width 0.22s cubic-bezier(0.22, 1, 0.36, 1);
}

.ais-root.is-docked {
  /* 高于设置/用户管理弹窗遮罩（1000），弹窗打开时也能继续使用 AI */
  z-index: 1001;
}

/* 嵌入第三方页面：不参与宿主布局，固定模式的面板贴视口右侧，宿主自行让出宽度 */
.ais-root.is-embedded {
  position: fixed;
  top: 0;
  right: 0;
  width: 0;
  height: 100vh;
}

.ais-root.is-embedded .ais-panel.is-docked {
  position: fixed;
  height: 100vh;
}

/* ── 唤起按钮 ── */
.ais-launcher {
  position: fixed;
  right: 0;
  z-index: 1040;
  display: flex;
  align-items: center;
  gap: 6px;
  height: 40px;
  padding: 0 12px 0 11px;
  border: 1px solid color-mix(in srgb, var(--accent-color, #0ea5e9) 30%, transparent);
  border-right: none;
  border-radius: 20px 0 0 20px;
  background: linear-gradient(135deg, #0ea5e9, #06b6d4);
  color: #ffffff;
  cursor: pointer;
  box-shadow: 0 6px 18px rgba(14, 165, 233, 0.32);
  transform: translateX(calc(100% - 42px));
  transition: transform 0.25s cubic-bezier(0.22, 1, 0.36, 1), box-shadow 0.2s ease;
  user-select: none;
}

.ais-launcher:hover,
.ais-launcher:focus-visible,
.ais-launcher.is-dragging {
  transform: translateX(0);
  box-shadow: 0 8px 24px rgba(14, 165, 233, 0.42);
}

.ais-launcher.is-dragging {
  cursor: grabbing;
}

.ais-launcher-icon {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
}

.ais-launcher.is-busy .ais-launcher-icon {
  animation: ais-spin 2.4s linear infinite;
}

.ais-launcher-label {
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.02em;
  white-space: nowrap;
}

.ais-launcher-dot {
  position: absolute;
  top: 6px;
  left: 8px;
  width: 8px;
  height: 8px;
  border: 2px solid #ffffff;
  border-radius: 50%;
  background: #f97316;
}

.ais-launcher.is-busy:not(.has-unread) .ais-launcher-dot {
  background: #22c55e;
  animation: ais-pulse 1.4s ease-in-out infinite;
}

/* ── 划词提问气泡 ── */
.ais-selection-ask {
  position: fixed;
  z-index: 1045;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 5px 10px;
  border: 1px solid var(--border-color, #e2e8f0);
  border-radius: 999px;
  background: var(--bg-secondary, #ffffff);
  color: var(--accent-color, #0ea5e9);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  box-shadow: 0 6px 18px rgba(15, 23, 42, 0.14);
  animation: ais-pop 0.16s ease-out;
}

.ais-selection-ask svg {
  width: 13px;
  height: 13px;
}

.ais-selection-ask:hover {
  border-color: var(--accent-color, #0ea5e9);
}

/* ── 面板 ── */
.ais-panel {
  display: flex;
  flex-direction: column;
  background: var(--bg-secondary, #ffffff);
  color: var(--text-primary, #1e293b);
  overflow: hidden;
}

.ais-panel.is-floating {
  position: fixed;
  top: 12px;
  right: 12px;
  bottom: 12px;
  z-index: 1050;
  max-width: calc(100vw - 24px);
  border: 1px solid var(--border-color, #e2e8f0);
  border-radius: 16px;
  box-shadow: 0 24px 60px rgba(15, 23, 42, 0.18), 0 2px 8px rgba(15, 23, 42, 0.06);
}

.ais-panel.is-docked {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  border-left: 1px solid var(--border-color, #e2e8f0);
}

.ais-panel.is-resizing {
  user-select: none;
}

@media (max-width: 767px) {
  .ais-panel.is-floating {
    top: 0;
    right: 0;
    bottom: 0;
    max-width: 100vw;
    border-radius: 0;
  }
}

/* 分隔栏：左边缘 6px 热区，悬停/拖动时显示强调色细线 */
.ais-resizer {
  position: absolute;
  top: 0;
  bottom: 0;
  left: -3px;
  width: 6px;
  z-index: 5;
  cursor: col-resize;
}

.ais-resizer::after {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: 2px;
  width: 2px;
  background: transparent;
  transition: background 0.15s ease;
}

.ais-resizer:hover::after,
.ais-panel.is-resizing .ais-resizer::after {
  background: var(--accent-color, #0ea5e9);
}

.ais-panel.is-floating .ais-resizer {
  top: 16px;
  bottom: 16px;
}

.ais-main {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

/* ── 头部 ── */
.ais-header {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  /* 与主聊天区顶部信息行、工作区头部等高 */
  height: 44px;
  padding: 0 8px 0 10px;
  border-bottom: 1px solid var(--border-color, #e2e8f0);
  box-sizing: border-box;
}

.ais-title {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  height: 32px;
  padding: 0 8px 0 4px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--text-primary, #1e293b);
  cursor: pointer;
  text-align: left;
}

.ais-title:hover,
.ais-title.active {
  background: var(--bg-tertiary, #f1f5f9);
}

.ais-title-mark,
.ais-rail-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 24px;
  height: 24px;
  border-radius: 7px;
  background: linear-gradient(135deg, #0ea5e9, #06b6d4);
  color: #ffffff;
}

.ais-title-mark svg,
.ais-rail-mark svg {
  width: 14px;
  height: 14px;
}

.ais-title-mark.is-busy svg,
.ais-rail-mark.is-busy svg {
  animation: ais-spin 2.4s linear infinite;
}

.ais-title-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 14px;
  font-weight: 600;
}

.ais-title-arrow {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
  color: var(--text-secondary, #64748b);
  transition: transform 0.2s ease;
}

.ais-title-arrow.open {
  transform: rotate(180deg);
}

.ais-header-actions {
  display: flex;
  align-items: center;
  gap: 2px;
}

.ais-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  padding: 0;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--text-secondary, #64748b);
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}

.ais-icon-btn svg {
  width: 16px;
  height: 16px;
}

.ais-icon-btn:hover:not(:disabled) {
  background: var(--bg-tertiary, #f1f5f9);
  color: var(--text-primary, #1e293b);
}

.ais-icon-btn.active {
  color: var(--accent-color, #0ea5e9);
}

.ais-icon-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

/* ── 折叠窄条 ── */
.ais-rail {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 8px 0;
}

.ais-rail-mark {
  margin: 4px 0 6px;
}

.ais-rail-unread {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #f97316;
}

.ais-rail-spacer {
  flex: 1;
}

/* ── 主体 ── */
.ais-body {
  position: relative;
  flex: 1;
  min-height: 0;
  display: flex;
}

.ais-body ::v-deep .chat-container {
  height: 100%;
}

/* 历史会话：覆盖在对话区上方的下拉层 */
.ais-history {
  position: absolute;
  inset: 0;
  z-index: 60;
  display: flex;
  flex-direction: column;
  background: var(--bg-secondary, #ffffff);
}

.ais-history-search {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 10px 12px 6px;
  padding: 0 10px;
  height: 34px;
  border: 1px solid var(--border-color, #e2e8f0);
  border-radius: 10px;
  background: var(--bg-primary, #f8fafc);
  color: var(--text-secondary, #64748b);
}

.ais-history-search:focus-within {
  border-color: var(--accent-color, #0ea5e9);
}

.ais-history-search svg {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
}

.ais-history-search input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  color: var(--text-primary, #1e293b);
  font-size: 13px;
}

.ais-history-list {
  flex: 1;
  overflow-y: auto;
  padding: 0 8px 12px;
}

.ais-history-empty {
  padding: 32px 0;
  text-align: center;
  font-size: 13px;
  color: var(--text-secondary, #64748b);
}

.ais-history-group-title {
  padding: 10px 8px 4px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary, #64748b);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ais-history-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  height: 36px;
  padding: 0 8px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 13px;
}

.ais-history-item:hover {
  background: var(--bg-tertiary, #f1f5f9);
}

.ais-history-item.active {
  background: color-mix(in srgb, var(--accent-color, #0ea5e9) 12%, transparent);
  color: var(--accent-color, #0ea5e9);
}

.ais-history-live {
  flex-shrink: 0;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #22c55e;
  animation: ais-pulse 1.4s ease-in-out infinite;
}

.ais-history-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ais-history-rename {
  flex: 1;
  min-width: 0;
  height: 26px;
  padding: 0 6px;
  border: 1px solid var(--accent-color, #0ea5e9);
  border-radius: 6px;
  outline: none;
  background: var(--bg-secondary, #ffffff);
  color: var(--text-primary, #1e293b);
  font-size: 13px;
}

.ais-history-time {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--text-secondary, #64748b);
}

.ais-history-ops {
  display: none;
  flex-shrink: 0;
  align-items: center;
  gap: 2px;
}

.ais-history-item:hover .ais-history-time {
  display: none;
}

.ais-history-item:hover .ais-history-ops {
  display: inline-flex;
}

.ais-history-ops button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-secondary, #64748b);
  cursor: pointer;
}

.ais-history-ops button svg {
  width: 13px;
  height: 13px;
}

.ais-history-ops button:hover {
  background: var(--bg-secondary, #ffffff);
  color: var(--text-primary, #1e293b);
}

.ais-history-ops button.on {
  color: var(--accent-color, #0ea5e9);
}

.ais-history-ops button.danger:hover,
.ais-history-ops button.confirming {
  background: #fee2e2;
  color: #dc2626;
}

/* ── 空态 ── */
.ais-welcome {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 100%;
  padding: 8px 4px;
  text-align: center;
}

.ais-welcome-mark {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  margin-bottom: 12px;
  border-radius: 14px;
  background: linear-gradient(135deg, #e0f2fe, #f0f9ff);
  color: #0ea5e9;
}

.ais-welcome-mark svg {
  width: 24px;
  height: 24px;
}

.ais-welcome h3 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary, #1e293b);
}

.ais-welcome-sub {
  margin: 6px 0 18px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-secondary, #64748b);
}

.ais-welcome-sub strong {
  color: var(--text-primary, #1e293b);
  font-weight: 600;
}

.ais-welcome-sub kbd {
  display: inline-block;
  min-width: 18px;
  padding: 0 5px;
  border: 1px solid var(--border-color, #e2e8f0);
  border-bottom-width: 2px;
  border-radius: 5px;
  background: var(--bg-primary, #f8fafc);
  font-family: inherit;
  font-size: 12px;
  line-height: 18px;
}

.ais-suggestions {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}

.ais-suggestion {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--border-color, #e2e8f0);
  border-radius: 10px;
  background: var(--bg-primary, #f8fafc);
  color: var(--text-primary, #475569);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.15s ease, color 0.15s ease, transform 0.15s ease;
}

.ais-suggestion svg {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
  opacity: 0;
  transition: opacity 0.15s ease, transform 0.15s ease;
}

.ais-suggestion:hover {
  border-color: #bae6fd;
  color: #0284c7;
}

.ais-suggestion:hover svg {
  opacity: 1;
  transform: translateX(2px);
}

/* ── 输入框上方：上下文条 / 错误提示 ── */
.ais-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin: 0 12px 6px;
  padding: 6px 10px;
  border-radius: 8px;
  background: #fee2e2;
  color: #dc2626;
  font-size: 12px;
}

.ais-error button {
  border: none;
  background: transparent;
  color: inherit;
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
}

.ais-context-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 0 12px;
}

.ais-context-bar:empty {
  display: none;
}

.ais-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 100%;
  height: 24px;
  padding: 0 4px 0 8px;
  border: 1px solid color-mix(in srgb, var(--accent-color, #0ea5e9) 30%, var(--border-color, #e2e8f0));
  border-radius: 999px;
  background: color-mix(in srgb, var(--accent-color, #0ea5e9) 8%, transparent);
  color: var(--text-primary, #1e293b);
  font-size: 12px;
  cursor: default;
}

.ais-chip > svg {
  width: 12px;
  height: 12px;
  flex-shrink: 0;
  color: var(--accent-color, #0ea5e9);
}

.ais-chip-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 200px;
}

.ais-chip-note {
  color: var(--text-secondary, #64748b);
}

.ais-chip.is-sent {
  border-color: var(--border-color, #e2e8f0);
  background: transparent;
}

.ais-chip.is-quote {
  max-width: 100%;
}

.ais-chip.is-quote .ais-chip-text {
  max-width: 260px;
  color: var(--text-secondary, #64748b);
}

.ais-chip.is-ghost,
.ais-chip.is-action {
  padding: 0 10px;
  border-style: dashed;
  background: transparent;
  color: var(--text-secondary, #64748b);
  cursor: pointer;
}

.ais-chip.is-action {
  border-style: solid;
}

.ais-chip.is-ghost:hover,
.ais-chip.is-action:hover {
  border-color: var(--accent-color, #0ea5e9);
  color: var(--accent-color, #0ea5e9);
}

.ais-chip-x {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--text-secondary, #64748b);
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
}

.ais-chip-x:hover {
  background: var(--bg-tertiary, #f1f5f9);
  color: var(--text-primary, #1e293b);
}

/* 窄栏下压缩输入框里的模型名，给发送按钮留出空间 */
.ais-body ::v-deep .model-btn-label {
  max-width: 96px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 拖拽遮罩 */
.ais-drag-mask {
  position: fixed;
  inset: 0;
  z-index: 1060;
  cursor: grabbing;
}

.ais-drag-mask.is-col {
  cursor: col-resize;
}

/* ── 动画 ── */
.ais-slide-enter-active,
.ais-slide-leave-active {
  transition: transform 0.24s cubic-bezier(0.22, 1, 0.36, 1), opacity 0.2s ease;
}

.ais-slide-enter,
.ais-slide-leave-to {
  transform: translateX(24px);
  opacity: 0;
}

.ais-fade-enter-active,
.ais-fade-leave-active {
  transition: opacity 0.15s ease, transform 0.15s ease;
}

.ais-fade-enter,
.ais-fade-leave-to {
  opacity: 0;
  transform: translateY(-6px);
}

@keyframes ais-spin {
  to { transform: rotate(360deg); }
}

@keyframes ais-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}

@keyframes ais-pop {
  from { opacity: 0; transform: translateY(4px) scale(0.96); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}

@media (prefers-reduced-motion: reduce) {
  .ais-root,
  .ais-launcher,
  .ais-slide-enter-active,
  .ais-slide-leave-active {
    transition: none;
  }
  .ais-launcher.is-busy .ais-launcher-icon,
  .ais-title-mark.is-busy svg,
  .ais-rail-mark.is-busy svg {
    animation: none;
  }
}
</style>
