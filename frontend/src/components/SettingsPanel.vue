<template>
  <div class="settings-overlay" @click="$emit('close')">
    <div class="settings-modal" @click.stop>
      <div class="settings-header">
        <h2>设置</h2>
        <button @click="$emit('close')" class="close-btn">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="18" y1="6" x2="6" y2="18"></line>
            <line x1="6" y1="6" x2="18" y2="18"></line>
          </svg>
        </button>
      </div>

      <div class="settings-body">
        <div class="settings-nav">
          <div
            v-for="item in navItems"
            :key="item.key"
            class="nav-item"
            :class="{ active: activeTab === item.key }"
            @click="activeTab = item.key"
          >
            <span class="nav-icon" v-html="item.icon"></span>
            <span class="nav-label">{{ item.label }}</span>
          </div>
        </div>

        <div class="settings-content">
          <!-- 记忆 -->
          <div v-if="activeTab === 'memory'" class="content-panel">
            <div class="panel-header">
              <h3>记忆</h3>
              <p class="panel-desc">当前用户的长期记忆文件，支持 Markdown 格式编辑</p>
            </div>
            <div v-if="loading" class="loading-state"><div class="spinner"></div></div>
            <div v-else class="memory-editor">
              <textarea
                v-model="memoryContent"
                class="markdown-editor"
                placeholder="在此编辑记忆内容..."
                spellcheck="false"
              ></textarea>
              <div class="editor-actions">
                <span v-if="memorySaved" class="save-hint">已保存</span>
                <span v-if="memoryError" class="save-error">{{ memoryError }}</span>
                <button class="save-btn" @click="saveMemory" :disabled="memorySaving">
                  {{ memorySaving ? '保存中...' : '保存' }}
                </button>
              </div>
            </div>
          </div>

          <!-- 提示词 -->
          <div v-if="activeTab === 'prompt'" class="content-panel">
            <div class="panel-header">
              <h3>系统提示词</h3>
              <p class="panel-desc">当前使用的系统提示词（只读）</p>
            </div>
            <div v-if="loading" class="loading-state"><div class="spinner"></div></div>
            <div v-else class="prompt-viewer">
              <pre class="prompt-content">{{ promptContent }}</pre>
            </div>
          </div>

          <!-- MCP -->
          <div v-if="activeTab === 'mcp'" class="content-panel">
            <div class="panel-header">
              <h3>MCP</h3>
              <p class="panel-desc">当前系统配置的 MCP 服务</p>
            </div>
            <div v-if="loading" class="loading-state"><div class="spinner"></div></div>
            <div v-else class="mcp-list">
              <div v-if="mcpServers.length === 0" class="empty-hint">暂无 MCP 服务配置</div>
              <div v-for="server in mcpServers" :key="server.name" class="mcp-card">
                <div class="mcp-header">
                  <span class="mcp-name">{{ server.name }}</span>
                  <span class="mcp-transport">{{ server.transport }}</span>
                </div>
                <div v-if="server.command" class="mcp-detail">
                  <span class="detail-label">Command:</span>
                  <code>{{ server.command }} {{ (server.args || []).join(' ') }}</code>
                </div>
                <div v-if="server.env_keys && server.env_keys.length" class="mcp-detail">
                  <span class="detail-label">Env:</span>
                  <code>{{ server.env_keys.join(', ') }}</code>
                </div>
              </div>
            </div>
          </div>

          <!-- 外观 -->
          <div v-if="activeTab === 'appearance'" class="content-panel">
            <div class="panel-header">
              <h3>外观</h3>
              <p class="panel-desc">切换界面的显示主题</p>
            </div>
            <div class="appearance-options">
              <div
                class="theme-option"
                :class="{ active: !isDarkTheme }"
                @click="switchTheme(false)"
              >
                <div class="theme-preview theme-preview-light">
                  <div class="preview-bar"></div>
                  <div class="preview-line"></div>
                  <div class="preview-line short"></div>
                </div>
                <div class="theme-option-info">
                  <span class="theme-option-name">浅色主题</span>
                  <span class="theme-option-desc">明亮清新，适合白天使用</span>
                </div>
                <svg v-if="!isDarkTheme" class="theme-check" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
                  <polyline points="22 4 12 14.01 9 11.01"></polyline>
                </svg>
              </div>
              <div
                class="theme-option"
                :class="{ active: isDarkTheme }"
                @click="switchTheme(true)"
              >
                <div class="theme-preview theme-preview-dark">
                  <div class="preview-bar"></div>
                  <div class="preview-line"></div>
                  <div class="preview-line short"></div>
                </div>
                <div class="theme-option-info">
                  <span class="theme-option-name">深色主题</span>
                  <span class="theme-option-desc">柔和护眼，适合夜间使用</span>
                </div>
                <svg v-if="isDarkTheme" class="theme-check" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
                  <polyline points="22 4 12 14.01 9 11.01"></polyline>
                </svg>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted } from 'vue'
import { getMemory, updateMemory, getSystemPrompt, getMcpServers } from '../api/settings.js'

const props = defineProps({
  isDarkTheme: { type: Boolean, default: false },
})

const emit = defineEmits(['close', 'toggle-theme'])

const activeTab = ref('memory')
const loading = ref(false)

// 记忆
const memoryContent = ref('')
const memorySaving = ref(false)
const memorySaved = ref(false)
const memoryError = ref('')

// 提示词
const promptContent = ref('')

// MCP
const mcpServers = ref([])

function switchTheme(dark) {
  if (dark === props.isDarkTheme) return
  emit('toggle-theme')
}

const navItems = [
  {
    key: 'memory',
    label: '记忆',
    icon: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a7 7 0 0 0-7 7c0 2.38 1.19 4.47 3 5.74V17a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1v-2.26c1.81-1.27 3-3.36 3-5.74a7 7 0 0 0-7-7z"/><line x1="9" y1="21" x2="15" y2="21"/></svg>',
  },
  {
    key: 'prompt',
    label: '提示词',
    icon: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>',
  },
  {
    key: 'mcp',
    label: 'MCP',
    icon: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="2" width="20" height="8" rx="2" ry="2"/><rect x="2" y="14" width="20" height="8" rx="2" ry="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/></svg>',
  },
  {
    key: 'appearance',
    label: '外观',
    icon: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><line x1="2" y1="12" x2="22" y2="12"/></svg>',
  },
]

async function loadTabData() {
  loading.value = true
  try {
    if (activeTab.value === 'memory') {
      const data = await getMemory()
      memoryContent.value = data.content || ''
    } else if (activeTab.value === 'prompt') {
      const data = await getSystemPrompt()
      promptContent.value = data.content || ''
    } else if (activeTab.value === 'mcp') {
      const data = await getMcpServers()
      mcpServers.value = data.servers || []
    }
  } catch (e) {
    console.error('加载数据失败:', e)
  } finally {
    loading.value = false
  }
}

async function saveMemory() {
  memorySaving.value = true
  memorySaved.value = false
  memoryError.value = ''
  try {
    await updateMemory(memoryContent.value)
    memorySaved.value = true
    setTimeout(() => { memorySaved.value = false }, 2000)
  } catch (e) {
    memoryError.value = e.message || '保存失败'
  } finally {
    memorySaving.value = false
  }
}

watch(activeTab, () => {
  loadTabData()
})

onMounted(() => {
  loadTabData()
})
</script>

<style scoped>
.settings-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.settings-modal {
  background: white;
  border-radius: 16px;
  width: 800px;
  max-width: 90vw;
  height: 600px;
  max-height: 85vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
  overflow: hidden;
}

.settings-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 24px;
  border-bottom: 1px solid #e2e8f0;
  flex-shrink: 0;
}

.settings-header h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: #1e293b;
}

.close-btn {
  width: 32px;
  height: 32px;
  border: none;
  background: transparent;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.2s;
}

.close-btn:hover {
  background: #f1f5f9;
}

.close-btn svg {
  width: 20px;
  height: 20px;
  color: #64748b;
}

.settings-body {
  flex: 1;
  display: flex;
  overflow: hidden;
}

.settings-nav {
  width: 180px;
  border-right: 1px solid #e2e8f0;
  padding: 12px 8px;
  flex-shrink: 0;
  overflow-y: auto;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s;
  color: #475569;
  font-size: 14px;
  font-weight: 500;
}

.nav-item:hover {
  background: #f1f5f9;
  color: #1e293b;
}

.nav-item.active {
  background: #e0f2fe;
  color: #0ea5e9;
}

.nav-icon {
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.nav-icon :deep(svg) {
  width: 20px;
  height: 20px;
}

.settings-content {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
}

.content-panel {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.panel-header {
  margin-bottom: 16px;
  flex-shrink: 0;
}

.panel-header h3 {
  margin: 0 0 4px 0;
  font-size: 16px;
  font-weight: 600;
  color: #1e293b;
}

.panel-desc {
  margin: 0;
  font-size: 13px;
  color: #94a3b8;
}

.loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px;
}

.spinner {
  width: 28px;
  height: 28px;
  border: 3px solid #e2e8f0;
  border-top-color: #0ea5e9;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* 记忆编辑器 */
.memory-editor {
  flex: 1;
  display: flex;
  flex-direction: column;
}

.markdown-editor {
  flex: 1;
  padding: 16px;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
  font-size: 13px;
  line-height: 1.6;
  color: #1e293b;
  resize: none;
  outline: none;
  transition: border-color 0.2s;
  min-height: 300px;
}

.markdown-editor:focus {
  border-color: #0ea5e9;
  box-shadow: 0 0 0 3px rgba(14, 165, 233, 0.1);
}

.editor-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 12px;
}

.save-hint {
  font-size: 13px;
  color: #22c55e;
}

.save-error {
  font-size: 13px;
  color: #ef4444;
}

.save-btn {
  padding: 8px 20px;
  border: none;
  background: #0ea5e9;
  color: white;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
}

.save-btn:hover:not(:disabled) {
  background: #0284c7;
}

.save-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

/* 提示词查看器 */
.prompt-viewer {
  flex: 1;
}

.prompt-content {
  padding: 16px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
  font-size: 13px;
  line-height: 1.6;
  color: #334155;
  white-space: pre-wrap;
  word-wrap: break-word;
  margin: 0;
  max-height: 450px;
  overflow-y: auto;
}

/* MCP 列表 */
.mcp-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.mcp-card {
  padding: 16px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
}

.mcp-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}

.mcp-name {
  font-size: 15px;
  font-weight: 600;
  color: #1e293b;
}

.mcp-transport {
  font-size: 12px;
  padding: 2px 8px;
  background: #e0f2fe;
  color: #0ea5e9;
  border-radius: 4px;
  font-weight: 500;
}

.mcp-detail {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-top: 6px;
  font-size: 13px;
}

.detail-label {
  color: #94a3b8;
  font-weight: 500;
  flex-shrink: 0;
}

.mcp-detail code {
  color: #475569;
  background: #f1f5f9;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 12px;
  word-break: break-all;
}

.empty-hint {
  text-align: center;
  color: #94a3b8;
  padding: 40px 0;
  font-size: 14px;
}

/* 外观 - 主题切换 */
.appearance-options {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.theme-option {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 16px;
  background: #f8fafc;
  border: 2px solid #e2e8f0;
  border-radius: 10px;
  cursor: pointer;
  transition: all 0.2s;
}

.theme-option:hover {
  border-color: #cbd5e1;
  background: #f1f5f9;
}

.theme-option.active {
  border-color: #0ea5e9;
  background: #e0f2fe;
}

.theme-preview {
  width: 80px;
  height: 56px;
  border-radius: 6px;
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex-shrink: 0;
  border: 1px solid #e2e8f0;
}

.theme-preview-light {
  background: #ffffff;
}

.theme-preview-dark {
  background: #1a1a2e;
}

.theme-preview .preview-bar {
  height: 8px;
  border-radius: 3px;
  background: #0ea5e9;
  width: 60%;
}

.theme-preview-dark .preview-bar {
  background: #7c6aef;
}

.theme-preview .preview-line {
  height: 4px;
  border-radius: 2px;
  background: #cbd5e1;
  width: 100%;
}

.theme-preview-dark .preview-line {
  background: #475569;
}

.theme-preview .preview-line.short {
  width: 60%;
}

.theme-option-info {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.theme-option-name {
  font-size: 14px;
  font-weight: 600;
  color: #1e293b;
}

.theme-option-desc {
  font-size: 12px;
  color: #94a3b8;
}

.theme-check {
  width: 20px;
  height: 20px;
  color: #0ea5e9;
  flex-shrink: 0;
}
</style>
