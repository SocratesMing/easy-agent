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
          <button type="button" class="ais-icon-btn" title="新建会话" @click="expand(); startNewChat(false)">
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
              <button type="button" class="ais-icon-btn" title="新建会话" @click="startNewChat()">
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
              :activeExpert="selectedExpert"
              @selectExpert="handleUseExpert"
              @clearExpert="selectedExpert = null"
              @sendMessage="handleSendMessage"
              @createSession="ensureCurrentSession"
              @removeFile="handleRemoveFile"
              @stop="handleStop"
              @retry="handleRetry"
              @approve="handleToolApproval('approve', $event)"
              @reject="handleToolApproval('reject', $event)"
              @edit="handleToolApproval('edit', $event)"
              @openPanel="openManagePanel"
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
                    class="ais-chip is-clickable"
                    :class="{ 'is-sent': contextAlreadySent }"
                    :title="contextPreview"
                    @click="attachPageContext"
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"></path><circle cx="12" cy="10" r="3"></circle></svg>
                    <span class="ais-chip-text">{{ pageContext.title }}</span>
                    <span v-if="contextAlreadySent" class="ais-chip-note">已提供</span>
                    <button type="button" class="ais-chip-x" title="不附带页面上下文" @click.stop="includeContext = false">×</button>
                  </span>
                  <button
                    v-else-if="pageContext"
                    type="button"
                    class="ais-chip is-ghost"
                    title="把当前页面信息提供给 AI"
                    @click="attachPageContext"
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
import chatSendMixin from '../mixins/chatSend.js'
import aiSidebarLayoutMixin, {
  DEFAULT_FEATURES, DEFAULT_WIDTH, MAX_PAGE_SESSIONS, MIN_WIDTH, NARROW_VIEWPORT,
  PAGE_SESSIONS_KEY, PREFS_KEY, RAIL_WIDTH, readJson, writeJson,
} from '../mixins/aiSidebarLayout.js'
import { chatStore } from '../store/chatStore.js'
import { ensureSkillDirectoryLoaded, skillFreeTitle } from '../../skills/store/skillDirectoryStore.js'
import { formatPageContext, setExternalPageContext } from '../../../utils/pageContext.js'
import { formatRelativeTime } from '../../../utils/relativeTime.js'

export default {
  name: 'AiSidebar',
  components: { Chat },
  mixins: [chatSessionMixin, chatSendMixin, aiSidebarLayoutMixin],
  emits: ['open-change', 'refresh-context', 'layout-change', 'open-in-main', 'open-panel'],
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
      // 「+」菜单召唤的专家：chatSessionMixin 发送时据此改走专家执行接口
      selectedExpert: null,
      showHistory: false,
      historyQuery: '',
      renamingId: null,
      renameText: '',
      confirmDeleteId: null,
      includeContext: true,
      openSource: 'api',
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
        this.sentContexts[sid] = this.sentContexts.__new__
        delete this.sentContexts.__new__
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
      this.$emit('open-change', v, v ? this.openSource : undefined)
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
  beforeUnmount() {
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
    beforeSessionChange(kind) {
      this.showHistory = false
      if (kind === 'create') this.selectedExpert = null
    },
    handleUseExpert(expert) {
      if (!expert || !expert.export_agent_id) return
      this.selectedExpert = {
        export_agent_id: expert.export_agent_id,
        export_name_cn: expert.export_name_cn,
        export_version: expert.export_version,
      }
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
      this.sentContexts[key] = text
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
    /** opts.source：唤起来源 launcher / shortcut / selection / api，随 open-change 交给宿主 */
    openSidebar(opts = {}) {
      const wasOpen = this.open
      this.openSource = opts.source || 'api'
      // 宿主页面可能已变（SPA 的 pushState 不触发 popstate、同页数据也会更新）：每次唤起都请宿主重采
      this.$emit('refresh-context')
      this.open = true
      this.collapsed = false
      this.unread = false
      this.hideSelectionAsk()
      if (opts.context) setExternalPageContext(opts.context)
      if (opts.newSession) {
        this.startNewChat(false)
      } else if (!wasOpen) {
        // 等重采的 pageContext 经 props 下发后再按页面匹配会话
        this.$nextTick(() => this.preferPageSession())
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
    toggle(opts) {
      if (this.open) this.close()
      else this.openSidebar(opts)
    },
    collapse() {
      this.collapsed = true
      this.showHistory = false
    },
    expand() {
      this.$emit('refresh-context')
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
    startNewChat(refreshContext = true) {
      if (refreshContext) this.$emit('refresh-context')
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
      // 等重采的 pageContext 经 props 下发，这条消息才带上最新页面内容
      await this.$nextTick()
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
    // 「+」菜单二级面板的「在管理面板中打开」：tab = experts / skills / connectors
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
    // 点上下文标签：重新采集当前页面并附带（页面内容变了也能拿到最新的）
    attachPageContext() {
      this.includeContext = true
      this.$emit('refresh-context')
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
      this.pageSessions[key] = list.slice(0, MAX_PAGE_SESSIONS)
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
  },
}
</script>

<style scoped src="./AiSidebar.css"></style>
