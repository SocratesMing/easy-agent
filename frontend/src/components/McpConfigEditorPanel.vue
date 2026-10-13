<!--
  MCP 服务管理：直接编辑当前用户的 mcp.json 原文（Monaco，带行号与高亮）。
  保存走后端 PUT /agent/connectors/mcp/raw，保存后会重新拉取归一化后的原文。
-->
<template>
  <div class="mcp-editor-panel">
    <div class="mcp-view-header">
      <div>
        <h3>MCP 服务管理</h3>
        <p class="mcp-view-desc">安装 MCP 服务，为 AI 扩展更多工具能力</p>
      </div>
      <button class="action-btn-primary" @click="$emit('add')">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
          <line x1="12" y1="5" x2="12" y2="19"></line>
          <line x1="5" y1="12" x2="19" y2="12"></line>
        </svg>
        添加 MCP
      </button>
    </div>

    <div class="mcp-editor-toolbar">
      <button class="back-btn" @click="handleCancel">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <line x1="19" y1="12" x2="5" y2="12"></line>
          <polyline points="12 19 5 12 12 5"></polyline>
        </svg>
        返回 MCP 列表
      </button>
      <div class="mcp-editor-actions">
        <span v-if="message" class="mcp-message" :class="messageType">{{ message }}</span>
        <button class="action-btn-outline" @click="handleCancel">取消</button>
        <button class="action-btn-primary" :disabled="saving" @click="handleSave">
          {{ saving ? '保存中...' : '保存' }}
        </button>
      </div>
    </div>

    <div class="mcp-path-bar">
      配置文件路径：<code>{{ path || '加载中...' }}</code>
    </div>

    <div class="mcp-editor-wrap">
      <div v-show="!useFallback" ref="editorRef" class="mcp-editor"></div>
      <textarea
        v-if="useFallback"
        v-model="content"
        class="mcp-editor-fallback"
        spellcheck="false"
        aria-label="mcp.json 内容"
      ></textarea>
    </div>

    <p v-if="error" class="mcp-editor-error">{{ error }}</p>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, nextTick, shallowRef } from 'vue'
import * as monaco from 'monaco-editor'
import { getMcpRaw, saveMcpRaw } from '../api/connectors.js'

const emit = defineEmits(['back', 'add'])

const editorRef = ref(null)
const editor = shallowRef(null)
const content = ref('')
const path = ref('')
const saving = ref(false)
const loading = ref(false)
const error = ref('')
const message = ref('')
const messageType = ref('success')
const useFallback = ref(false)
const dirty = ref(false)

function showMessage(text, type = 'success') {
  message.value = text
  messageType.value = type
  setTimeout(() => { message.value = '' }, 3000)
}

function currentTheme() {
  return document.documentElement.getAttribute('data-theme') === 'dark'
    ? 'vs-dark'
    : 'vs'
}

function setContent(value) {
  content.value = value
  if (editor.value) {
    const model = editor.value.getModel()
    if (model) model.setValue(value)
  }
}

function initEditor() {
  if (!editorRef.value) return
  try {
    editor.value = monaco.editor.create(editorRef.value, {
      value: content.value,
      language: 'json',
      theme: currentTheme(),
      readOnly: false,
      automaticLayout: true,
      minimap: { enabled: false },
      scrollBeyondLastLine: false,
      fontSize: 13,
      lineNumbers: 'on',
      renderLineHighlight: 'line',
      contextmenu: false,
      folding: true,
      glyphMargin: false,
      lineDecorationsWidth: 10,
      lineNumbersMinChars: 3,
      tabSize: 2,
    })
    editor.value.onDidChangeModelContent(() => { dirty.value = true })
  } catch (e) {
    // Monaco 起不来时降级为原生 textarea，功能不丢
    console.error('Monaco 初始化失败，降级为 textarea:', e)
    useFallback.value = true
    editor.value = null
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await getMcpRaw()
    path.value = data.path || ''
    setContent(data.content || '')
    dirty.value = false
  } catch (e) {
    error.value = e.message || '读取 mcp.json 失败'
  } finally {
    loading.value = false
  }
}

function collectContent() {
  if (useFallback.value || !editor.value) return content.value
  return editor.value.getValue()
}

function describeStatus(statuses) {
  const failed = (statuses || []).filter(s => s.status === 'error')
  if (failed.length === 0) return ''
  return `以下服务连接异常：${failed.map(s => s.name).join('、')}`
}

async function handleSave() {
  saving.value = true
  error.value = ''
  try {
    const resp = await saveMcpRaw(collectContent())
    await load()
    const failed = describeStatus(resp.server_status)
    if (failed) showMessage(`已保存，但${failed}`, 'error')
    else showMessage('已保存，配置将在下次对话生效')
  } catch (e) {
    error.value = e.message || '保存失败'
  } finally {
    saving.value = false
  }
}

function handleCancel() {
  if (dirty.value && !confirm('放弃未保存的修改？')) return
  emit('back')
}

onMounted(async () => {
  await load()
  await nextTick()
  initEditor()
})

onBeforeUnmount(() => {
  if (editor.value) {
    editor.value.dispose()
    editor.value = null
  }
})
</script>

<style scoped>
.mcp-editor-panel {
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

.mcp-message {
  font-size: 12px;
  color: #16a34a;
  max-width: 320px;
}

.mcp-message.error {
  color: #dc2626;
}

.mcp-path-bar {
  margin: 0 24px 8px;
  padding: 8px 12px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: var(--bg-tertiary);
  color: var(--text-secondary);
  font-size: 12px;
  word-break: break-all;
}

.mcp-path-bar code {
  color: var(--text-primary);
  font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
}

.mcp-editor-wrap {
  flex: 1;
  min-height: 240px;
  margin: 0 24px 16px;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  overflow: hidden;
}

.mcp-editor {
  width: 100%;
  height: 100%;
  min-height: 240px;
}

.mcp-editor-fallback {
  width: 100%;
  height: 100%;
  min-height: 240px;
  padding: 12px;
  border: none;
  outline: none;
  resize: none;
  background: var(--bg-secondary);
  color: var(--text-primary);
  font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
  font-size: 13px;
  line-height: 1.6;
}

.mcp-editor-error {
  margin: 8px 24px 16px;
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

.action-btn-primary svg {
  width: 14px;
  height: 14px;
}

.action-btn-outline {
  border: 1px solid var(--border-color);
  background: transparent;
  color: var(--text-primary);
}

.action-btn-outline:hover {
  background: var(--bg-tertiary);
}

html[data-theme="dark"] .mcp-path-bar {
  background: var(--bg-tertiary);
  border-color: var(--border-color);
}

html[data-theme="dark"] .mcp-editor-fallback {
  background: var(--bg-tertiary);
  color: var(--text-primary);
}

html[data-theme="dark"] .mcp-editor-error {
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
