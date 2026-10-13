<!--
  「专家 · 技能 · 连接器」容器：三 Tab 即面板首行（上方不放标题栏等任何内容）。
  - 专家：ExpertListPanel.vue（专家列表 / 新建 / 编辑 / 召唤）
  - 技能：SkillListPanel.vue（搜索 / 分类筛选 / 启用开关 / 详情页）
  - 连接器（= MCP）：ConnectorListPanel.vue（含「已安装」筛选）+ McpConfigEditorPanel.vue
    （mcp.json 原文编辑；粘贴 JSON 添加已并入编辑器，不再单独提供添加页）

  主 Tab 交互：静止/悬停/选中三态 + 内容切换动效 + ←/→/Home/End 键盘切换 +
  ≤768px 横向滚动（关闭按钮固定在右侧）。
-->
<template>
  <div class="skill-center" ref="rootRef">
    <div class="tabs">
      <div class="tabs-list" role="tablist" aria-label="面板切换">
        <button
          v-for="(tab, index) in tabs"
          :id="`tab-${tab.key}`"
          :key="tab.key"
          class="tab main-tab"
          :class="{ active: activeTab === tab.key }"
          role="tab"
          :aria-selected="activeTab === tab.key"
          :aria-controls="`panel-${tab.key}`"
          :aria-describedby="`hint-${tab.key}`"
          :tabindex="activeTab === tab.key ? 0 : -1"
          @click="switchTab(tab.key)"
          @keydown="onTabKeydown($event, index)"
        >
          <span class="tab-icon" v-html="tab.icon"></span>
          <span class="tab-label">{{ tab.label }}</span>
          <!-- 无障碍描述（视觉隐藏，供 aria-describedby 读取） -->
          <span class="visually-hidden" :id="`hint-${tab.key}`">{{ tab.hint }}</span>
          <!-- 视觉气泡（纯装饰，鼠标/键盘聚焦时出现） -->
          <span class="tab-hint" aria-hidden="true">{{ tab.hint }}</span>
        </button>
      </div>
      <button @click="$emit('close')" class="close-btn" title="关闭" aria-label="关闭面板">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <line x1="18" y1="6" x2="6" y2="18"></line>
          <line x1="6" y1="6" x2="18" y2="18"></line>
        </svg>
      </button>
    </div>

    <Transition name="panel-fade" mode="out-in">
      <div
        :id="`panel-${activeTab}`"
        :key="activeTab"
        class="panel-slot"
        role="tabpanel"
        :aria-labelledby="`tab-${activeTab}`"
        tabindex="0"
      >
        <SkillListPanel
          v-if="activeTab === 'skills'"
          @close="$emit('close')"
          @create-skill="$emit('create-skill')"
        />

        <ExpertListPanel
          v-else-if="activeTab === 'experts'"
          :session-id="sessionId"
          @use="(expert, prompt) => $emit('use', expert, prompt)"
          @close="$emit('close')"
        />
        <div v-else-if="activeTab === 'experts'" class="center-empty">
          <span class="empty-icon">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
              <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
              <circle cx="12" cy="7" r="4"></circle>
            </svg>
          </span>
          <p class="empty-title">专家能力建设中</p>
          <p class="empty-sub">这里将提供预置角色与人设，可一键切换专家视角</p>
        </div>

        <template v-else>
          <ConnectorListPanel
            v-if="connectorView === 'list'"
            @manage="connectorView = 'manage'"
          />
          <McpConfigEditorPanel
            v-else
            @back="connectorView = 'list'"
          />
        </template>
      </div>
    </Transition>
  </div>
</template>

<script>
import SkillListPanel from './SkillListPanel.vue'
import ExpertListPanel from './ExpertListPanel.vue'
import ConnectorListPanel from './ConnectorListPanel.vue'

const T = 'xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="15" height="15" aria-hidden="true"'

// hint 同时作为悬浮提示文本与 aria-describedby 的描述，说明「这个 Tab 是做什么的」
const TAB_ICONS = {
  experts: `<svg ${T}><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>`,
  skills: `<svg ${T}><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>`,
  connectors: `<svg ${T}><path d="M9 2v6"/><path d="M15 2v6"/><path d="M6 8h12v3a6 6 0 0 1-12 0z"/><path d="M12 17v5"/></svg>`,
}

export default {
  components: {
    SkillListPanel,
    ExpertListPanel,
    ConnectorListPanel,
    // Monaco 体积较大，编辑器面板懒加载：只在进入「MCP 服务管理」时才拉取
    McpConfigEditorPanel: () => import('./McpConfigEditorPanel.vue'),
  },
  props: {
    // sessionId 仅透传给专家面板（创建专家时溯源用）；
    // initialTab 允许外部入口直接打开并定位到指定 Tab，默认选中专家 Tab。
    sessionId: {
      type: String,
      default: ''
    },
    // 从「+」菜单进入时可指定初始 Tab（experts / skills / connectors）
    initialTab: {
      type: String,
      default: 'experts',
      validator: (v) => ['experts', 'skills', 'connectors'].includes(v)
    }
  },
  emits: ['close', 'use'],
  // create-skill：技能列表「创建技能」入口，由外层接手跳转新会话预填指令
  emits: ['close', 'create-skill'],
  watch: {
    initialTab(v) {
      if (v && v !== this.activeTab) this.activeTab = v
    },
  },
  data() {
    return {
      tabs: [
        { key: 'experts', label: '专家', icon: TAB_ICONS.experts, hint: '专家：创建与管理我的专家，选中「召唤」后按专家人设对话' },
        { key: 'skills', label: '技能', icon: TAB_ICONS.skills, hint: '技能：管理公共技能与我的技能，可添加/移除、导入 zip、查看 SKILL.md' },
        { key: 'connectors', label: '连接器', icon: TAB_ICONS.connectors, hint: '连接器（MCP）：一键添加内置连接器、编辑 mcp.json 原文、接入自定义 MCP 服务' },
      ],
      activeTab: this.initialTab || 'skills',
      // 连接器内部视图：list（卡片列表）/ manage（mcp.json 编辑）
      connectorView: 'list',
    }
  },
  watch: {
    // 外部在面板保持挂载的情况下改变 initialTab（如再次点击入口）时跟随切换
    initialTab(tab) {
      if (tab && tab !== this.activeTab) this.switchTab(tab)
    },
  },
  methods: {
    switchTab(key) {
      if (this.activeTab === key) return
      this.activeTab = key
      // 每次进入连接器都回到列表视图（v-if 重挂载会重新拉取目录）
      if (key === 'connectors') this.connectorView = 'list'
    },

    // 主 Tab 键盘切换（WAI-ARIA tabs 模式）：方向键循环、Home/End 跳首尾，焦点跟随
    onTabKeydown(event, index) {
      let next = null
      const total = this.tabs.length
      if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = (index + 1) % total
      else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = (index - 1 + total) % total
      else if (event.key === 'Home') next = 0
      else if (event.key === 'End') next = total - 1
      if (next === null) return

      event.preventDefault()
      this.switchTab(this.tabs[next].key)
      this.$nextTick(() => {
        const el = this.$refs.rootRef && this.$refs.rootRef.querySelector(`#tab-${this.tabs[next].key}`)
        if (el) el.focus()
      })
    },
  },
}
</script>

<style scoped>
.skill-center {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: transparent;
  height: 100%;
  position: relative;
}

/* ── 顶部主 Tab（专家 / 技能 / 连接器）── */
.tabs {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
  padding: 14px 24px;
  background: transparent;
  border-bottom: 1px solid var(--border-color);
}

/* Tab 行：分段控件（track + thumb）。窄屏自动换行，下方悬浮提示才不会被裁掉 */
.tabs-list {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  min-width: 0;
  max-width: 100%;
  width: max-content;
  margin-right: auto;
  padding: 4px;
  border: 1px solid var(--border-color);
  border-radius: 12px;
  background: var(--bg-secondary);
}

.tab {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
  padding: 7px 14px;
  border: 1px solid transparent;
  border-radius: 9px;
  background: transparent;
  font-size: 13.5px;
  font-weight: 500;
  line-height: 1.4;
  color: var(--text-secondary);
  white-space: nowrap;
  cursor: pointer;
  touch-action: manipulation;
  /* 显式列属性，避免 transition: all */
  transition: background-color 0.18s ease, border-color 0.18s ease, color 0.18s ease, box-shadow 0.18s ease;
}

.tab-icon {
  display: inline-flex;
  align-items: center;
}

/* 无障碍描述：视觉隐藏但保留在可访问性树里（供 aria-describedby 读取） */
.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: -1px;
  padding: 0;
  border: 0;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}

/* 悬浮 / 键盘聚焦时显示「这个 Tab 做什么」的气泡提示。
   用 display 切换（而非 visibility）——隐藏时不占布局，窄屏也不会撑出横向滚动。 */
.tab-hint {
  display: none;
  position: absolute;
  top: calc(100% + 8px);
  left: 50%;
  z-index: 30;
  width: max-content;
  max-width: 240px;
  padding: 8px 10px;
  border-radius: 8px;
  background: #1e293b;
  color: #e2e8f0;
  font-size: 12px;
  font-weight: 400;
  line-height: 1.5;
  text-align: left;
  white-space: normal;
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.28);
  pointer-events: none;
  transform: translateX(-50%);
}

.tab-hint::before {
  content: '';
  position: absolute;
  top: -4px;
  left: 50%;
  width: 8px;
  height: 8px;
  margin-left: -4px;
  background: #1e293b;
  transform: rotate(45deg);
}

.tab:hover .tab-hint,
.tab:focus-visible .tab-hint {
  display: block;
}

/* 最后一个 Tab 贴着视口右缘：气泡改为右对齐，避免超出视口把页面撑出横向滚动 */
.tab:last-child .tab-hint {
  left: auto;
  right: 0;
  transform: none;
}

.tab:last-child .tab-hint::before {
  left: auto;
  right: 14px;
  margin-left: 0;
}

/* 窄屏下所有气泡统一右对齐（面板被压窄时居中气泡同样会顶出视口） */
@media (max-width: 48rem) {
  .tab-hint {
    left: auto;
    right: 0;
    transform: none;
  }

  .tab-hint::before {
    left: auto;
    right: 14px;
    margin-left: 0;
  }
}

/* 超窄屏（≤480px，面板已被压得很窄）：Tab 行改为横向滚动，
   并关掉悬浮气泡——触屏本就没有悬停，滚动也避免气泡被裁切。 */
@media (max-width: 30rem) {
  .tabs-list {
    flex-wrap: nowrap;
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    scrollbar-width: none;
  }

  .tabs-list::-webkit-scrollbar {
    display: none;
  }

  .tabs-list .tab-hint,
  .tabs-list .tab:hover .tab-hint,
  .tabs-list .tab:focus-visible .tab-hint {
    display: none;
  }
}

/* 悬停：对比度高于静止态 */
.tab:hover {
  background: var(--bg-tertiary);
  color: var(--text-primary);
}

/* 选中：淡 accent 药丸 + 描边 + 轻阴影，明暗主题下都有明确层次 */
.tab.active {
  background: color-mix(in srgb, var(--accent-color) 12%, var(--bg-surface));
  border-color: color-mix(in srgb, var(--accent-color) 30%, transparent);
  color: var(--accent-color);
  font-weight: 600;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06);
}

.tab.active:hover {
  background: color-mix(in srgb, var(--accent-color) 18%, var(--bg-surface));
}

/* 选中态的图标用主色，视觉上更快被识别 */
.tab.active .tab-icon {
  color: var(--accent-color);
}

.tab:focus-visible {
  outline: 2px solid var(--accent-color);
  outline-offset: 2px;
}

.close-btn {
  width: 32px;
  height: 32px;
  margin-left: auto;
  flex-shrink: 0;
  border: none;
  background: transparent;
  border-radius: 9px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: background-color 0.18s ease;
}

.close-btn:hover {
  background: var(--bg-tertiary);
}

.close-btn:focus-visible {
  outline: 2px solid var(--accent-color);
  outline-offset: 2px;
}

.close-btn svg {
  width: 18px;
  height: 18px;
  color: var(--text-secondary);
}

/* ── Tab 内容区（承载各面板，保持原有 flex 布局）── */
.panel-slot {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}

.panel-slot:focus-visible {
  outline: 2px solid var(--accent-color);
  outline-offset: -2px;
}

/* 切换动效：只动 opacity / transform */
.panel-fade-enter-active,
.panel-fade-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
}

.panel-fade-enter-from {
  opacity: 0;
  transform: translateY(4px);
}

.panel-fade-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

@media (prefers-reduced-motion: reduce) {
  .panel-fade-enter-active,
  .panel-fade-leave-active,
  .tab,
  .close-btn {
    transition: none;
  }
}

.center-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 32px;
  text-align: center;
  color: var(--text-secondary);
}

.empty-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 56px;
  height: 56px;
  margin-bottom: 10px;
  border: 1px solid var(--border-color);
  border-radius: 16px;
  background: var(--bg-secondary);
  color: var(--text-secondary);
}

.empty-icon svg {
  width: 26px;
  height: 26px;
}

.empty-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.empty-sub {
  margin: 2px 0 0;
  max-width: 320px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-secondary);
}

@media (max-width: 48rem) {
  .tabs {
    padding: 12px 16px;
    gap: 8px;
  }

  .tab {
    padding: 7px 12px;
    font-size: 13px;
  }
}
</style>
