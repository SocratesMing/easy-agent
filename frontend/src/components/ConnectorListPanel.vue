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
        <p>{{ keyword ? '没有匹配的连接器' : '暂无连接器，点右上角「自定义连接器」添加 MCP 服务' }}</p>
      </div>

      <div v-else class="connector-grid">
        <div
          v-for="card in cards"
          :key="card.key"
          class="connector-card"
          @click="openDetail(card)"
        >
          <div class="connector-card-inner">
            <div class="connector-icon" :class="{ custom: card.custom }">{{ card.initial }}</div>
            <div class="connector-body">
              <div class="connector-name-row">
                <span class="connector-name">{{ card.name }}</span>
                <span v-if="card.custom" class="connector-tag">自定义</span>
                <span v-else-if="card.installed" class="connector-tag installed">已添加</span>
              </div>
              <div class="connector-desc">{{ card.description || '暂无描述' }}</div>
            </div>
            <button
              class="connector-add-btn"
              :class="{ added: card.installed }"
              :disabled="card.installed || installing === card.key"
              :title="card.installed ? '已添加' : '添加'"
              @click.stop="install(card)"
            >
              <div v-if="installing === card.key" class="btn-spinner-sm"></div>
              <svg v-else-if="card.installed" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                <polyline points="20 6 9 17 4 12"></polyline>
              </svg>
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
      <div v-if="detail" class="connector-detail-overlay" @click.self="detail = null">
        <div class="connector-detail-card" @click.stop>
          <div class="connector-detail-header">
            <div class="connector-icon" :class="{ custom: detail.custom }">{{ detail.initial }}</div>
            <div class="connector-detail-title">
              <div class="connector-detail-name">
                {{ detail.name }}
                <span v-if="detail.custom" class="connector-tag">自定义</span>
              </div>
              <div class="connector-detail-sub">{{ detail.custom ? '用户自定义 MCP 服务' : '内置连接器' }}</div>
            </div>
            <button class="detail-close-btn" title="关闭" @click="detail = null">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <line x1="18" y1="6" x2="6" y2="18"></line>
                <line x1="6" y1="6" x2="18" y2="18"></line>
              </svg>
            </button>
          </div>

          <div class="connector-detail-body">
            <p class="connector-detail-desc">{{ detail.description || '暂无描述' }}</p>
            <div class="connector-detail-label">mcp.json 配置</div>
            <pre class="connector-snippet">{{ detailSnippet }}</pre>
            <p v-if="detailError" class="connector-detail-error">{{ detailError }}</p>
          </div>

          <div class="connector-detail-footer">
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
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import {
  getConnectors,
  installConnector,
  uninstallConnector,
} from '../api/connectors.js'

defineEmits(['manage', 'add'])

const loading = ref(false)
const error = ref('')
const connectors = ref([])
const customServers = ref([])
const keyword = ref('')

const installing = ref('')
const removing = ref('')
const notice = ref('')
const noticeType = ref('success')
const detail = ref(null)
const detailError = ref('')
const copied = ref(false)

function showNotice(message, type = 'success') {
  notice.value = message
  noticeType.value = type
  setTimeout(() => { notice.value = '' }, 3000)
}

// 卡片：内置目录项 + 用户 mcp.json 里的自定义项（后者按 mcp.json 的 key 命名）
const cards = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  const match = (text) => !kw || String(text || '').toLowerCase().includes(kw)

  const fromCatalog = connectors.value
    .filter(c => match(c.name) || match(c.id) || match(c.description))
    .map(c => ({
      key: c.id,
      name: c.name,
      description: c.description,
      icon: c.icon,
      config: c.config,
      installed: !!c.installed,
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
      installed: true,
      custom: true,
      initial: c.name.slice(0, 1).toUpperCase(),
    }))

  return [...fromCatalog, ...fromUser]
})

const detailSnippet = computed(() => {
  if (!detail.value) return ''
  return JSON.stringify(
    { servers: { [detail.value.key]: detail.value.config } },
    null,
    2,
  )
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await getConnectors()
    connectors.value = data.connectors || []
    customServers.value = data.custom || []
  } catch (e) {
    error.value = e.message || '加载连接器失败'
  } finally {
    loading.value = false
  }
}

function openDetail(card) {
  detail.value = card
  detailError.value = ''
  copied.value = false
}

function describeStatus(statuses) {
  const failed = (statuses || []).filter(s => s.status === 'error')
  if (failed.length === 0) return ''
  return `以下服务连接异常：${failed.map(s => s.name).join('、')}`
}

async function install(card) {
  if (card.installed || installing.value) return
  installing.value = card.key
  try {
    const resp = await installConnector(card.key)
    await load()
    const failed = describeStatus(resp.server_status)
    if (failed) showNotice(`已添加「${card.name}」，但${failed}`, 'error')
    else showNotice(`已添加「${card.name}」`)
  } catch (e) {
    showNotice(e.message || '添加失败', 'error')
  } finally {
    installing.value = ''
  }
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
  justify-content: space-between;
  gap: 12px;
  padding: 16px 24px;
  border-bottom: 1px solid #e2e8f0;
}

.connector-search {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  max-width: 360px;
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
  height: 36px;
  padding: 0 16px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: var(--bg-secondary);
  color: var(--text-primary);
  font-size: 14px;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
}

.custom-connector-btn:hover {
  background: var(--bg-tertiary);
  border-color: #cbd5e1;
}

.custom-connector-btn svg {
  width: 16px;
  height: 16px;
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
  border-radius: 10px;
  background: var(--bg-secondary);
  cursor: pointer;
  transition: all 0.2s;
}

.connector-card:hover {
  border-color: #0ea5e9;
  box-shadow: 0 2px 8px rgba(14, 165, 233, 0.12);
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
}

.connector-icon.custom {
  background: #ede9fe;
  color: #7c3aed;
}

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
html[data-theme="dark"] .custom-connector-btn,
html[data-theme="dark"] .connector-snippet {
  background: var(--bg-tertiary);
  border-color: var(--border-color);
  color: var(--text-primary);
}

html[data-theme="dark"] .connector-search input {
  color: var(--text-primary);
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
