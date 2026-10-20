<template>
  <div v-if="enabled" ref="rootRef" class="scope-selector" :class="{ 'trigger-hidden': hideTrigger }">
    <button class="scope-trigger" :class="{ active: selected.length }" :aria-expanded="open" :disabled="disabled" @click.stop="togglePopover">
      <IconLibrary />
      <span>{{ triggerLabel }}</span>
      <IconChevronDown class="chevron" :class="{ rotated: open }" />
    </button>

    <!-- 面板样式对齐「+」菜单技能子面板：标题+数量 / 条目列表（勾选即生效）/ 底部管理入口 -->
    <Transition name="scope-popover">
      <div v-if="open" class="scope-panel" :style="panelStyle">
        <div class="scope-head">
          <span class="scope-title">我的知识库</span>
          <span class="scope-count">{{ bases.length }}</span>
        </div>

        <div class="scope-list">
          <div v-if="loading" class="scope-hint">加载中…</div>
          <div v-else-if="!bases.length" class="scope-hint">{{ error || '暂无可用知识库' }}</div>
          <label v-else v-for="base in bases" :key="base.id" class="scope-item" :class="{ checked: draft.includes(base.id) }">
            <input v-model="draft" type="checkbox" :value="base.id" />
            <span class="scope-item-icon"><IconBookOpen /></span>
            <span class="scope-item-body">
              <span class="scope-item-name-row">
                <span class="scope-item-name">{{ base.name }}</span>
                <span class="scope-item-tag" :class="draft.includes(base.id) ? 'on' : 'off'">{{ draft.includes(base.id) ? '已选' : '未选' }}</span>
              </span>
              <span class="scope-item-desc">{{ spaceLabel(base.visibility) }} · {{ roleLabel(base.role) }} · {{ base.document_count || 0 }} 篇资料</span>
            </span>
          </label>
        </div>

        <div v-if="error && bases.length" class="scope-error">{{ error }}</div>
        <div v-if="showManage" class="scope-footer">
          <button type="button" class="scope-manage" @click="openManage">在管理面板中打开 →</button>
        </div>
      </div>
    </Transition>
  </div>
</template>

<script>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { IconBookOpen, IconChevronDown, IconLibrary } from './icons.js'
import { getKnowledgeCapabilities, getSessionKnowledgeScope, listKnowledgeBases, replaceSessionKnowledgeScope } from './api.js'
import { eventTarget } from '../../utils/domEvent.js'

export default {
  components: {
    IconBookOpen,
    IconChevronDown,
    IconLibrary,
  },
  props: {
    sessionId: { type: String, default: '' },
    disabled: { type: Boolean, default: false },
    // 隐藏常驻触发按钮（入口由宿主提供，如加号菜单）
    hideTrigger: { type: Boolean, default: false },
    // 面板定位锚点（CSS 选择器）：加号菜单场景传「知识库」菜单项，
    // 面板贴着菜单项右侧弹出（与技能子面板同款定位）
    anchorSelector: { type: String, default: '' },
    // 底部「在管理面板中打开」入口（SDK 未配置主站地址时无处可跳，隐藏）
    showManage: { type: Boolean, default: true },
  },
  emits: ['change', 'manage'],
  setup(props, { emit }) {
const rootRef = ref(null)
const enabled = ref(false)
const open = ref(false)
const loading = ref(false)
const bases = ref([])
const selected = ref([])
const draft = ref([])
const saving = ref(false)
const error = ref('')
const panelStyle = ref({})
const selectedBaseNames = computed(() => selected.value
  .map((id) => {
    const base = bases.value.find(item => item.id === id)
    return base ? base.name : ''
  })
  .filter(Boolean))
const triggerLabel = computed(() => {
  if (!selectedBaseNames.value.length) return '选择知识库'
  if (selectedBaseNames.value.length === 1) return selectedBaseNames.value[0]
  return `${selectedBaseNames.value[0]} 等 ${selectedBaseNames.value.length} 个`
})

let loadSeq = 0
async function load() {
  const seq = ++loadSeq
  error.value = ''
  // 已有数据时静默刷新，避免面板每次展开都闪「加载中」
  loading.value = !bases.value.length
  try {
    const capability = await getKnowledgeCapabilities()
    enabled.value = Boolean(capability.enabled)
    if (!enabled.value) return
    const [baseResponse, scope] = await Promise.all([
      listKnowledgeBases({ pageSize: 100 }),
      props.sessionId ? getSessionKnowledgeScope(props.sessionId) : Promise.resolve({ base_ids: [] })
    ])
    bases.value = baseResponse.items || []
    // 保存完成（persist 会推进 loadSeq）或已有更新的加载时，丢弃过期快照
    if (seq !== loadSeq) return
    // 面板打开期间用户已勾选（保存中）时不覆盖；无会话时的勾选是本地暂存
    // （发消息建会话后才落库），不能用服务端空快照覆盖
    if (!saving.value && props.sessionId) {
      selected.value = scope.base_ids || []
      draft.value = [...selected.value]
    }
  } catch (reason) { error.value = reason.message || '知识范围加载失败' }
  finally { loading.value = false }
}

function togglePopover() {
  if (props.disabled) return
  // 无会话也可打开面板：勾选先暂存在前端（不建会话），
  // 等发消息真正建立会话时由宿主落库
  if (!open.value) {
    draft.value = [...selected.value]
    error.value = ''
    updatePanelPosition()
  }
  open.value = !open.value
}

// 供加号菜单 hover/click 展开：幂等打开面板。
// anchor 为触发的菜单项元素；每次展开都重新拉取列表，主页面新建的知识库无需刷新页面即可看到
let anchorEl = null
async function openPopover(anchor) {
  if (props.disabled || open.value) return
  if (anchor) anchorEl = anchor
  draft.value = [...selected.value]
  error.value = ''
  updatePanelPosition()
  open.value = true
  await load()
}
function closePopover() { if (open.value) open.value = false }

// 面板贴着锚点（如加号菜单「知识库」菜单项）右侧弹出，
// 定位方式与 ChatInput 的技能/连接器子面板（openSubpanel）保持一致
function updatePanelPosition() {
  // 页面上可能同时存在多个输入框（主对话区 + AI 侧边栏），且 SDK 运行在 Shadow DOM 中，
  // 不能用 document.querySelector 全局查找锚点
  const anchor = (anchorEl && anchorEl.isConnected ? anchorEl : null)
    || (props.anchorSelector && rootRef.value && rootRef.value.getRootNode().querySelector(props.anchorSelector))
    || rootRef.value
  if (!anchor) return
  const rect = anchor.getBoundingClientRect()
  const panelWidth = Math.min(380, window.innerWidth * 0.9)
  const estimatedHeight = 420
  const top = Math.max(8, Math.min(rect.top - 8, window.innerHeight - estimatedHeight - 8))
  const left = rect.right + 10 + panelWidth > window.innerWidth
    ? Math.max(8, rect.left - panelWidth - 10)
    : rect.right + 10
  panelStyle.value = {
    position: 'fixed',
    top: `${top}px`,
    left: `${left}px`,
    width: '380px',
    maxWidth: '90vw',
  }
}

// 面板底部入口：交由宿主打开知识库工作台
function openManage() { emit('manage') }

function handleViewportChange() {
  if (open.value) updatePanelPosition()
}
let saveSeq = 0
function sameSet(a, b) {
  const ka = [...a].sort().join('|')
  const kb = [...b].sort().join('|')
  return ka === kb
}
async function persist(snapshot) {
  const seq = ++saveSeq
  saving.value = true; error.value = ''
  try {
    const response = await replaceSessionKnowledgeScope(props.sessionId, snapshot)
    if (seq !== saveSeq) return  // 已有更新的勾选在保存，丢弃过期响应
    loadSeq++  // 作废在途的旧快照读取，避免保存结果被早先发起的 load 覆盖
    selected.value = response.base_ids || []
    emit('change', [...selected.value])
  }
  catch (reason) {
    if (seq === saveSeq) error.value = reason.message || '知识范围保存失败'
  }
  finally {
    if (seq === saveSeq) saving.value = false
  }
}
// 宿主（App/AiSidebar）新建会话后调用：把无会话期间暂存的知识范围落库到新会话
async function applyPendingScope(baseIds) {
  if (!props.sessionId || !Array.isArray(baseIds)) return
  selected.value = [...baseIds]
  draft.value = [...baseIds]
  await persist([...baseIds])
}
watch(draft, () => {
  if (!open.value) return
  const snapshot = [...draft.value]
  if (sameSet(snapshot, selected.value)) return  // 打开面板时的同步赋值，无实际变化
  if (!props.sessionId) {
    // 无会话时勾选（新对话场景）：仅本地暂存并通知宿主，不建会话；
    // 等发消息真正建立会话后，宿主调 applyPendingScope 落库
    selected.value = snapshot
    emit('change', [...snapshot])
    return
  }
  persist(snapshot)
}, { deep: true })
function handleOutside(event) { if (open.value && rootRef.value && !rootRef.value.contains(eventTarget(event))) open.value = false }
function roleLabel(role) { return ({ viewer: '查看者', maintainer: '维护者', manager: '管理员' })[role] || role }
function spaceLabel(space) { return ({ personal: '个人', team: '团队', shared: '共享' })[space] || space }

watch(() => props.sessionId, async () => {
  // 无会话 → 新建会话：宿主已把暂存勾选落库（applyPendingScope），
  // 这里仅刷新列表与勾选态；会话间切换则以服务端快照为准
  await load()
})
onMounted(() => {
  load()
  document.addEventListener('pointerdown', handleOutside)
  window.addEventListener('resize', handleViewportChange)
})
onUnmounted(() => {
  document.removeEventListener('pointerdown', handleOutside)
  window.removeEventListener('resize', handleViewportChange)
})

    return {
      applyPendingScope,
      bases,
      disabled: computed(() => props.disabled),
      draft,
      enabled,
      error,
      load,
      loading,
      open,
      openManage,
      panelStyle,
      roleLabel,
      rootRef,
      saving,
      selected,
      spaceLabel,
      togglePopover,
      openPopover,
      closePopover,
      triggerLabel,
    }
  },
}
</script>

<style scoped>
/* 根节点：常驻触发按钮隐藏时仅承载弹层 */
.scope-selector{position:relative;z-index:50;font-family:inherit;display:inline-flex;min-width:0}
.scope-selector.trigger-hidden .scope-trigger{display:none}

/* 常驻触发按钮（hide-trigger 场景不展示，保留独立使用时的样式） */
.scope-trigger{height:36px;max-width:230px;display:flex;align-items:center;gap:6px;border:1px solid var(--border-color);background:var(--bg-secondary);border-radius:10px;padding:0 10px;color:var(--text-secondary);font-size:12px;cursor:pointer;box-shadow:none}
.scope-trigger svg{width:13px;height:13px;flex-shrink:0}
.scope-trigger .chevron{width:11px;height:11px;transition:transform .18s}
.scope-trigger .chevron.rotated{transform:rotate(180deg)}
.scope-trigger>span{max-width:165px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.scope-trigger:hover:not(:disabled){border-color:color-mix(in srgb,var(--accent-color) 40%,var(--border-color));background:color-mix(in srgb,var(--accent-color) 10%,transparent)}
.scope-trigger.active{border-color:color-mix(in srgb,var(--accent-color) 48%,var(--border-color));background:color-mix(in srgb,var(--accent-color) 12%,transparent);color:var(--accent-color)}
.scope-trigger:disabled{opacity:.5;cursor:not-allowed}

/* ========== 面板：与「+」菜单技能子面板（.plus-subpanel）同款样式 ========== */
.scope-panel{position:fixed;width:380px;max-width:90vw;display:flex;flex-direction:column;background:var(--bg-secondary,#fff);border:1px solid var(--border-color,#e2e8f0);border-radius:12px;box-shadow:0 12px 36px rgba(0,0,0,.14);padding:12px;box-sizing:border-box;color:var(--text-primary,#1e293b);z-index:9999}
.scope-head{display:flex;align-items:center;gap:8px;padding:2px 4px 8px}
.scope-title{flex:1;font-size:14px;font-weight:600;color:var(--text-primary,#1e293b)}
.scope-count{font-size:12px;color:var(--text-secondary,#64748b);font-variant-numeric:tabular-nums}
.scope-list{max-height:300px;overflow-y:auto;display:flex;flex-direction:column;gap:2px;scrollbar-width:thin}
.scope-item{position:relative;display:flex;align-items:flex-start;gap:10px;padding:8px 10px;border-radius:8px;cursor:pointer;transition:background .15s ease}
.scope-item:hover{background:var(--bg-tertiary,#f1f5f9)}
.scope-item.checked{background:color-mix(in srgb,var(--accent-color,#16a34a) 10%,transparent)}
.scope-item>input{position:absolute;opacity:0;pointer-events:none}
.scope-item-icon{display:flex;align-items:center;justify-content:center;width:28px;height:28px;flex-shrink:0;border-radius:8px;background:color-mix(in srgb,var(--accent-color,#16a34a) 12%,var(--bg-tertiary,#f1f5f9));color:var(--accent-color,#16a34a)}
.scope-item-icon svg{width:15px;height:15px}
.scope-item-body{flex:1;min-width:0;display:flex;flex-direction:column;gap:2px}
.scope-item-name-row{display:flex;align-items:center;gap:6px;min-width:0}
.scope-item-name{flex:1;min-width:0;font-size:13px;font-weight:600;color:var(--text-primary,#1e293b);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.scope-item-tag{flex-shrink:0;padding:1px 6px;border-radius:5px;font-size:11px}
.scope-item-tag.on{background:#dcfce7;color:#16a34a}
.scope-item-tag.off{background:var(--bg-tertiary,#f1f5f9);color:var(--text-secondary,#64748b)}
.scope-item-desc{font-size:12px;line-height:1.5;color:var(--text-secondary,#64748b);display:-webkit-box;-webkit-line-clamp:1;line-clamp:1;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}
.scope-hint{padding:16px 10px;font-size:13px;color:var(--text-secondary,#64748b);text-align:center}
.scope-error{margin-top:4px;padding:8px 10px;border-radius:8px;font-size:12px;color:#dc2626;background:rgba(220,38,38,.06);text-align:center}
.scope-footer{margin-top:8px;padding-top:6px;border-top:1px solid var(--border-color,#e2e8f0)}
.scope-manage{width:100%;padding:8px 10px;border:none;border-radius:8px;background:transparent;font-size:13px;color:var(--text-secondary,#64748b);cursor:pointer;text-align:center}
.scope-manage:hover{background:rgba(15,23,42,.05)}
.scope-popover-enter-active,.scope-popover-leave-active{transition:.18s}
.scope-popover-enter-from,.scope-popover-leave-to{opacity:0;transform:translateY(-5px) scale(.98);transform-origin:top left}
</style>
