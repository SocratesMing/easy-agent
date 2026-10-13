<!--
  添加 MCP 服务：整页粘贴 JSON。支持标准 mcpServers / 对象 / 单条 server / servers 四种格式，
  由后端 POST /agent/connectors/mcp 解析入库。
-->
<template>
  <div class="mcp-add-panel">
    <div class="mcp-view-header">
      <div>
        <h3>添加 MCP 服务</h3>
        <p class="mcp-view-desc">粘贴 MCP 服务配置（JSON），确认后立即生效</p>
      </div>
    </div>

    <div class="mcp-editor-toolbar">
      <button class="back-btn" @click="$emit('back')">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <line x1="19" y1="12" x2="5" y2="12"></line>
          <polyline points="12 19 5 12 12 5"></polyline>
        </svg>
        返回 MCP 列表
      </button>
      <div class="mcp-editor-actions">
        <button class="action-btn-outline" @click="$emit('back')">取消</button>
        <button class="action-btn-primary" :disabled="submitting" @click="confirmAdd">
          {{ submitting ? '添加中...' : '确认添加' }}
        </button>
      </div>
    </div>

    <div class="mcp-add-body">
      <p class="mcp-add-hint">支持以下格式（以 servers 下的名称识别服务名）：</p>
      <pre class="mcp-add-example">标准格式：{"mcpServers": {"akshare-mcp": {"type":"sse","url":"http://127.0.0.1:8005/sse","timeout":60}}}
对象格式：{"myserver": {"transport":"stdio","command":"npx",...}}
单条格式：{"name":"myserver","transport":"stdio","command":"npx",...}</pre>
      <textarea
        v-model="json"
        class="mcp-add-input"
        placeholder='{"mcpServers": {"akshare-mcp": {"type": "sse", "url": "http://127.0.0.1:8005/sse", "timeout": 60}}}'
        spellcheck="false"
        aria-label="MCP 服务配置 JSON"
      ></textarea>
      <p v-if="error" class="mcp-add-error">{{ error }}</p>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { addMcpConfig } from '../api/connectors.js'

const emit = defineEmits(['back'])

const json = ref('')
const error = ref('')
const submitting = ref(false)

async function confirmAdd() {
  error.value = ''
  let parsed
  try {
    parsed = JSON.parse(json.value)
  } catch {
    error.value = 'JSON 格式无效，请检查输入'
    return
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    error.value = '请输入 JSON 对象，格式如 {"servers": {"myserver": {...}}}'
    return
  }

  submitting.value = true
  try {
    const resp = await addMcpConfig(parsed)
    const failed = (resp.server_status || []).filter(s => s.status === 'error')
    if (failed.length > 0) {
      const names = failed.map(s => s.name).join('、')
      // 已落盘但连接异常：留在本页提示，避免用户误以为没添加成功
      error.value = `以下 MCP 服务添加成功但连接异常：${names}（请检查配置）`
      return
    }
    emit('back')
  } catch (e) {
    error.value = e.message || '添加失败'
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.mcp-add-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.mcp-view-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 16px 24px;
}

.mcp-view-header h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.mcp-view-desc {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.mcp-editor-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 24px 12px;
}

.back-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--text-secondary);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
}

.back-btn:hover {
  background: var(--bg-tertiary);
  color: var(--text-primary);
}

.back-btn svg {
  width: 14px;
  height: 14px;
}

.mcp-editor-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.mcp-add-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: 0 24px 20px;
}

.mcp-add-hint {
  margin: 0 0 8px;
  font-size: 12px;
  color: var(--text-secondary);
}

.mcp-add-example {
  margin: 0 0 12px;
  padding: 12px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: var(--bg-tertiary);
  color: var(--text-secondary);
  font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
  font-size: 12px;
  line-height: 1.6;
  overflow-x: auto;
  white-space: pre;
}

.mcp-add-input {
  flex: 1;
  min-height: 200px;
  padding: 12px;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: var(--bg-secondary);
  color: var(--text-primary);
  font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
  font-size: 13px;
  line-height: 1.6;
  resize: none;
  outline: none;
}

.mcp-add-input:focus {
  border-color: #0ea5e9;
}

.mcp-add-error {
  margin: 10px 0 0;
  padding: 8px 12px;
  border: 1px solid #fecaca;
  border-radius: 8px;
  background: #fef2f2;
  color: #dc2626;
  font-size: 12px;
}

.action-btn-primary,
.action-btn-outline {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 34px;
  padding: 0 16px;
  border-radius: 8px;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
}

.action-btn-primary {
  border: 1px solid #0ea5e9;
  background: #0ea5e9;
  color: white;
}

.action-btn-primary:hover:not(:disabled) {
  background: #0284c7;
  border-color: #0284c7;
}

.action-btn-primary:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.action-btn-outline {
  border: 1px solid var(--border-color);
  background: transparent;
  color: var(--text-primary);
}

.action-btn-outline:hover {
  background: var(--bg-tertiary);
}

html[data-theme="dark"] .mcp-add-example {
  background: var(--bg-tertiary);
  border-color: var(--border-color);
}

html[data-theme="dark"] .mcp-add-input {
  background: var(--bg-tertiary);
  color: var(--text-primary);
}

html[data-theme="dark"] .mcp-add-error {
  background: rgba(239, 68, 68, 0.15);
  border-color: rgba(239, 68, 68, 0.35);
  color: #fca5a5;
}

html[data-theme="dark"] .action-btn-outline {
  background: var(--bg-tertiary);
  border-color: var(--border-color);
  color: var(--text-primary);
}
</style>
