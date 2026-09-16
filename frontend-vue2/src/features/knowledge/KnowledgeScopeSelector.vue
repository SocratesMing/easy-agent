<template>
  <div v-if="enabled" ref="rootRef" class="scope-selector">
    <button class="scope-trigger" :class="{ active: selected.length }" :aria-expanded="open" :disabled="disabled || saving || creatingSession" @click.stop="togglePopover">
      <IconLibrary />
      <span>{{ creatingSession ? '正在准备会话…' : triggerLabel }}</span>
      <IconChevronDown class="chevron" :class="{ rotated: open }" />
    </button>

    <transition name="scope-popover">
      <div v-if="open" class="scope-panel" :style="panelStyle">
        <header>
          <div><strong>选择本次对话使用的知识库</strong><small>每次提问仅从所选知识库检索，并保留来源依据</small></div>
          <button aria-label="关闭" @click="open = false"><IconX /></button>
        </header>

        <label class="scope-search"><IconSearch /><input v-model="query" placeholder="搜索知识库" /></label>

        <div class="scope-list">
          <label v-for="base in visibleBases" :key="base.id" class="scope-item" :class="{ checked: draft.includes(base.id) }">
            <input v-model="draft" type="checkbox" :value="base.id" :disabled="saving" />
            <span class="scope-book" :class="base.visibility"><IconBookOpen /></span>
            <span class="scope-copy"><strong>{{ base.name }}</strong><small>{{ spaceLabel(base.visibility) }} · {{ roleLabel(base.role) }} · {{ base.document_count || 0 }} 篇资料</small></span>
            <span class="scope-check"><IconCheck /></span>
          </label>
          <div v-if="!visibleBases.length" class="scope-empty"><IconBookDashed /><span>{{ query ? '没有匹配的知识库' : '暂无可用知识库' }}</span></div>
        </div>

        <div v-if="error" class="scope-error"><IconCircleAlert />{{ error }}</div>
        <footer>
          <button class="clear" :disabled="saving || !draft.length" @click="draft = []">清空选择</button>
          <button class="save" :disabled="saving" @click="save">{{ saving ? '保存中…' : '应用' }}</button>
        </footer>
      </div>
    </transition>
  </div>
</template>

<script>
import { IconBookDashed, IconBookOpen, IconCheck, IconChevronDown, IconCircleAlert, IconLibrary, IconSearch, IconX } from './icons.js'
import { getKnowledgeCapabilities, getSessionKnowledgeScope, listKnowledgeBases, replaceSessionKnowledgeScope } from './api.js'

export default {
  name: 'KnowledgeScopeSelector',
  components: {
    IconBookDashed,
    IconBookOpen,
    IconCheck,
    IconChevronDown,
    IconCircleAlert,
    IconLibrary,
    IconSearch,
    IconX,
  },
  props: {
    sessionId: { type: String, default: '' },
    disabled: { type: Boolean, default: false },
  },
  emits: ['create-session', 'change'],
  data() {
    return {
      enabled: false,
      open: false,
      bases: [],
      selected: [],
      draft: [],
      query: '',
      saving: false,
      creatingSession: false,
      error: '',
      pendingOpen: false,
      panelStyle: {},
    }
  },
  computed: {
    visibleBases() {
      const keyword = this.query.trim().toLowerCase()
      return this.bases.filter(base => !keyword || base.name.toLowerCase().includes(keyword) || (base.description || '').toLowerCase().includes(keyword))
    },
    selectedBaseNames() {
      return this.selected
        .map((id) => {
          const base = this.bases.find(item => item.id === id)
          return base ? base.name : ''
        })
        .filter(Boolean)
    },
    triggerLabel() {
      if (!this.selectedBaseNames.length) return '选择知识库'
      if (this.selectedBaseNames.length === 1) return this.selectedBaseNames[0]
      return `${this.selectedBaseNames[0]} 等 ${this.selectedBaseNames.length} 个`
    },
  },
  watch: {
    async sessionId(sessionId) {
      await this.load()
      if (sessionId && this.pendingOpen) {
        this.creatingSession = false
        this.pendingOpen = false
        this.draft = [...this.selected]
        this.updatePanelPosition()
        this.open = true
      }
    },
  },
  mounted() {
    this.load()
    document.addEventListener('pointerdown', this.handleOutside)
    window.addEventListener('resize', this.handleViewportChange)
  },
  beforeDestroy() {
    document.removeEventListener('pointerdown', this.handleOutside)
    window.removeEventListener('resize', this.handleViewportChange)
  },
  methods: {
    async load() {
      this.error = ''
      try {
        const capability = await getKnowledgeCapabilities()
        this.enabled = Boolean(capability.enabled)
        if (!this.enabled) return
        const [baseResponse, scope] = await Promise.all([
          listKnowledgeBases({ pageSize: 100 }),
          this.sessionId ? getSessionKnowledgeScope(this.sessionId) : Promise.resolve({ base_ids: [] }),
        ])
        this.bases = baseResponse.items || []
        this.selected = scope.base_ids || []
        this.draft = [...this.selected]
      } catch (reason) { this.error = reason.message || '知识范围加载失败' }
    },
    togglePopover() {
      if (this.disabled) return
      if (!this.sessionId) {
        if (this.creatingSession) return
        this.creatingSession = true
        this.pendingOpen = true
        this.$emit('create-session')
        return
      }
      if (!this.open) {
        this.draft = [...this.selected]
        this.query = ''
        this.error = ''
        this.updatePanelPosition()
      }
      this.open = !this.open
    },
    updatePanelPosition() {
      const root = this.$refs.rootRef
      if (!root) return
      const rect = root.getBoundingClientRect()
      const viewportPadding = 12
      const panelWidth = Math.min(360, window.innerWidth - viewportPadding * 2)
      const left = Math.min(
        Math.max(rect.left, viewportPadding),
        Math.max(viewportPadding, window.innerWidth - panelWidth - viewportPadding)
      )
      this.panelStyle = {
        position: 'fixed',
        left: `${left}px`,
        right: 'auto',
        top: 'auto',
        bottom: `${Math.max(viewportPadding, window.innerHeight - rect.top + 8)}px`,
        width: `${panelWidth}px`,
      }
    },
    handleViewportChange() {
      if (this.open) this.updatePanelPosition()
    },
    async save() {
      this.saving = true
      this.error = ''
      try {
        const response = await replaceSessionKnowledgeScope(this.sessionId, this.draft)
        this.selected = response.base_ids || []
        this.draft = [...this.selected]
        this.open = false
        this.$emit('change', [...this.selected])
      } catch (reason) { this.error = reason.message || '知识范围保存失败' } finally { this.saving = false }
    },
    handleOutside(event) {
      const root = this.$refs.rootRef
      if (this.open && root && !root.contains(event.target)) this.open = false
    },
    roleLabel(role) {
      return ({ viewer: '查看者', maintainer: '维护者', manager: '管理员' })[role] || role
    },
    spaceLabel(space) {
      return ({ personal: '个人', team: '团队', shared: '共享' })[space] || space
    },
  },
}
</script>

<style scoped>
.scope-selector{position:absolute;right:70px;top:14px;z-index:35;font-family:Inter,"PingFang SC","Microsoft YaHei",sans-serif}.scope-trigger{height:34px;border:1px solid #e1e5e2;background:rgba(255,255,255,.96);border-radius:17px;padding:0 11px;display:flex;align-items:center;gap:6px;color:#68716b;font-size:10px;cursor:pointer;box-shadow:0 3px 12px rgba(25,45,32,.05);transition:.18s}.scope-trigger:hover{border-color:#cbd4ce;background:#fff}.scope-trigger.active{border-color:#b7dccb;color:#188158;background:#f3faf7}.scope-trigger svg{width:13px;height:13px}.scope-trigger .chevron{width:11px;height:11px;transition:transform .18s}.scope-trigger .chevron.rotated{transform:rotate(180deg)}.scope-panel{position:absolute;right:0;top:42px;width:330px;background:#fff;border:1px solid #e3e7e4;border-radius:14px;box-shadow:0 18px 45px rgba(26,44,32,.16);padding:15px;box-sizing:border-box}.scope-panel header{display:flex;justify-content:space-between;align-items:flex-start}.scope-panel header>div{display:flex;flex-direction:column;gap:3px}.scope-panel header strong{font-size:12px;color:#2b322e}.scope-panel header small{font-size:9px;color:#969e98}.scope-panel header button{width:26px;height:26px;border:0;background:transparent;border-radius:7px;display:grid;place-items:center;color:#7e8781;cursor:pointer}.scope-panel header button:hover{background:#f3f5f3}.scope-panel svg{width:14px;height:14px}.scope-search{height:34px;margin-top:13px;border:1px solid #e1e5e2;background:#fafbfa;border-radius:9px;padding:0 9px;display:flex;align-items:center;gap:7px;color:#9ba29e}.scope-search:focus-within{border-color:#9fd8c0;background:#fff;box-shadow:0 0 0 3px rgba(37,161,111,.07)}.scope-search input{min-width:0;flex:1;border:0;outline:0;background:transparent;color:#303632;font-size:10px}.scope-list{max-height:270px;overflow:auto;margin:10px -3px 0}.scope-item{position:relative;display:grid;grid-template-columns:16px 34px 1fr 20px;gap:8px;align-items:center;padding:8px 7px;border-radius:10px;cursor:pointer;transition:.15s}.scope-item:hover{background:#f7f9f7}.scope-item.checked{background:#f1f8f4}.scope-item>input{position:absolute;opacity:0;pointer-events:none}.scope-book{width:31px;height:36px;border-radius:5px 8px 8px 5px;background:linear-gradient(145deg,#45bd8a,#229568);color:#fff;display:grid;place-items:center}.scope-book.team{background:linear-gradient(145deg,#5d9ee8,#557bc8)}.scope-book.shared{background:linear-gradient(145deg,#a788e7,#795fc4)}.scope-copy{min-width:0;display:flex;flex-direction:column;gap:3px}.scope-copy strong{font-size:10px;color:#343b37;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.scope-copy small{font-size:8px;color:#969e98;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.scope-check{width:17px;height:17px;border:1px solid #d6dcd8;border-radius:5px;color:transparent;display:grid;place-items:center}.scope-check svg{width:11px;height:11px}.scope-item.checked .scope-check{border-color:#25a16f;background:#25a16f;color:#fff}.scope-empty{height:110px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:7px;color:#9ca39e;font-size:9px}.scope-empty svg{width:23px;height:23px}.scope-error{margin-top:8px;padding:7px 9px;border-radius:8px;background:#fff3f2;color:#be4848;display:flex;gap:6px;align-items:center;font-size:9px}.scope-panel footer{display:flex;justify-content:space-between;align-items:center;margin-top:12px;padding-top:12px;border-top:1px solid #edf0ed}.scope-panel footer button{height:31px;border-radius:8px;padding:0 12px;font-size:10px;cursor:pointer}.scope-panel footer .clear{border:0;background:transparent;color:#7e8781}.scope-panel footer .save{border:1px solid #25a16f;background:#25a16f;color:#fff;min-width:64px}.scope-panel footer button:disabled{opacity:.45;cursor:not-allowed}.scope-popover-enter-active,.scope-popover-leave-active{transition:.18s}.scope-popover-enter-from,.scope-popover-leave-to{opacity:0;transform:translateY(-5px) scale(.98);transform-origin:top right}.scope-error{color:#b74348}@media(max-width:700px){.scope-selector{right:58px;top:10px}.scope-panel{width:min(330px,calc(100vw - 24px))}}

/* Match the native EasyAgent composer instead of using a standalone green theme. */
.scope-selector{position:relative;right:auto;top:auto;z-index:50;font-family:inherit;display:inline-flex;min-width:0}
.scope-trigger{height:36px;max-width:230px;border-color:var(--border-color);background:var(--bg-secondary);border-radius:10px;padding:0 10px;color:var(--text-secondary);font-size:12px;box-shadow:none}
.scope-trigger>span{max-width:165px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.scope-trigger:hover:not(:disabled){border-color:color-mix(in srgb,var(--accent-color) 40%,var(--border-color));background:color-mix(in srgb,var(--accent-color) 10%,transparent);transform:translateY(-1px)}
.scope-trigger.active{border-color:color-mix(in srgb,var(--accent-color) 48%,var(--border-color));background:color-mix(in srgb,var(--accent-color) 12%,transparent);color:var(--accent-color)}
.scope-trigger:disabled{opacity:.5;cursor:not-allowed}
.scope-panel{right:auto;left:0;top:auto;bottom:44px;width:360px;max-height:calc(100vh - 24px);overflow-y:auto;background:var(--bg-secondary);border-color:var(--border-color);border-radius:12px;box-shadow:0 8px 24px rgba(0,0,0,.14);padding:16px;color:var(--text-primary);z-index:9999}
.scope-panel header strong,.scope-copy strong{color:var(--text-primary)}
.scope-panel header small,.scope-copy small,.scope-empty{color:var(--text-secondary)}
.scope-panel header button:hover,.scope-item:hover{background:var(--bg-tertiary)}
.scope-search{background:var(--bg-primary);border-color:var(--border-color);color:var(--text-secondary)}
.scope-search:focus-within{background:var(--bg-primary);border-color:var(--accent-color);box-shadow:0 0 0 3px color-mix(in srgb,var(--accent-color) 14%,transparent)}
.scope-search input{color:var(--text-primary);font-size:12px}
.scope-item{grid-template-columns:34px minmax(0,1fr) 20px;padding:9px 8px}
.scope-item.checked{background:color-mix(in srgb,var(--accent-color) 10%,transparent)}
.scope-book{grid-column:1;background:linear-gradient(145deg,#38bdf8,#0284c7)}
.scope-copy{grid-column:2}.scope-check{grid-column:3;border-color:var(--border-color)}
.scope-copy strong{font-size:12px}.scope-copy small{font-size:10px}.scope-item.checked .scope-check{border-color:var(--accent-color);background:var(--accent-color)}
.scope-panel footer{border-top-color:var(--border-color)}
.scope-panel footer .clear{color:var(--text-secondary);background:transparent}
.scope-panel footer .save{border-color:var(--accent-color);background:var(--accent-color);font-size:12px}
@media(max-width:700px){.scope-selector{right:auto;top:auto}.scope-panel{left:auto;right:-76px;width:min(360px,calc(100vw - 24px))}}
</style>
