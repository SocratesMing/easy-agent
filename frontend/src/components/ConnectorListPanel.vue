<!--
  连接器列表（= MCP）：搜索 + 自定义连接器入口 + 卡片网格 + 详情弹层。
  点卡片弹出描述与对应的 mcp.json 片段；已安装/自定义的项可在弹层里移除。
-->
<template>
  <div class="connector-panel">
    <div class="connector-toolbar">
      <div class="connector-search">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="11" cy="11" r="8"></circle>
          <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
        </svg>
        <input v-model="keyword" type="text" placeholder="搜索连接器" aria-label="搜索连接器" />
      </div>
      <button
        class="installed-filter"
        :class="{ active: installedOnly }"
        :aria-pressed="installedOnly ? 'true' : 'false'"
        :title="`只看已安装的 ${installedCount} 个 MCP 服务`"
        @click="installedOnly = !installedOnly"
      >
        已安装<span class="filter-count">{{ installedCount }}</span>
      </button>
      <button class="custom-connector-btn" @click="$emit('manage')">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <line x1="4" y1="21" x2="4" y2="14"></line>
          <line x1="4" y1="10" x2="4" y2="3"></line>
          <line x1="12" y1="21" x2="12" y2="12"></line>
          <line x1="12" y1="8" x2="12" y2="3"></line>
          <line x1="20" y1="21" x2="20" y2="16"></line>
          <line x1="20" y1="12" x2="20" y2="3"></line>
          <line x1="1" y1="14" x2="7" y2="14"></line>
          <line x1="9" y1="8" x2="15" y2="8"></line>
          <line x1="17" y1="16" x2="23" y2="16"></line>
        </svg>
        自定义连接器
      </button>
    </div>

    <div v-if="notice" class="connector-notice" :class="noticeType">{{ notice }}</div>

    <div class="connector-content">
      <div v-if="loading" class="loading-state"><div class="spinner"></div></div>

      <div v-else-if="error" class="error-state">
        <p>{{ error }}</p>
        <button class="retry-btn" @click="load">重试</button>
      </div>

      <div v-else-if="cards.length === 0" class="empty-state">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <path d="M9 2v6"></path>
          <path d="M15 2v6"></path>
          <path d="M6 8h12v3a6 6 0 0 1-12 0z"></path>
          <path d="M12 17v5"></path>
        </svg>
        <p>{{ emptyText }}</p>
      </div>

      <div v-else class="connector-grid">
        <div
          v-for="card in cards"
          :key="card.key"
          class="connector-card"
          @click="openDetail(card)"
        >
          <div class="connector-card-inner">
            <div class="connector-icon" :class="[toneClass(card), { custom: card.custom }]">
              <svg
                v-if="iconSvg(card.icon)"
                xmlns="http://www.w3.org/2000/svg"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
                aria-hidden="true"
                v-html="iconSvg(card.icon)"
              ></svg>
              <template v-else>{{ card.initial }}</template>
            </div>
            <div class="connector-body">
              <div class="connector-name-row">
                <span class="connector-name">{{ card.name }}</span>
                <span v-if="card.custom" class="connector-tag">自定义</span>
                <span v-else-if="card.installed" class="connector-tag installed">已添加</span>
                <!-- 连接状态：正常 / 异常 / 已停用（悬停显示错误详情） -->
                <span
                  v-if="card.installed"
                  class="conn-status"
                  :class="statusClass(card)"
                  :title="statusOf(card)?.error || statusText(card)"
                >
                  <span class="conn-status-dot"></span>{{ statusText(card) }}
                </span>
              </div>
              <div class="connector-desc">{{ card.description || '暂无描述' }}</div>
            </div>
            <!-- 启用开关：已安装项显示（停用后 Agent 不加载其工具，配置保留） -->
            <ToggleSwitch
              v-if="card.installed"
              :modelValue="card.enabled"
              :loading="toggling === card.key"
              :ariaLabel="(card.enabled ? '停用' : '启用') + card.name"
              @change="toggle(card)"
              @click.stop
            />
            <button
              v-else
              class="connector-add-btn"
              :disabled="installing === card.key"
              title="添加"
              @click.stop="install(card)"
            >
              <div v-if="installing === card.key" class="btn-spinner-sm"></div>
              <svg v-else xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                <line x1="12" y1="5" x2="12" y2="19"></line>
                <line x1="5" y1="12" x2="19" y2="12"></line>
              </svg>
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- 连接器详情 -->
    <Teleport to="body">
      <div
        v-if="detail"
        class="connector-detail-overlay"
        @mousedown="overlayPress = markOverlayPress($event)"
        @click.self="overlayClickCloses($event, overlayPress, () => (detail = null))"
      >
        <div class="connector-detail-card" @click.stop>
          <div class="connector-detail-header">
            <div class="connector-icon" :class="[toneClass(detail), { custom: detail.custom }]">
              <svg
                v-if="iconSvg(detail.icon)"
                xmlns="http://www.w3.org/2000/svg"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
                aria-hidden="true"
                v-html="iconSvg(detail.icon)"
              ></svg>
              <template v-else>{{ detail.initial }}</template>
            </div>
            <div class="connector-detail-title">
              <div class="connector-detail-name">
                {{ detail.name }}
                <span v-if="detail.custom" class="connector-tag">自定义</span>
              </div>
              <div class="connector-detail-sub">{{ detail.custom ? '用户自定义 MCP 服务' : '内置连接器' }}</div>
            </div>
            <!-- 启用开关：已安装项可在详情里直接启/停 -->
            <ToggleSwitch
              v-if="detail.installed"
              :modelValue="detail.enabled"
              :loading="toggling === detail.key"
              :ariaLabel="(detail.enabled ? '停用' : '启用') + detail.name"
              @change="toggle(detail)"
            />
            <button class="detail-close-btn" title="关闭" @click="detail = null">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <line x1="18" y1="6" x2="6" y2="18"></line>
                <line x1="6" y1="6" x2="18" y2="18"></line>
              </svg>
            </button>
          </div>

          <div class="connector-detail-body">
            <p class="connector-detail-desc">{{ detail.description || '暂无描述' }}</p>

            <!-- 连接状态（已安装项）：含错误详情与工具数，可重新检测 -->
            <div v-if="detail.installed" class="detail-status-row">
              <span class="conn-status" :class="statusClass(detail)">
                <span class="conn-status-dot"></span>{{ statusText(detail) }}
              </span>
              <span v-if="statusOf(detail)?.tools_count" class="detail-status-tools">
                {{ statusOf(detail).tools_count }} 个工具
              </span>
              <span v-if="statusOf(detail)?.error" class="detail-status-error">{{ statusOf(detail).error }}</span>
              <button class="tools-load-btn" :disabled="statusLoading" @click="refreshStatus">
                {{ statusLoading ? '检测中...' : '重新检测' }}
              </button>
            </div>

            <!-- 工具列表：连接该 server 拉取实际暴露的工具 -->
            <template v-if="detail.installed">
              <div class="connector-detail-label">
                工具列表
                <button
                  v-if="!toolsLoaded"
                  class="tools-load-btn"
                  :disabled="toolsLoading"
                  @click="loadTools(detail)"
                >
                  {{ toolsLoading ? '获取中...' : '查看工具' }}
                </button>
              </div>
              <div v-if="toolsLoading" class="loading-state tools-state"><div class="spinner"></div></div>
              <div v-else-if="toolsError" class="connector-detail-error">{{ toolsError }}</div>
              <div v-else-if="toolsLoaded" class="tools-list">
                <div v-if="tools.length === 0" class="tools-empty">该服务未暴露任何工具</div>
                <div v-for="tool in tools" :key="tool.name" class="tools-item">
                  <span class="tools-name">{{ tool.name }}</span>
                  <span v-if="tool.description" class="tools-desc">{{ tool.description }}</span>
                </div>
              </div>
            </template>

            <!-- 可编辑字段：已安装项仅开放白名单字段（url / key 等） -->
            <template v-if="detail.installed && detail.fields && detail.fields.length">
              <div class="connector-detail-label">配置（仅可修改以下字段）</div>
              <div class="fields-grid">
                <label v-for="f in detail.fields" :key="f.key" class="fields-item">
                  <span class="fields-label">{{ f.label }}</span>
                  <input
                    v-model="editValues[f.key]"
                    type="text"
                    :placeholder="f.placeholder || ''"
                    autocomplete="off"
                  />
                </label>
              </div>
              <p v-if="detailError" class="connector-detail-error">{{ detailError }}</p>
            </template>

            <div class="connector-detail-label">mcp.json 配置</div>
            <pre class="connector-snippet">{{ detailSnippet }}</pre>
            <p v-if="!detailEditable && detailError" class="connector-detail-error">{{ detailError }}</p>
          </div>

          <div class="connector-detail-footer">
            <button
              v-if="detailEditable"
              class="action-btn-outline"
              :disabled="savingConfig"
              @click="saveConfig"
            >
              {{ savingConfig ? '保存中...' : '保存修改' }}
            </button>
            <button class="action-btn-outline" @click="copySnippet">
              {{ copied ? '已复制' : '复制配置' }}
            </button>
            <button
              v-if="detail.installed"
              class="action-btn-danger"
              :disabled="removing === detail.key"
              @click="uninstall(detail)"
            >
              {{ removing === detail.key ? '移除中...' : '移除' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- 安装填写弹窗：目录条目声明了 fields（鉴权 Key / 路径等）时先收集值，
         有多少必填/可选字段就列多少（样式参考「运行 TCA MCP Server」弹窗） -->
    <Teleport to="body">
      <div
        v-if="installModal"
        class="connector-detail-overlay"
        @mousedown="overlayPress = markOverlayPress($event)"
        @click.self="overlayClickCloses($event, overlayPress, () => (installModal = null))"
      >
        <div class="connector-detail-card install-modal" @click.stop>
          <div class="connector-detail-header">
            <div class="connector-detail-title">
              <div class="connector-detail-name">添加「{{ installModal.card.name }}」</div>
              <div class="connector-detail-sub">请填写该 MCP 服务需要的信息</div>
            </div>
            <button class="detail-close-btn" title="关闭" @click="installModal = null">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <line x1="18" y1="6" x2="6" y2="18"></line>
                <line x1="6" y1="6" x2="18" y2="18"></line>
              </svg>
            </button>
          </div>
          <div class="connector-detail-body">
            <p v-if="installModal.card.description" class="connector-detail-desc">{{ installModal.card.description }}</p>
            <div class="fields-grid">
              <label v-for="f in installModal.card.fields" :key="f.key" class="fields-item">
                <span class="fields-label">
                  {{ f.label }}
                  <span v-if="f.required" class="fields-required">*</span>
                </span>
                <input
                  v-model="installModal.values[f.key]"
                  type="text"
                  :placeholder="f.placeholder || ''"
                  autocomplete="off"
                />
              </label>
            </div>
            <p v-if="installError" class="connector-detail-error">{{ installError }}</p>
          </div>
          <div class="connector-detail-footer">
            <button
              class="action-btn-outline"
              :disabled="installing === installModal.card.key"
              @click="confirmInstall(true)"
            >
              {{ installing === installModal.card.key ? '连接中...' : '运行' }}
            </button>
            <button
              class="action-btn-primary"
              :disabled="installing === installModal.card.key"
              @click="confirmInstall(false)"
            >
              保存
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import ToggleSwitch from './ToggleSwitch.vue'
import {
  getConnectors,
  installConnector,
  uninstallConnector,
  toggleConnector,
  getConnectorTools,
  updateConnectorConfig,
  getConnectorsStatus,
} from '../api/connectors.js'
import { overlayClickCloses, markOverlayPress } from '../utils/overlayClick.js'

defineEmits(['manage'])

// ========== 连接器图标与主题色 ==========
// 目录条目的 icon 名 → 语义 SVG（lucide 风格描边，与页面其它图标一致）；
// 未命中的 icon 回退首字母。每个连接器按 id 稳定分配一组鲜明主题色，
// 图标块浅底深字，hover 时卡片描边染上同色系（暗色主题见样式区 tone-N 覆盖）。
const CONNECTOR_ICON_PATHS = {
  'chart-line': '<path d="M3 3v16a2 2 0 0 0 2 2h16"/><polyline points="7 14 11 10 14 12 20 6"/>',
  'folder-outline': '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
  'web': '<circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
  'source-branch': '<line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/>',
  'flask-outline': '<path d="M10 2v7.5L4.6 18.9A2 2 0 0 0 6.4 22h11.2a2 2 0 0 0 1.8-3.1L14 9.5V2"/><line x1="8.5" y1="2" x2="15.5" y2="2"/><line x1="7" y1="15" x2="17" y2="15"/>',
  'database-outline': '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>',
  'chat-question-outline': '<path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
  'cube-outline': '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/>',
}

const CONNECTOR_TONES = [
  { bg: '#e0f2fe', fg: '#0284c7' }, // sky
  { bg: '#ede9fe', fg: '#7c3aed' }, // violet
  { bg: '#fef3c7', fg: '#d97706' }, // amber
  { bg: '#d1fae5', fg: '#059669' }, // emerald
  { bg: '#fce7f3', fg: '#db2777' }, // rose
  { bg: '#e0e7ff', fg: '#4f46e5' }, // indigo
  { bg: '#ffedd5', fg: '#ea580c' }, // orange
]

function toneClass(card) {
  // 自定义连接器沿用既有紫色首字母样式
  if (!card || card.custom) return ''
  let hash = 0
  const key = String(card.key || '')
  for (let i = 0; i < key.length; i++) hash = (hash * 31 + key.charCodeAt(i)) >>> 0
  return `tone-${hash % CONNECTOR_TONES.length}`
}

function iconSvg(icon) {
  return CONNECTOR_ICON_PATHS[icon] || ''
}

const loading = ref(false)
const error = ref('')
const connectors = ref([])
const customServers = ref([])
const keyword = ref('')
const installedOnly = ref(false)

const installing = ref('')
const overlayPress = ref(null)
// 已安装 MCP 的连接状态：key(server名) → {status: ok|error, tools_count, error}
const statusMap = ref({})
const statusLoading = ref(false)
const toggling = ref('')
const removing = ref('')
const savingConfig = ref(false)
const notice = ref('')
const noticeType = ref('success')
const detail = ref(null)
const detailError = ref('')
const copied = ref(false)

// 安装填写弹窗：{card, values}；工具列表 / 详情内字段编辑
const installModal = ref(null)
const installError = ref('')
const tools = ref([])
const toolsLoading = ref(false)
const toolsLoaded = ref(false)
const toolsError = ref('')
const editValues = reactive({})

function showNotice(message, type = 'success') {
  notice.value = message
  noticeType.value = type
  setTimeout(() => { notice.value = '' }, 3000)
}

// 卡片：内置目录项 + 用户 mcp.json 里的自定义项；已安装的统一排在最前面
const cards = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  const match = (text) => !kw || String(text || '').toLowerCase().includes(kw)

  const fromCatalog = connectors.value
    .filter(c => (!installedOnly.value || c.installed) && (match(c.name) || match(c.id) || match(c.description)))
    .map(c => ({
      key: c.id,
      name: c.name,
      description: c.description,
      icon: c.icon,
      config: c.config,
      fields: c.fields || [],
      installed: !!c.installed,
      enabled: !!c.enabled,
      userConfig: c.user_config || null,
      custom: false,
      initial: (c.name || c.id).slice(0, 1).toUpperCase(),
    }))

  const fromUser = customServers.value
    .filter(c => match(c.name))
    .map(c => ({
      key: c.name,
      name: c.name,
      description: '用户自定义 MCP 服务',
      icon: '',
      config: c.config,
      fields: [],
      installed: true,
      enabled: !!c.enabled,
      userConfig: c.config,
      custom: true,
      initial: c.name.slice(0, 1).toUpperCase(),
    }))

  // 已安装（含自定义）置顶：先安装的目录项与自定义项，再未安装目录项
  const all = [...fromCatalog, ...fromUser]
  return all.sort((a, b) => Number(b.installed) - Number(a.installed))
})

// 已安装数 = 内置目录中已添加的 + 用户自定义（自定义项必然已安装）
const installedCount = computed(
  () => connectors.value.filter(c => c.installed).length + customServers.value.length,
)

const emptyText = computed(() => {
  if (keyword.value.trim()) return '没有匹配的连接器'
  if (installedOnly.value) return '还没有已安装的 MCP，点卡片上的 + 添加，或「自定义连接器」编辑配置'
  return '暂无连接器，点右上角「自定义连接器」编辑 mcp.json 添加 MCP 服务'
})

const detailSnippet = computed(() => {
  if (!detail.value) return ''
  // 已安装项展示用户实际配置（可能与目录默认不同）
  const cfg = detail.value.installed && detail.value.userConfig
    ? detail.value.userConfig
    : detail.value.config
  return JSON.stringify({ servers: { [detail.value.key]: cfg } }, null, 2)
})

// 详情里是否显示字段编辑：已安装且条目声明了 fields
const detailEditable = computed(() =>
  !!detail.value?.installed && (detail.value?.fields?.length || 0) > 0,
)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await getConnectors()
    connectors.value = data.connectors || []
    customServers.value = data.custom || []
    // 详情打开时同步最新状态（开关切换后 reload 会走到这里）
    if (detail.value) {
      const fresh = [...connectors.value, ...customServers.value.map(c => ({ ...c, id: c.name }))]
        .find(x => x.id === detail.value.key)
      if (fresh) {
        detail.value = {
          ...detail.value,
          enabled: !!fresh.enabled,
          userConfig: fresh.user_config || fresh.config || detail.value.userConfig,
        }
      } else {
        detail.value = null // 已被移除
      }
    }
  } catch (e) {
    error.value = e.message || '加载连接器失败'
  } finally {
    loading.value = false
  }
  // 列表就绪后异步校验已安装项的连接状态（不阻塞渲染）
  refreshStatus()
}

// ========== 连接状态：对全部已安装项并发校验，卡片/详情显示正常或异常 ==========
async function refreshStatus() {
  if (statusLoading.value) return
  statusLoading.value = true
  try {
    const data = await getConnectorsStatus()
    const map = {}
    for (const st of data.statuses || []) map[st.name] = st
    statusMap.value = map
  } catch {
    // 状态获取失败不影响列表展示（徽标保持未知态）
  } finally {
    statusLoading.value = false
  }
}

function statusOf(card) {
  if (!card.installed) return null
  return statusMap.value[card.key] || null
}

function statusText(card) {
  if (!card.installed) return ''
  if (!card.enabled) return '已停用'
  const st = statusOf(card)
  if (!st) return '检测中'
  return st.status === 'ok' ? '正常' : '异常'
}

function statusClass(card) {
  if (!card.installed) return ''
  if (!card.enabled) return 'disabled'
  const st = statusOf(card)
  if (!st) return 'checking'
  return st.status === 'ok' ? 'ok' : 'error'
}

function openDetail(card) {
  detail.value = card
  detailError.value = ''
  copied.value = false
  tools.value = []
  toolsLoaded.value = false
  toolsError.value = ''
  // 预填可编辑字段当前值（从用户实际配置按字段路径取）
  Object.keys(editValues).forEach(k => delete editValues[k])
  if (detailEditable.value) {
    const cfg = detail.value.userConfig || detail.value.config || {}
    for (const f of detail.value.fields) {
      editValues[f.key] = getPathValue(cfg, f.key) ?? ''
    }
  }
  // 已安装项自动拉取工具列表
  if (detail.value.installed) loadTools(detail.value)
}

/** 按 "headers.Authorization" / "args.1" 路径取配置值 */
function getPathValue(cfg, keyPath) {
  let cur = cfg
  for (const seg of keyPath.split('.')) {
    if (cur == null) return undefined
    if (Array.isArray(cur)) {
      if (!/^\d+$/.test(seg) || Number(seg) >= cur.length) return undefined
      cur = cur[Number(seg)]
    } else if (typeof cur === 'object') {
      cur = cur[seg]
    } else {
      return undefined
    }
  }
  return cur
}

// ========== 启用开关 ==========
async function toggle(card) {
  if (toggling.value) return
  toggling.value = card.key
  try {
    await toggleConnector(card.key, !card.enabled)
    card.enabled = !card.enabled
    await load()
    showNotice(`已${card.enabled ? '启用' : '停用'}「${card.name}」`)
  } catch (e) {
    showNotice(e.message || '操作失败', 'error')
  } finally {
    toggling.value = ''
  }
}

// ========== 工具列表 ==========
async function loadTools(card) {
  if (toolsLoading.value) return
  toolsLoading.value = true
  toolsError.value = ''
  try {
    const data = await getConnectorTools(card.key)
    if (data.error) {
      toolsError.value = `连接失败：${data.error}`
      tools.value = []
    } else {
      tools.value = data.tools || []
    }
    toolsLoaded.value = true
  } catch (e) {
    toolsError.value = e.message || '获取工具列表失败'
  } finally {
    toolsLoading.value = false
  }
}

// ========== 安装（含 fields 填写弹窗） ==========
async function install(card) {
  if (card.installed || installing.value) return
  if ((card.fields?.length || 0) > 0) {
    // 有需要用户填写的字段（鉴权 Key / 路径等）：弹窗收集，有多少列多少
    const values = {}
    for (const f of card.fields) values[f.key] = ''
    installModal.value = { card, values }
    installError.value = ''
    return
  }
  await doInstall(card, {}, true)
}

async function confirmInstall(validate) {
  const { card, values } = installModal.value
  // 必填字段非空校验，缺什么提示什么
  const missing = card.fields.filter(f => f.required && !String(values[f.key] || '').trim())
  if (missing.length) {
    installError.value = `请先填写：${missing.map(f => f.label).join('、')}`
    return
  }
  await doInstall(card, values, validate)
}

async function doInstall(card, values, validate) {
  installing.value = card.key
  installError.value = ''
  try {
    const resp = await installConnector(card.key, values, validate)
    installModal.value = null
    await load()
    const failed = describeStatus(resp.server_status)
    if (failed) showNotice(`已添加「${card.name}」，但${failed}`, 'error')
    else showNotice(`已添加「${card.name}」`)
  } catch (e) {
    if (installModal.value) installError.value = e.message || '添加失败'
    else showNotice(e.message || '添加失败', 'error')
  } finally {
    installing.value = ''
  }
}

// ========== 已安装项配置修改（仅白名单字段） ==========
async function saveConfig() {
  if (!detail.value || savingConfig.value) return
  const card = detail.value
  const values = {}
  for (const f of card.fields) {
    const v = String(editValues[f.key] ?? '').trim()
    if (v) values[f.key] = v
  }
  if (Object.keys(values).length === 0) {
    detailError.value = '没有需要保存的修改'
    return
  }
  savingConfig.value = true
  detailError.value = ''
  try {
    const resp = await updateConnectorConfig(card.key, values)
    await load()
    const failed = describeStatus(resp.server_status)
    if (failed) showNotice(`配置已保存，但${failed}`, 'error')
    else showNotice(`「${card.name}」配置已保存`)
  } catch (e) {
    detailError.value = e.message || '保存失败'
  } finally {
    savingConfig.value = false
  }
}

function describeStatus(statuses) {
  const failed = (statuses || []).filter(s => s.status === 'error')
  if (failed.length === 0) return ''
  return `以下服务连接异常：${failed.map(s => s.name).join('、')}`
}

async function uninstall(card) {
  if (removing.value) return
  removing.value = card.key
  detailError.value = ''
  try {
    await uninstallConnector(card.key)
    detail.value = null
    await load()
    showNotice(`已移除「${card.name}」`)
  } catch (e) {
    detailError.value = e.message || '移除失败'
  } finally {
    removing.value = ''
  }
}

async function copySnippet() {
  const text = detailSnippet.value
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    const textarea = document.createElement('textarea')
    textarea.value = text
    document.body.appendChild(textarea)
    textarea.select()
    document.execCommand('copy')
    document.body.removeChild(textarea)
  }
  copied.value = true
  setTimeout(() => { copied.value = false }, 2000)
}

onMounted(load)
</script>

<style scoped>
.connector-panel {
  min-width: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.connector-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  padding: 16px 24px;
}

.connector-search {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  max-width: 320px;
  height: 36px;
  padding: 0 12px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: var(--bg-secondary);
}

.connector-search svg {
  width: 16px;
  height: 16px;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.connector-search input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  color: var(--text-primary);
  font-size: 14px;
}

.custom-connector-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
  height: 36px;
  padding: 0 16px;
  border: none;
  border-radius: 8px;
  background: var(--accent-color, #0ea5e9);
  color: #ffffff;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
}

.custom-connector-btn:hover {
  background: color-mix(in srgb, var(--accent-color, #0ea5e9) 86%, #000);
}

.custom-connector-btn:focus-visible {
  outline: 2px solid var(--accent-color, #0ea5e9);
  outline-offset: 2px;
}

.custom-connector-btn svg {
  width: 16px;
  height: 16px;
}

.installed-filter {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 36px;
  padding: 0 14px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: var(--bg-secondary);
  color: var(--text-secondary);
  font-size: 14px;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.2s;
}

.installed-filter:hover {
  background: var(--bg-tertiary);
  color: var(--text-primary);
}

.installed-filter.active {
  border-color: #0ea5e9;
  background: rgba(14, 165, 233, 0.1);
  color: #0ea5e9;
}

.installed-filter:focus-visible {
  outline: 2px solid #0ea5e9;
  outline-offset: 2px;
}

.installed-filter .filter-count {
  font-variant-numeric: tabular-nums;
}

.connector-notice {
  margin: 12px 24px 0;
  padding: 8px 12px;
  border-radius: 8px;
  font-size: 13px;
}

.connector-notice.success {
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  color: #16a34a;
}

.connector-notice.error {
  background: #fef2f2;
  border: 1px solid #fecaca;
  color: #dc2626;
}

.connector-content {
  flex: 1;
  overflow-y: auto;
  padding: 20px 24px;
}

.connector-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(300px, 100%), 1fr));
  gap: 12px;
}

.connector-card {
  border: 1px solid var(--border-color);
  border-radius: 12px;
  background: var(--bg-secondary);
  cursor: pointer;
  transition: border-color 0.2s, box-shadow 0.2s, transform 0.2s;
}

/* hover 描边染上连接器主题色（--tone-fg 由 tone-N 类注入） */
.connector-card:hover {
  border-color: color-mix(in srgb, var(--tone-fg, #0ea5e9) 45%, var(--border-color));
  box-shadow: 0 2px 10px color-mix(in srgb, var(--tone-fg, #0ea5e9) 14%, transparent);
}

.connector-card-inner {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px;
}

.connector-icon {
  width: 40px;
  height: 40px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 10px;
  background: #f0f9ff;
  color: #0ea5e9;
  font-size: 16px;
  font-weight: 600;
  transition: transform 0.2s;
}

.connector-card:hover .connector-icon {
  transform: scale(1.05);
}

.connector-icon svg {
  width: 22px;
  height: 22px;
}

/* 主题色调色板：图标块浅底深字，卡片 hover 描边取同色系（tone-fg） */
.connector-icon.tone-0 { background: #e0f2fe; color: #0284c7; --tone-fg: #0284c7; }
.connector-icon.tone-1 { background: #ede9fe; color: #7c3aed; --tone-fg: #7c3aed; }
.connector-icon.tone-2 { background: #fef3c7; color: #d97706; --tone-fg: #d97706; }
.connector-icon.tone-3 { background: #d1fae5; color: #059669; --tone-fg: #059669; }
.connector-icon.tone-4 { background: #fce7f3; color: #db2777; --tone-fg: #db2777; }
.connector-icon.tone-5 { background: #e0e7ff; color: #4f46e5; --tone-fg: #4f46e5; }
.connector-icon.tone-6 { background: #ffedd5; color: #ea580c; --tone-fg: #ea580c; }

.connector-icon.custom {
  background: #ede9fe;
  color: #7c3aed;
}

/* 暗色主题：底色降明度、字色提亮，保持对比度 */
html[data-theme="dark"] .connector-icon.tone-0 { background: #0c4a6e; color: #7dd3fc; --tone-fg: #7dd3fc; }
html[data-theme="dark"] .connector-icon.tone-1 { background: #2e1065; color: #c4b5fd; --tone-fg: #c4b5fd; }
html[data-theme="dark"] .connector-icon.tone-2 { background: #452c09; color: #fbbf24; --tone-fg: #fbbf24; }
html[data-theme="dark"] .connector-icon.tone-3 { background: #064e3b; color: #6ee7b7; --tone-fg: #6ee7b7; }
html[data-theme="dark"] .connector-icon.tone-4 { background: #4c0519; color: #f9a8d4; --tone-fg: #f9a8d4; }
html[data-theme="dark"] .connector-icon.tone-5 { background: #312e81; color: #a5b4fc; --tone-fg: #a5b4fc; }
html[data-theme="dark"] .connector-icon.tone-6 { background: #431407; color: #fdba74; --tone-fg: #fdba74; }

.connector-body {
  flex: 1;
  min-width: 0;
}

.connector-name-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.connector-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.connector-tag {
  flex-shrink: 0;
  padding: 1px 6px;
  border-radius: 6px;
  background: #ede9fe;
  color: #7c3aed;
  font-size: 11px;
}

.connector-tag.installed {
  background: #dcfce7;
  color: #16a34a;
}

.connector-desc {
  margin-top: 2px;
  font-size: 12px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.connector-add-btn {
  width: 30px;
  height: 30px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.2s;
}

.connector-add-btn:hover:not(:disabled) {
  border-color: #0ea5e9;
  color: #0ea5e9;
}

.connector-add-btn:disabled {
  cursor: not-allowed;
}

.connector-add-btn.added {
  border-color: #bbf7d0;
  color: #16a34a;
}

.connector-add-btn svg {
  width: 16px;
  height: 16px;
}

.btn-spinner-sm {
  width: 14px;
  height: 14px;
  border: 2px solid #e2e8f0;
  border-top-color: #0ea5e9;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.loading-state,
.error-state,
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 60px 20px;
  color: var(--text-secondary);
  font-size: 14px;
}

.empty-state svg {
  width: 48px;
  height: 48px;
  opacity: 0.5;
}

.empty-state p,
.error-state p {
  margin: 0;
}

.spinner {
  width: 40px;
  height: 40px;
  border: 3px solid #e2e8f0;
  border-top-color: #0ea5e9;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

.retry-btn {
  padding: 8px 20px;
  border: 1px solid var(--border-color);
  background: var(--bg-secondary);
  border-radius: 8px;
  font-size: 14px;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.2s;
}

.retry-btn:hover {
  background: var(--bg-tertiary);
}

/* 详情弹层 */
.connector-detail-overlay {
  position: fixed;
  inset: 0;
  z-index: 3000;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(15, 23, 42, 0.45);
}

.connector-detail-card {
  width: min(560px, 92vw);
  max-height: 80vh;
  display: flex;
  flex-direction: column;
  border-radius: 12px;
  background: var(--bg-primary);
  box-shadow: 0 20px 40px rgba(15, 23, 42, 0.25);
  overflow: hidden;
}

.connector-detail-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px 20px;
  border-bottom: 1px solid #e2e8f0;
}

.connector-detail-title {
  flex: 1;
  min-width: 0;
}

.connector-detail-name {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.connector-detail-sub {
  margin-top: 2px;
  font-size: 12px;
  color: var(--text-secondary);
}

.detail-close-btn {
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
}

.detail-close-btn:hover {
  background: var(--bg-tertiary);
}

.detail-close-btn svg {
  width: 16px;
  height: 16px;
}

.connector-detail-body {
  padding: 16px 20px;
  overflow-y: auto;
}

.connector-detail-desc {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.7;
  white-space: pre-wrap;
}

.connector-detail-label {
  margin-bottom: 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
}

.connector-snippet {
  margin: 0;
  padding: 12px;
  max-height: 240px;
  overflow: auto;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: var(--bg-secondary);
  color: var(--text-primary);
  font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre;
}

.connector-detail-error {
  margin: 10px 0 0;
  font-size: 12px;
  color: #dc2626;
}

/* ========== 连接状态徽标 ========== */
.conn-status {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
  font-size: 11px;
  line-height: 1;
  color: var(--text-secondary);
}

.conn-status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--text-secondary);
}

.conn-status.ok { color: #059669; }
.conn-status.ok .conn-status-dot { background: #10b981; }

.conn-status.error { color: #dc2626; }
.conn-status.error .conn-status-dot { background: #ef4444; }

.conn-status.disabled { color: var(--text-secondary); opacity: 0.7; }

.conn-status.checking .conn-status-dot {
  animation: conn-status-pulse 1.2s ease-in-out infinite;
}

@keyframes conn-status-pulse {
  0%, 100% { opacity: 0.35; }
  50% { opacity: 1; }
}

html[data-theme="dark"] .conn-status.ok { color: #6ee7b7; }
html[data-theme="dark"] .conn-status.error { color: #fca5a5; }

.detail-status-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
  padding: 8px 10px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: var(--bg-tertiary);
}

.detail-status-tools {
  font-size: 12px;
  color: var(--text-secondary);
}

.detail-status-error {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  color: #dc2626;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ========== 启用开关 / 工具列表 / 字段编辑 / 安装弹窗 ========== */
.connector-card .toggle-switch {
  margin-left: 8px;
}

.connector-detail-header .toggle-switch {
  margin-left: auto;
}

.tools-load-btn {
  margin-left: 8px;
  padding: 1px 8px;
  border: 1px solid var(--border-color);
  border-radius: 6px;
  background: transparent;
  color: var(--accent-color);
  font-size: 11px;
  cursor: pointer;
}

.tools-load-btn:hover:not(:disabled) {
  background: var(--bg-tertiary);
}

.tools-state {
  padding: 12px 0;
}

.tools-list {
  margin: 8px 0 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 180px;
  overflow-y: auto;
  scrollbar-width: thin;
}

.tools-item {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: 7px 10px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: var(--bg-tertiary);
}

.tools-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  font-family: 'SF Mono', 'Fira Code', monospace;
}

.tools-desc {
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-secondary);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.tools-empty {
  padding: 8px 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.fields-grid {
  margin: 8px 0 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.fields-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.fields-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--text-secondary);
}

.fields-required {
  color: #ef4444;
  margin-left: 2px;
}

.fields-item input {
  height: 34px;
  padding: 0 10px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: var(--bg-secondary);
  color: var(--text-primary);
  font-size: 13px;
  outline: none;
  transition: border-color 0.15s;
}

.fields-item input:focus {
  border-color: color-mix(in srgb, var(--accent-color) 55%, transparent);
}

.install-modal {
  width: 440px;
  max-width: 92vw;
}

.action-btn-primary {
  height: 34px;
  padding: 0 16px;
  border: 1px solid var(--accent-color);
  border-radius: 8px;
  background: var(--accent-color);
  color: #fff;
  font-size: 13px;
  cursor: pointer;
  transition: opacity 0.2s;
}

.action-btn-primary:hover:not(:disabled) {
  opacity: 0.88;
}

.action-btn-primary:disabled,
.action-btn-outline:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.connector-detail-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 0 20px 20px;
}

.action-btn-outline,
.action-btn-danger {
  height: 34px;
  padding: 0 16px;
  border-radius: 8px;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
}

.action-btn-outline {
  border: 1px solid var(--border-color);
  background: transparent;
  color: var(--text-primary);
}

.action-btn-outline:hover {
  background: var(--bg-tertiary);
}

.action-btn-danger {
  border: 1px solid #fca5a5;
  background: transparent;
  color: #ef4444;
}

.action-btn-danger:hover:not(:disabled) {
  background: #fef2f2;
}

.action-btn-danger:disabled {
  cursor: not-allowed;
}

/* 深色主题 */
html[data-theme="dark"] .connector-card,
html[data-theme="dark"] .connector-detail-card {
  background: var(--bg-secondary);
  border-color: var(--border-color);
}

html[data-theme="dark"] .connector-search,
html[data-theme="dark"] .connector-snippet {
  background: var(--bg-tertiary);
  border-color: var(--border-color);
  color: var(--text-primary);
}

html[data-theme="dark"] .custom-connector-btn {
  background: var(--accent-color, #0ea5e9);
  border: none;
  color: #ffffff;
}

html[data-theme="dark"] .connector-search input {
  color: var(--text-primary);
}

html[data-theme="dark"] .installed-filter {
  background: var(--bg-tertiary);
  border-color: var(--border-color);
  color: var(--text-secondary);
}

html[data-theme="dark"] .installed-filter.active {
  background: rgba(14, 165, 233, 0.18);
  color: #7dd3fc;
}

html[data-theme="dark"] .connector-notice.success {
  background: rgba(22, 163, 74, 0.15);
  border-color: rgba(22, 163, 74, 0.35);
  color: #86efac;
}

html[data-theme="dark"] .connector-notice.error {
  background: rgba(239, 68, 68, 0.15);
  border-color: rgba(239, 68, 68, 0.35);
  color: #fca5a5;
}

html[data-theme="dark"] .connector-tag {
  background: rgba(124, 58, 237, 0.2);
  color: #c4b5fd;
}

html[data-theme="dark"] .connector-tag.installed {
  background: rgba(22, 163, 74, 0.2);
  color: #86efac;
}

html[data-theme="dark"] .spinner {
  border-color: var(--border-color);
  border-top-color: var(--accent-color);
}

html[data-theme="dark"] .retry-btn,
html[data-theme="dark"] .action-btn-outline {
  background: var(--bg-tertiary);
  border-color: var(--border-color);
  color: var(--text-primary);
}
</style>
