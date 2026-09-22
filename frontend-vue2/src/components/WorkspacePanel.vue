<template>
  <div
    ref="rootEl"
    class="workspace-panel"
    :class="{ collapsed: !visible, 'is-resizing': isResizing, 'is-previewing': showPreview, 'is-fullscreen': isFullscreen, 'is-animating': isPanelAnimating }"
    :style="panelStyle"
  >
    <div
      v-if="visible"
      class="wp-resizer"
      title="拖动调整宽度"
      @mousedown="startResize"
    ></div>
    <template v-if="renderContent">
      <div class="wp-header">
        <div class="wp-title">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" class="wp-header-icon">
            <path d="M3 7V17C3 18.1046 3.89543 19 5 19H19C20.1046 19 21 18.1046 21 17V9C21 7.89543 20.1046 7 19 7H13L11 5H5C3.89543 5 3 5.89543 3 7Z" fill="#eab308" stroke="#ca8a04" stroke-width="1.5"></path>
            <path d="M3 10H21" stroke="#ca8a04" stroke-width="1.5"></path>
          </svg>
          <span>工作区</span>
        </div>
        <!-- 两个按钮成组靠右：header 用 space-between，若各自作为直接子元素，
             刷新会被挤到「标题」与「收起」之间的正中位置 -->
        <div class="wp-actions">
          <button class="wp-icon-btn" @click="refresh" title="刷新工作区" :disabled="isLoading">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" :class="{ spinning: isLoading }">
              <polyline points="23 4 23 10 17 10"></polyline>
              <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path>
            </svg>
          </button>
          <button
            class="wp-icon-btn"
            @click="toggleFullscreen"
            :title="isFullscreen ? '退出全屏' : '工作区全屏'"
          >
            <svg
              v-if="!isFullscreen"
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <path d="M8 3H5a2 2 0 0 0-2 2v3"></path>
              <path d="M21 8V5a2 2 0 0 0-2-2h-3"></path>
              <path d="M3 16v3a2 2 0 0 0 2 2h3"></path>
              <path d="M16 21h3a2 2 0 0 0 2-2v-3"></path>
            </svg>
            <svg
              v-else
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <path d="M8 3v3a2 2 0 0 1-2 2H3"></path>
              <path d="M21 8h-3a2 2 0 0 1-2-2V3"></path>
              <path d="M3 16h3a2 2 0 0 1 2 2v3"></path>
              <path d="M16 21v-3a2 2 0 0 1 2-2h3"></path>
            </svg>
          </button>
          <button class="wp-icon-btn" @click="handleCollapse" title="收起工作区">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
              <line x1="9" y1="3" x2="9" y2="21"></line>
            </svg>
          </button>
        </div>
      </div>

      <!-- 标签栏：文件树固定为第一个 tab，打开过的文件各占一个 tab -->
      <div class="wp-tabs">
        <button
          class="wp-tab wp-tab-tree"
          :class="{ active: !showPreview }"
          @click="activeTabId = TREE_TAB"
          title="文件列表"
        >
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" class="wp-tab-tree-icon">
            <path d="M3 7V17C3 18.1046 3.89543 19 5 19H19C20.1046 19 21 18.1046 21 17V9C21 7.89543 20.1046 7 19 7H13L11 5H5C3.89543 5 3 5.89543 3 7Z" fill="#eab308" stroke="#ca8a04" stroke-width="1.5"></path>
          </svg>
          <span class="wp-tab-name">文件</span>
        </button>
        <div
          v-for="tab in openTabs"
          :key="tab.id"
          class="wp-tab"
          :class="{ active: tab.id === activeTabId }"
          @click="activeTabId = tab.id"
          :title="tab.name"
        >
          <span class="wp-tab-name">{{ tab.name }}</span>
          <button class="wp-tab-action" @click.stop="downloadTab(tab)" title="下载该文件">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
              <polyline points="7 10 12 15 17 10"></polyline>
              <line x1="12" y1="15" x2="12" y2="3"></line>
            </svg>
          </button>
          <button class="wp-tab-close" @click.stop="closeTab(tab.id)" title="关闭">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
              <line x1="18" y1="6" x2="6" y2="18"></line>
              <line x1="6" y1="6" x2="18" y2="18"></line>
            </svg>
          </button>
        </div>
      </div>

      <div class="wp-content" v-show="!showPreview || isFullscreen">
        <div v-if="isLoading" class="wp-center">
          <div class="wp-spinner"></div>
          <span class="wp-center-text">加载中...</span>
        </div>

        <div v-else-if="error" class="wp-center">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" class="wp-error-icon">
            <circle cx="12" cy="12" r="10"></circle>
            <line x1="12" y1="8" x2="12" y2="12"></line>
            <line x1="12" y1="16" x2="12.01" y2="16"></line>
          </svg>
          <span class="wp-center-text wp-error-text">{{ error }}</span>
          <button class="wp-retry-btn" @click="refresh">重试</button>
        </div>

        <div v-else-if="workspaceTreeData.length === 0" class="wp-center">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" class="wp-empty-icon">
            <path d="M3 7V17C3 18.1046 3.89543 19 5 19H19C20.1046 19 21 18.1046 21 17V9C21 7.89543 20.1046 7 19 7H13L11 5H5C3.89543 5 3 5.89543 3 7Z" fill="#fbbf24" stroke="#f59e0b" stroke-width="1.5"></path>
          </svg>
          <span class="wp-center-text">工作区为空</span>
          <span class="wp-empty-hint">Agent 生成或修改的文件会显示在这里</span>
        </div>

        <div v-else class="wp-file-tree">
          <FileTreeNode
            v-for="item in workspaceTreeData"
            :key="item.id"
            :item="item"
            :selectedId="selectedFile?.id"
            :depth="0"
            :sessionId="currentSessionId"
            :taskId="taskId"
            @select="handleSelectFile"
          @download="handleDownloadFile"
          />
        </div>
      </div>
    </template>

    <!-- 预览挂载层：定位在「头部 + 标签栏」之下，
         FilePreview 自身是 absolute inset:0，相对本层定位才不会盖住标签栏。
         用 v-show 而不是 v-if：组件需保持挂载，visible 才能发生 false→true 的变化
         来触发加载；v-show 的 display:none 也不会在收起时拦截文件树的点击。 -->
    <div v-show="showPreview || isFullscreen" class="wp-preview-host">
      <div v-if="isFullscreen && !showPreview" class="wp-main-placeholder">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none">
          <path d="M3 7V17C3 18.1046 3.89543 19 5 19H19C20.1046 19 21 18.1046 21 17V9C21 7.89543 20.1046 7 19 7H13L11 5H5C3.89543 5 3 5.89543 3 7Z" fill="#fbbf24" stroke="#f59e0b" stroke-width="1.5"></path>
        </svg>
        <span>从左侧选择文件预览</span>
      </div>
      <FilePreview
        :filename="activeTab?.name || ''"
        :filePath="activeTab?.file_path || ''"
        :sessionId="currentSessionId"
        :taskId="taskId"
        :visible="showPreview"
        inline
        @close="closeTab(activeTabId)"
      />
    </div>
  </div>
</template>

<script>
import FileTreeNode from './FileTreeNode.vue'
import FilePreview from './FilePreview.vue'
import { getWorkspaceTree } from '../api/files'
import { getScheduledTaskWorkspace } from '../api/scheduledTasks'
import { getStoredToken } from '../api/auth.js'

// 文件树固定在第一个 tab；宽度/动画常量集中在此
const TREE_TAB = '__tree__'
const WIDTH_KEY = 'workspace_panel_width'
const WIDTH_MIN = 220
const WIDTH_MAX = 640
const WIDTH_DEFAULT = 280
const ANIM_MS = 250

export default {
  components: { FilePreview, FileTreeNode },
  props: {
    username: { type: String, default: '' },
    currentSessionId: { type: String, default: null },
    // 定时任务工作目录模式：传入 taskId 时改为读取该任务的工作目录，
    // 其余（文件树 / 标签页 / 预览 / 下载 / 全屏）与会话工作区完全共用。
    taskId: { type: String, default: '' },
    isStreaming: { type: Boolean, default: false },
    visible: { type: Boolean, default: true },
  },
  data() {
    return {
      workspaceTreeData: [],
      selectedFile: null,
      isLoading: false,
      error: null,

      // ── 预览标签页 ──────────────────────────────────────────────
      // 文件树固定在第一个 tab（TREE_TAB），打开过的文件各占一个 tab，
      // 可切换、可单独关闭。参考编辑器的多标签交互。
      TREE_TAB,
      openTabs: [],
      activeTabId: TREE_TAB,

      // ── 面板宽度（可拖拽，持久化到 localStorage）────────────────
      panelWidth: Number(localStorage.getItem(WIDTH_KEY)) || WIDTH_DEFAULT,
      isResizing: false,

      // ── 全屏（页面内铺满视口）───────────────────────────────────
      isFullscreen: false,

      // 折叠时延迟卸载内容：让内容跟着宽度一起被 overflow 裁进去，
      // 否则会变成「内容先消失、再收起一条空白面板」，观感很生硬
      renderContent: this.visible,
      // 折叠/展开的宽度动画期间为 true：此时暂停预览内容的布局与绘制（见 CSS）。
      // 面板宽度逐帧变化会让 iframe / office 预览反复重排，是卡顿的主要来源。
      isPanelAnimating: false,
      unloadTimer: null,
      animTimer: null,
    }
  },
  computed: {
    // 数据源标识：定时任务工作目录（taskId）优先，否则会话工作区（currentSessionId）。
    // 两种来源共用同一套「文件树 + 标签页 + 预览」，只有取数与下载地址不同。
    activeSourceKey() {
      return this.taskId || this.currentSessionId || ''
    },
    activeTab() {
      return this.openTabs.find((t) => t.id === this.activeTabId) || null
    },
    // 有激活的文件 tab 即处于预览态（沿用原 showPreview 的语义）
    showPreview() {
      return this.activeTab !== null
    },
    panelStyle() {
      if (this.isFullscreen) {
        return {
          position: 'fixed',
          top: '0',
          right: '0',
          bottom: '0',
          left: '0',
          width: '100%',
          zIndex: '150',
          // 宽度瞬变交给 FLIP 位移动画呈现，自身不再补间
          transition: 'none',
        }
      }
      return { width: this.visible ? `${this.panelWidth}px` : '0px' }
    },
  },
  watch: {
    visible: [
      // 折叠/展开的宽度动画与延迟卸载
      function (v) {
        this.isPanelAnimating = true
        clearTimeout(this.animTimer)
        this.animTimer = setTimeout(() => {
          this.isPanelAnimating = false
        }, ANIM_MS)

        clearTimeout(this.unloadTimer)
        if (v) {
          this.renderContent = true
        } else {
          this.unloadTimer = setTimeout(() => {
            this.renderContent = false
          }, ANIM_MS)
        }
      },
      function (newVal) {
        if (newVal) {
          this.refresh()
        }
      },
    ],
    isStreaming(newVal, oldVal) {
      if (oldVal === true && newVal === false) {
        setTimeout(() => this.refresh(), 300)
      }
    },
    activeSourceKey() {
      this.resetWorkspaceView()
      this.refresh()
    },
  },
  methods: {
    // ── 全屏（页面内铺满视口）───────────────────────────────────
    // 用 position: fixed 覆盖浏览器视口 —— 不是 Fullscreen API（那会把地址栏/
    // 标签栏也收起，用户不要整屏全屏）。视口全屏的 Esc 退出需要自己监听。
    // FLIP 过渡：切换前记录位置，切换后（fixed 全屏生效）先反向位移 + 淡入，
    // 再动画回位 —— 面板看起来是从原位置「滑入」全屏，而不是瞬移。
    // 只用 translate 不用 scale，内容不会被拉伸变形。
    playFullscreenFlip(el, first) {
      if (!el || !first) return
      const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
      if (reduced) return
      this.$nextTick(() => {
        requestAnimationFrame(() => {
          const last = el.getBoundingClientRect()
          const dx = first.left - last.left
          const dy = first.top - last.top
          if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return
          // FLIP 期间禁用 width transition，避免宽度过渡与位移动画叠加
          el.style.transition = 'none'
          const anim = el.animate(
            [
              { transform: `translate(${dx}px, ${dy}px)`, opacity: 0.55 },
              { transform: 'translate(0, 0)', opacity: 1 },
            ],
            { duration: 280, easing: 'cubic-bezier(0.2, 0.7, 0.3, 1)' }
          )
          const done = anim.finished
            ? anim.finished
            : new Promise((resolve) => { anim.onfinish = resolve })
          done.then(() => { el.style.transition = '' }).catch(() => { el.style.transition = '' })
        })
      })
    },
    toggleFullscreen() {
      const el = this.$refs.rootEl
      const first = el ? el.getBoundingClientRect() : null
      // 同步先禁掉 width transition：状态切换会让宽度瞬变，交给 FLIP 位移动画呈现，
      // 动画结束后由 playFullscreenFlip 恢复默认过渡
      if (el) el.style.transition = 'none'
      this.isFullscreen = !this.isFullscreen
      this.playFullscreenFlip(el, first)
    },
    handleCollapse() {
      // 先退出全屏再收起：否则面板会带着全屏宽度进入折叠动画
      this.isFullscreen = false
      this.$emit('toggle')
    },
    onKeydown(e) {
      if (e.key === 'Escape' && this.isFullscreen) {
        this.isFullscreen = false
      }
    },
    startResize(e) {
      this.isResizing = true
      e.preventDefault()
      document.addEventListener('mousemove', this.onResize)
      document.addEventListener('mouseup', this.stopResize)
      document.body.style.cursor = 'col-resize'
      document.body.style.userSelect = 'none'
    },
    onResize(e) {
      if (!this.isResizing) return
      // 面板贴右侧，宽度 = 视口右边缘到鼠标的距离
      const next = window.innerWidth - e.clientX
      this.panelWidth = Math.min(WIDTH_MAX, Math.max(WIDTH_MIN, next))
    },
    stopResize() {
      if (!this.isResizing) return
      this.isResizing = false
      document.removeEventListener('mousemove', this.onResize)
      document.removeEventListener('mouseup', this.stopResize)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      localStorage.setItem(WIDTH_KEY, String(Math.round(this.panelWidth)))
    },
    async buildWorkspaceTree() {
      const taskId = this.taskId
      const sessionId = this.currentSessionId
      const sourceKey = taskId || sessionId
      if (!sourceKey) {
        this.workspaceTreeData = []
        return
      }
      this.isLoading = true
      this.error = null
      try {
        const response = taskId
          ? await getScheduledTaskWorkspace(taskId, '')
          : await getWorkspaceTree('', sessionId)
        // 切换来源期间到达的旧响应丢弃，避免工作区内容串会话 / 串任务
        if (this.activeSourceKey !== sourceKey) return
        this.workspaceTreeData = (response.items || []).map((item) => ({
          id: item.path,
          name: item.name,
          type: item.type,
          size: item.size,
          file_type:
            item.type === 'file'
              ? item.name.split('.').pop().toLowerCase()
              : '',
          file_path: item.path,
        }))
      } catch (e) {
        if (this.activeSourceKey !== sourceKey) return
        this.error = '加载工作区失败: ' + e.message
        this.workspaceTreeData = []
      } finally {
        if (this.activeSourceKey === sourceKey) this.isLoading = false
      }
    },
    tabIdOf(file) {
      return String(file.id || file.file_path || file.path || file.name || '')
    },
    handleSelectFile(file) {
      this.selectedFile = file
      const id = this.tabIdOf(file)
      if (!id) return
      // 已经打开过就只切换，不重复建 tab
      if (!this.openTabs.some((t) => t.id === id)) {
        this.openTabs.push({
          id,
          name: file.name || '',
          file_path: file.file_path || file.path || '',
        })
      }
      this.activeTabId = id
    },
    downloadTab(tab) {
      this.handleDownloadFile({ name: tab.name, file_path: tab.file_path })
    },
    closeTab(id) {
      const idx = this.openTabs.findIndex((t) => t.id === id)
      if (idx === -1) return
      this.openTabs.splice(idx, 1)
      if (this.activeTabId === id) {
        // 关闭当前 tab：优先接管右侧邻居，其次左侧，都没有则回到文件树
        const next = this.openTabs[Math.min(idx, this.openTabs.length - 1)]
        this.activeTabId = next ? next.id : this.TREE_TAB
      }
    },
    handleDownloadFile(file) {
      const filePath = file.file_path || file.path
      const token = getStoredToken()
      const params = new URLSearchParams()
      params.set('file_path', filePath)
      params.set('download', 'true')
      if (token) params.set('token', token)
      // 相对路径：开发由 vue.config.js 的 proxy 转发，生产与后端同源
      let url
      if (this.taskId) {
        url = `/agent/scheduled-tasks/${this.taskId}/workspace/file?${params.toString()}`
      } else {
        params.set('session_id', this.currentSessionId)
        url = `/agent/files/preview?${params.toString()}`
      }
      const link = document.createElement('a')
      link.href = url
      link.download = file.name
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
    },
    refresh() {
      this.buildWorkspaceTree()
    },
    // 数据源切换（会话或定时任务）时工作区视图必须整体重来：清空已打开的文件标签、
    // 选中态与预览，否则旧来源的预览/标签会残留（其 file_path 在新来源中无效）。
    resetWorkspaceView() {
      this.openTabs = []
      this.activeTabId = this.TREE_TAB
      this.selectedFile = null
    },
  },
  mounted() {
    document.addEventListener('keydown', this.onKeydown)
    this.buildWorkspaceTree()
  },
  beforeDestroy() {
    document.removeEventListener('keydown', this.onKeydown)
    this.stopResize()
    clearTimeout(this.unloadTimer)
    clearTimeout(this.animTimer)
  },
}
</script>

<style scoped>
/* 宽度由 JS 内联 style 控制（可拖拽 + 记忆），这里的 width 仅作兜底 */
.workspace-panel {
  position: relative;
  width: 280px;
  display: flex;
  flex-direction: column;
  background: #ffffff;
  border-left: 1px solid var(--border-color, #e2e8f0);
  height: 100%;
  overflow: hidden;
  /* 展开/折叠动画：宽度由 JS 内联 style 设置，这里负责补间 */
  transition: width 0.25s ease;
  box-shadow: 4px 0 16px rgba(0, 0, 0, 0.06);
}

/* 页面内全屏：定位与尺寸由 panelStyle 的内联样式提供
   （position:fixed; inset:0; width:100% —— 内联优先级最高，最可靠），
   这里只负责收细节 —— 全屏时宽度不可拖拽，隐藏把手；去左边框与阴影。
   z-index 需高于聊天区内浮层（如 .scroll-btn 的 10）；.workspace-area 自身
   有 z-index:1 会形成层叠上下文，App.vue 里用 :has() 在全屏时把它一并抬高。 */
.workspace-panel.is-fullscreen {
  border-left: none;
  box-shadow: none;
  /* 全屏双栏：左侧固定文件树、右侧预览。用 grid 而不是重排 DOM，
     预览层（.wp-preview-host）才能保持常驻挂载、不因收起/切换而重载。 */
  display: grid;
  grid-template-columns: 280px 1fr;
  grid-template-rows: 44px 34px 1fr;
  grid-template-areas:
    'header header'
    'tabs   tabs'
    'tree   preview';
}

.workspace-panel.is-fullscreen .wp-header { grid-area: header; }

.workspace-panel.is-fullscreen .wp-tabs { grid-area: tabs; }

.workspace-panel.is-fullscreen .wp-content {
  grid-area: tree;
  min-height: 0;
  overflow-y: auto;
  border-right: 1px solid var(--border-color, #e2e8f0);
}

.workspace-panel.is-fullscreen .wp-preview-host {
  grid-area: preview;
  /* 脱离普通模式的 absolute 覆盖，回到网格右侧那一格 */
  position: relative;
  top: auto;
  right: auto;
  bottom: auto;
  left: auto;
  min-width: 0;
}

.workspace-panel.is-fullscreen .wp-resizer {
  display: none;
}

/* 全屏且未打开文件时右侧的占位提示 */
.wp-main-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: var(--text-secondary, #64748b);
  font-size: 13px;
}

.wp-main-placeholder svg {
  width: 48px;
  height: 48px;
  opacity: 0.45;
}

/* 预览文件时保证足够阅读宽度：宽度由内联 style 控制（可拖拽 220~640），
   min-width 优先级高于内联 width，所以拖得很窄时也能自动撑开，
   不必改动拖拽逻辑与 localStorage 记录。 */
.workspace-panel.is-previewing {
  min-width: 560px;
}

.workspace-panel.collapsed {
  width: 0;
  /* 预览态的 min-width:560px 会压过内联 width:0，导致收起无效，这里必须重置 */
  min-width: 0;
  border-left: none;
  overflow: hidden;
}

.wp-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  /* 与 .chat-topbar 严格等高（44px，含 1px 底边框）：工作区展开时，
     两个区域顶部的横线必须落在同一条水平线上。用固定高度而不是靠 padding
     推导，避免两侧内容尺寸变化时再次错位。 */
  height: 44px;
  padding: 0 12px 0 14px;
  border-bottom: 1px solid #e2e8f0;
  flex-shrink: 0;
  box-sizing: border-box;
}

.wp-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.wp-title span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 右侧按钮组：与文件树的 6px 圆角保持同一套视觉语言 */
.wp-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.wp-header-icon {
  width: 20px;
  height: 20px;
  /* 彩色文件夹图标与文件树保持同一套视觉语言，不再跟随文字色 */
  flex-shrink: 0;
}

/* 头部图标按钮：无边框 + hover 圆角底色（面板头部常见的轻量图标按钮）。
   刷新与收起共用同一套尺寸与交互，两个图标并排才配套。 */
.wp-icon-btn {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: none;
  border: none;
  padding: 0;
  border-radius: 6px;
  color: var(--text-secondary);
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}

.wp-icon-btn:hover:not(:disabled) {
  background: var(--bg-tertiary);
  color: var(--text-primary);
}

.wp-icon-btn:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.wp-icon-btn svg {
  width: 17px;
  height: 17px;
}

.wp-icon-btn svg.spinning {
  animation: spin 0.8s linear infinite;
}

/* 预览标签栏：文件树 + 已打开文件。高度 34px 与 .wp-header 的 44px 一起，
   构成 .wp-preview-host 的定位基准（见下方 top: 78px）。 */
.wp-tabs {
  display: flex;
  align-items: stretch;
  flex-shrink: 0;
  height: 34px;
  padding: 0 6px;
  background: #f8fafc;
  border-bottom: 1px solid var(--border-color, #e2e8f0);
  overflow-x: auto;
  overflow-y: hidden;
  scrollbar-width: none;
}

.wp-tabs::-webkit-scrollbar {
  display: none;
}

.wp-tab {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
  max-width: 150px;
  padding: 0 8px;
  font-size: 12px;
  color: var(--text-secondary, #64748b);
  border-bottom: 2px solid transparent;
  cursor: pointer;
  user-select: none;
  transition: background 0.15s ease, color 0.15s ease;
}

.wp-tab:hover {
  background: #eef2f7;
}

.wp-tab.active {
  color: var(--text-primary, #1f2937);
  background: #ffffff;
  border-bottom-color: #0ea5e9;
}

.wp-tab-tree-icon {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
}

.wp-tab-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 标签上的操作按钮（下载 / 关闭）：hover 或激活时才显现，避免标签栏过于嘈杂 */
.wp-tab-action,
.wp-tab-close {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 16px;
  height: 16px;
  padding: 0;
  border: none;
  border-radius: 4px;
  background: none;
  color: inherit;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s ease, background 0.15s ease;
}

.wp-tab:hover .wp-tab-action,
.wp-tab.active .wp-tab-action,
.wp-tab:hover .wp-tab-close,
.wp-tab.active .wp-tab-close {
  opacity: 0.55;
}

.wp-tab-action:hover,
.wp-tab-close:hover {
  opacity: 1;
  background: rgba(15, 23, 42, 0.08);
}

.wp-tab-action svg,
.wp-tab-close svg {
  width: 11px;
  height: 11px;
}

/* 标签栏在浅色下是「页面底色」，深色下沿用同一逻辑（面板 #1a1a1a 上压更深的 #000），
   否则会保留一块刺眼的浅色条 */
html[data-theme="dark"] .wp-tabs {
  background: var(--bg-primary);
}

html[data-theme="dark"] .wp-tab:hover {
  background: var(--bg-tertiary);
}

html[data-theme="dark"] .wp-tab.active {
  background: var(--bg-secondary);
  color: var(--text-primary);
  border-bottom-color: var(--accent-color, #0ea5e9);
}

html[data-theme="dark"] .wp-tab-action:hover,
html[data-theme="dark"] .wp-tab-close:hover {
  background: rgba(255, 255, 255, 0.14);
}

/* 预览挂载层：从「头部 44px + 标签栏 34px」下方开始 */
.wp-preview-host {
  position: absolute;
  top: 78px;
  right: 0;
  bottom: 0;
  left: 0;
  z-index: 50;
}

/* 折叠/展开的宽度动画期间暂停预览的布局与绘制：否则 iframe / office 内容
   会随面板宽度逐帧重排，是「有预览时收起/展开卡顿」的根因。
   content-visibility 只跳过渲染，组件仍常驻挂载、不会触发重新加载。 */
.workspace-panel.is-animating .wp-preview-host {
  content-visibility: hidden;
}

.wp-content {
  flex: 1;
  overflow-y: auto;
  padding: 6px 8px;
}

.wp-file-tree {
  width: 100%;
}

.wp-center {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 24px;
  gap: 12px;
  height: 100%;
}

.wp-spinner {
  width: 24px;
  height: 24px;
  border: 3px solid #e2e8f0;
  border-top-color: #166534;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.wp-center-text {
  font-size: 13px;
  color: var(--text-secondary);
  text-align: center;
}

.wp-error-icon {
  width: 32px;
  height: 32px;
  color: #ef4444;
}

.wp-error-text {
  color: #ef4444;
}

.wp-retry-btn {
  padding: 6px 16px;
  font-size: 12px;
  color: #166534;
  background: #f0fdf4;
  border: 1px solid #86efac;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.2s;
}

.wp-retry-btn:hover {
  background: #dcfce7;
}

.wp-empty-icon {
  width: 56px;
  height: 56px;
  /* 彩色图标用透明度淡化，空态不喧宾夺主 */
  opacity: 0.45;
}

/* 拖拽把手：贴在面板左边缘，hover 时高亮提示可拖动 */
.wp-resizer {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 5px;
  cursor: col-resize;
  z-index: 2;
  background: transparent;
  transition: background 0.15s ease;
}

.wp-resizer:hover,
.workspace-panel.is-resizing .wp-resizer {
  background: #0ea5e9;
}

/* 拖拽中：禁止整页选中文字；同时关掉宽度过渡，否则拖动会有明显的延迟跟随感 */
.workspace-panel.is-resizing {
  user-select: none;
  transition: none;
}

.wp-empty-hint {
  max-width: 200px;
  font-size: 12px;
  line-height: 1.5;
  text-align: center;
  color: var(--text-secondary);
  opacity: 0.75;
}
/* 展开时内容淡入，避免宽度到位后内容「啪」地一下出现 */
@keyframes wp-fade-in {
  from { opacity: 0; }
  to { opacity: 1; }
}

.wp-header,
.wp-content {
  animation: wp-fade-in 0.22s ease both;
}

/* 尊重系统的「减少动态效果」设置 */
@media (prefers-reduced-motion: reduce) {
  .workspace-panel {
    transition: none;
  }

  .wp-header,
  .wp-content {
    animation: none;
  }
}
</style>
