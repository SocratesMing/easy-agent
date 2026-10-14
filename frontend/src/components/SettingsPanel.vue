<template>
  <div class="settings-overlay" @click="$emit('close')">
    <div
      class="settings-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="settings-title"
      ref="dialogRef"
      tabindex="-1"
      @click.stop
    >
      <div class="settings-header">
        <h2 id="settings-title">设置</h2>
        <button @click="$emit('close')" class="close-btn">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="18" y1="6" x2="6" y2="18"></line>
            <line x1="6" y1="6" x2="18" y2="18"></line>
          </svg>
        </button>
      </div>

      <div class="settings-body">
        <div class="settings-nav" role="tablist" aria-label="设置分类">
          <button
            v-for="item in navItems"
            :key="item.key"
            :id="`tab-${item.key}`"
            type="button"
            class="nav-item"
            role="tab"
            :aria-selected="activeTab === item.key"
            :aria-controls="`panel-${item.key}`"
            :tabindex="activeTab === item.key ? 0 : -1"
            :class="{ active: activeTab === item.key }"
            @click="activeTab = item.key"
            @keydown="onNavKeydown($event, item.key)"
          >
            <span class="nav-icon" v-html="item.icon"></span>
            <span class="nav-label">{{ item.label }}</span>
          </button>
        </div>

        <div class="settings-content">
          <!-- 记忆 -->
          <div
            v-if="activeTab === 'memory'"
            class="content-panel"
            role="tabpanel"
            id="panel-memory"
            aria-labelledby="tab-memory"
          >
            <div class="panel-header memory-panel-header">
              <div class="panel-header-text">
                <h3>记忆</h3>
                <p class="panel-desc">当前用户的长期记忆文件，支持 Markdown 格式</p>
              </div>
              <button
                v-if="!loading"
                class="memory-edit-btn"
                :class="{ 'is-saving': memorySaving }"
                :disabled="memorySaving"
                @click="toggleMemoryEdit"
              >
                {{ memorySaving ? '保存中...' : (memoryEditing ? '保存' : '编辑') }}
              </button>
            </div>
            <div v-if="loading" class="loading-state"><div class="spinner"></div></div>
            <!-- 预览态：按 Markdown 渲染 -->
            <div
              v-else-if="!memoryEditing"
              class="memory-preview markdown-body"
              v-html="renderedMemory"
            ></div>
            <!-- 编辑态 -->
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
              </div>
            </div>
          </div>

          <!-- 提示词 -->
          <div
            v-if="activeTab === 'prompt'"
            class="content-panel"
            role="tabpanel"
            id="panel-prompt"
            aria-labelledby="tab-prompt"
          >
            <div class="panel-header">
              <h3>系统提示词</h3>
              <p class="panel-desc">当前使用的系统提示词（只读）</p>
            </div>
            <div v-if="loading" class="loading-state"><div class="spinner"></div></div>
            <div v-else class="prompt-viewer">
              <div
                class="prompt-preview markdown-body"
                v-html="renderedPrompt"
              ></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import { MessageBox } from 'element-ui'
import {
  getMemory,
  updateMemory,
  getSystemPrompt,
} from '../api/settings.js'
import { marked } from 'marked'
import { setupMarkedExtensions, normalizeMathDelimiters, escapeHtml } from '../markdownSetup.js'

setupMarkedExtensions()

const renderer = new marked.Renderer()

renderer.code = function(token) {
  const code = typeof token === 'object' ? (token.text || token.raw || '') : String(token || '')
  const lang = typeof token === 'object' ? (token.lang || '') : String(arguments[1] || '')
  const langLabel = lang || 'text'
  return `<div class="code-block-wrapper">
    <div class="code-header">
      <span class="code-lang">${escapeHtml(langLabel)}</span>
      <button class="code-copy-btn" onclick="copyCode(this)">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
          <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
        </svg>
        <span>复制</span>
      </button>
    </div>
    <pre><code>${escapeHtml(code)}</code></pre>
  </div>`
}

function settingsRenderMarkdown(content) {
  if (!content) return ''
  try {
    return marked.parse(normalizeMathDelimiters(content), {
      renderer,
      breaks: true,
      gfm: true,
    })
  } catch (e) {
    console.error('Markdown 渲染失败:', e)
    return content
  }
}

const emit = defineEmits(['close'])

// 设置弹窗：Esc 关闭 + 打开时聚焦当前标签，导航支持方向键切换（WAI-ARIA tabs）
const dialogRef = ref(null)

// 设置页可能先于聊天消息加载，这里确保代码块复制按钮可用（幂等）。
if (typeof window !== 'undefined' && !window.copyCode) {
  window.copyCode = async function(btn) {
    const wrapper = btn.closest('.code-block-wrapper')
    const codeEl = wrapper.querySelector('pre code') || wrapper.querySelector('pre')
    const code = codeEl?.textContent || ''

    const span = btn.querySelector('span')
    const originalText = span ? span.textContent : ''
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(code)
        if (span) span.textContent = '已复制!'
      } else {
        throw new Error('Clipboard API unavailable')
      }
    } catch (e) {
      const textarea = document.createElement('textarea')
      textarea.value = code
      textarea.setAttribute('readonly', '')
      textarea.style.position = 'fixed'
      textarea.style.top = '-9999px'
      document.body.appendChild(textarea)
      textarea.select()
      const ok = document.execCommand('copy')
      document.body.removeChild(textarea)
      if (span) span.textContent = ok ? '已复制!' : '复制失败'
    }
    btn.classList.add('copied')
    setTimeout(() => {
      if (span) span.textContent = originalText
      btn.classList.remove('copied')
    }, 2000)
  }
}

function onDialogKeydown(e) {
  if (e.key === 'Escape') emit('close')
}

function onNavKeydown(e, key) {
  const keys = navItems.map(i => i.key)
  const idx = keys.indexOf(key)
  if (idx === -1) return
  let next = null
  if (e.key === 'ArrowDown' || e.key === 'ArrowRight') next = keys[(idx + 1) % keys.length]
  else if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') next = keys[(idx - 1 + keys.length) % keys.length]
  else if (e.key === 'Home') next = keys[0]
  else if (e.key === 'End') next = keys[keys.length - 1]
  if (!next) return
  e.preventDefault()
  activeTab.value = next
  nextTick(() => {
    dialogRef.value?.querySelector(`#tab-${next}`)?.focus()
  })
}

const activeTab = ref('memory')
const loading = ref(false)

// 记忆
const memoryContent = ref('')
const memorySaving = ref(false)
const memorySaved = ref(false)
const memoryError = ref('')
// false = 预览态（Markdown 渲染）；true = 编辑态（textarea）
const memoryEditing = ref(false)

// 提示词
const promptContent = ref('')

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
]

async function loadTabData() {
  loading.value = true
  try {
    if (activeTab.value === 'memory') {
      const data = await getMemory()
      memoryContent.value = data.content || ''
      // 每次进入该页都从预览态开始
      memoryEditing.value = false
    } else if (activeTab.value === 'prompt') {
      const data = await getSystemPrompt()
      promptContent.value = data.content || ''
    }
  } catch (e) {
    console.error('加载数据失败:', e)
  } finally {
    loading.value = false
  }
}


// 预览态：把记忆内容按 Markdown 渲染
const renderedMemory = computed(() => {
  const raw = memoryContent.value || ''
  if (!raw.trim()) {
    return '<p class="memory-empty">暂无记忆内容，点击右上角「编辑」添加</p>'
  }
  try {
    return settingsRenderMarkdown(raw)
  } catch (e) {
    console.error('记忆 Markdown 渲染失败:', e)
    return `<pre>${escapeHtml(raw)}</pre>`
  }
})

// 系统提示词（只读）：同样按 Markdown 渲染
const renderedPrompt = computed(() => {
  const raw = promptContent.value || ''
  if (!raw.trim()) {
    return '<p class="memory-empty">暂无系统提示词</p>'
  }
  try {
    return settingsRenderMarkdown(raw)
  } catch (e) {
    console.error('系统提示词 Markdown 渲染失败:', e)
    return `<pre>${escapeHtml(raw)}</pre>`
  }
})

// 「编辑 / 保存」同一个按钮切换：编辑态点击即保存，成功后再回到预览态
async function toggleMemoryEdit() {
  if (!memoryEditing.value) {
    memoryError.value = ''
    memoryEditing.value = true
    return
  }
  await saveMemory()
  if (!memoryError.value) {
    memoryEditing.value = false
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
  document.addEventListener('keydown', onDialogKeydown)
  nextTick(() => {
    const active = dialogRef.value?.querySelector('.nav-item.active')
    ;(active || dialogRef.value)?.focus()
  })
})

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onDialogKeydown)
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
  border-radius: 1rem;
  width: min(75rem, 95vw);
  height: min(56rem, 92vh);
  display: flex;
  flex-direction: column;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
  overflow: hidden;
}

.settings-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 1rem 1.5rem;
  border-bottom: 1px solid #e2e8f0;
  flex-shrink: 0;
}

.settings-header h2 {
  margin: 0;
  font-size: 1.25rem;
  font-weight: 600;
  color: var(--text-primary);
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
  background: var(--bg-tertiary);
}

.close-btn svg {
  width: 20px;
  height: 20px;
  color: var(--text-secondary);
}

.settings-body {
  flex: 1;
  display: flex;
  overflow: hidden;
}

.settings-nav {
  width: 14rem;
  border-right: 1px solid #e2e8f0;
  padding: 0.75rem 0.5rem;
  flex-shrink: 0;
  overflow-y: auto;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 0.625rem;
  width: 100%;
  padding: 0.6875rem 0.875rem;
  border: none;
  background: none;
  font: inherit;
  text-align: left;
  border-radius: 0.5rem;
  cursor: pointer;
  transition: background 0.2s, color 0.2s;
  color: var(--text-secondary);
  font-size: 0.875rem;
  font-weight: 500;
}

.nav-item:focus-visible {
  outline: 2px solid var(--accent-color, #0ea5e9);
  outline-offset: 2px;
}

.nav-item:hover {
  background: var(--bg-tertiary);
  color: var(--text-primary);
}

.nav-item.active {
  background: color-mix(in srgb, var(--accent-color) 18%, transparent);
  color: #0ea5e9;
}

.nav-icon {
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.nav-icon ::v-deep svg {
  width: 20px;
  height: 20px;
}

.settings-content {
  flex: 1;
  overflow-y: auto;
  padding: 2rem;
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
  color: var(--text-primary);
}

.panel-desc {
  margin: 0;
  font-size: 13px;
  color: var(--text-secondary);
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
/* 记忆页头部：标题 + 右上角「编辑/保存」按钮 */
.memory-panel-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.panel-header-text {
  flex: 1;
  min-width: 0;
}

.memory-edit-btn {
  flex-shrink: 0;
  padding: 6px 16px;
  border: 1px solid var(--border-color, #e2e8f0);
  background: transparent;
  color: var(--text-primary);
  border-radius: 8px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
}

.memory-edit-btn:hover {
  border-color: #0ea5e9;
  color: #0ea5e9;
}

.memory-edit-btn.is-saving {
  border-color: #0ea5e9;
  background: #0ea5e9;
  color: #fff;
}

.memory-edit-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

/* 预览态：Markdown 渲染结果 */
.memory-preview {
  flex: 1;
  overflow-y: auto;
  min-height: 300px;
  padding: 16px 20px;
  border: 1px solid var(--border-color, #e2e8f0);
  border-radius: 10px;
  background: var(--bg-secondary, #fff);
  font-size: 14px;
  line-height: 1.7;
  color: var(--text-primary);
  word-break: break-word;
}

.memory-preview > *:first-child { margin-top: 0; }
.memory-preview > *:last-child { margin-bottom: 0; }

.memory-preview h1,
.memory-preview h2,
.memory-preview h3,
.memory-preview h4 {
  margin: 18px 0 10px;
  font-weight: 600;
  line-height: 1.35;
  color: var(--text-primary);
}

.memory-preview h1 { font-size: 20px; }
.memory-preview h2 { font-size: 17px; }
.memory-preview h3 { font-size: 15px; }
.memory-preview h4 { font-size: 14px; }
.memory-preview p { margin: 0 0 12px; }
.memory-preview ul,
.memory-preview ol { margin: 0 0 12px; padding-left: 22px; }
.memory-preview li { margin: 4px 0; }

.memory-preview code {
  padding: 2px 6px;
  border-radius: 4px;
  background: rgba(100, 116, 139, 0.15);
  font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
  font-size: 0.9em;
}

.memory-preview pre {
  margin: 0 0 12px;
  padding: 12px 14px;
  border-radius: 8px;
  background: rgba(100, 116, 139, 0.12);
  overflow-x: auto;
}

.memory-preview pre code {
  padding: 0;
  background: transparent;
}

.memory-preview blockquote {
  margin: 0 0 12px;
  padding: 2px 0 2px 12px;
  border-left: 3px solid #0ea5e9;
  color: var(--text-secondary);
}

.memory-preview a {
  color: #0ea5e9;
  text-decoration: none;
}

.memory-preview a:hover {
  text-decoration: underline;
}

.memory-preview table {
  width: 100%;
  margin: 0 0 12px;
  border-collapse: collapse;
  font-size: 13px;
}

.memory-preview th,
.memory-preview td {
  padding: 6px 10px;
  border: 1px solid var(--border-color, #e2e8f0);
  text-align: left;
}

.memory-preview th {
  background: rgba(100, 116, 139, 0.08);
  font-weight: 600;
}

.memory-preview hr {
  margin: 16px 0;
  border: none;
  border-top: 1px solid var(--border-color, #e2e8f0);
}

.memory-preview img {
  max-width: 100%;
}

.memory-preview .memory-empty {
  margin: 0;
  font-size: 13px;
  color: var(--text-secondary);
}

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
  color: var(--text-primary);
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

.prompt-preview {
  flex: 1;
  overflow-y: auto;
  min-height: 300px;
  padding: 16px 20px;
  border: 1px solid var(--border-color, #e2e8f0);
  border-radius: 10px;
  background: var(--bg-secondary, #fff);
  font-size: 14px;
  line-height: 1.7;
  color: var(--text-primary);
  word-break: break-word;
}

.prompt-preview > *:first-child { margin-top: 0; }
.prompt-preview > *:last-child { margin-bottom: 0; }

.prompt-preview h1,
.prompt-preview h2,
.prompt-preview h3,
.prompt-preview h4 {
  margin: 18px 0 10px;
  font-weight: 600;
  line-height: 1.35;
  color: var(--text-primary);
}

.prompt-preview h1 { font-size: 20px; }
.prompt-preview h2 { font-size: 17px; }
.prompt-preview h3 { font-size: 15px; }
.prompt-preview h4 { font-size: 14px; }
.prompt-preview p { margin: 0 0 12px; }
.prompt-preview ul,
.prompt-preview ol { margin: 0 0 12px; padding-left: 22px; }
.prompt-preview li { margin: 4px 0; }

.prompt-preview code {
  padding: 2px 6px;
  border-radius: 4px;
  background: rgba(100, 116, 139, 0.15);
  font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
  font-size: 0.9em;
}

.prompt-preview pre {
  margin: 0 0 12px;
  padding: 12px 14px;
  border-radius: 8px;
  background: rgba(100, 116, 139, 0.12);
  overflow-x: auto;
}

.prompt-preview pre code {
  padding: 0;
  background: transparent;
}

.prompt-preview blockquote {
  margin: 0 0 12px;
  padding: 2px 0 2px 12px;
  border-left: 3px solid #0ea5e9;
  color: var(--text-secondary);
}

.prompt-preview a {
  color: #0ea5e9;
  text-decoration: none;
}

.prompt-preview a:hover {
  text-decoration: underline;
}

.prompt-preview table {
  width: 100%;
  margin: 0 0 12px;
  border-collapse: collapse;
  font-size: 13px;
}

.prompt-preview th,
.prompt-preview td {
  padding: 6px 10px;
  border: 1px solid var(--border-color, #e2e8f0);
  text-align: left;
}

.prompt-preview th {
  background: rgba(100, 116, 139, 0.08);
  font-weight: 600;
}

.prompt-preview hr {
  margin: 16px 0;
  border: none;
  border-top: 1px solid var(--border-color, #e2e8f0);
}

.prompt-preview img {
  max-width: 100%;
}

.prompt-preview .memory-empty {
  margin: 0;
  font-size: 13px;
  color: var(--text-secondary);
}

.prompt-content {
  padding: 16px;
  background: var(--bg-tertiary);
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-secondary);
  white-space: pre-wrap;
  word-wrap: break-word;
  margin: 0;
  max-height: 450px;
  overflow-y: auto;
}

/* 窄屏：设置弹窗全屏化，左侧导航改为顶部横向标签 */
@media (max-width: 48rem) {
  .settings-overlay {
    align-items: stretch;
    justify-content: stretch;
  }

  .settings-modal {
    width: 100vw;
    height: 100dvh;
    border-radius: 0;
    box-shadow: none;
  }

  .settings-body {
    flex-direction: column;
  }

  .settings-nav {
    width: 100%;
    display: flex;
    gap: 0.25rem;
    padding: 0.5rem 0.75rem;
    border-right: none;
    border-bottom: 1px solid #e2e8f0;
    overflow-x: auto;
    overflow-y: hidden;
  }

  .nav-item {
    width: auto;
    flex-shrink: 0;
    white-space: nowrap;
  }

  .settings-content {
    padding: 1rem;
  }
}

/* Dark theme overrides */
html[data-theme="dark"] .settings-modal {
  background: #1e293b;
}

html[data-theme="dark"] .settings-header h2 {
  color: #e2e8f0;
}

html[data-theme="dark"] .panel-desc {
  color: var(--text-secondary);
}

<style>
/* 设置页 Markdown 预览：v-html 动态节点不带 scoped data 属性，
   这里用全局选择器穿透，避免标题/列表/表格退化为正文样式。 */
.memory-preview h1,
.memory-preview h2,
.memory-preview h3,
.memory-preview h4,
.memory-preview h5,
.memory-preview h6,
.prompt-preview h1,
.prompt-preview h2,
.prompt-preview h3,
.prompt-preview h4,
.prompt-preview h5,
.prompt-preview h6 {
  margin: 18px 0 10px;
  font-weight: 600;
  line-height: 1.35;
  color: var(--text-primary, #1e293b);
}

.memory-preview h1,
.prompt-preview h1 {
  font-size: 22px;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--border-color, #e2e8f0);
}

.memory-preview h2,
.prompt-preview h2 {
  font-size: 18px;
  padding-bottom: 4px;
  border-bottom: 1px solid var(--border-color, #e2e8f0);
}

.memory-preview h3,
.prompt-preview h3 {
  font-size: 16px;
}

.memory-preview h4,
.prompt-preview h4 {
  font-size: 15px;
}

.memory-preview h5,
.memory-preview h6,
.prompt-preview h5,
.prompt-preview h6 {
  font-size: 14px;
}

.memory-preview p,
.prompt-preview p {
  margin: 0 0 12px;
}

.memory-preview ul,
.memory-preview ol,
.prompt-preview ul,
.prompt-preview ol {
  margin: 0 0 12px;
  padding-left: 22px;
}

.memory-preview li,
.prompt-preview li {
  margin: 4px 0;
}

.memory-preview code,
.prompt-preview code {
  padding: 2px 6px;
  border-radius: 4px;
  background: rgba(100, 116, 139, 0.15);
  font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
  font-size: 0.9em;
}

.memory-preview pre,
.prompt-preview pre,
.code-block-wrapper pre {
  margin: 0;
}

.memory-preview blockquote,
.prompt-preview blockquote {
  margin: 0 0 12px;
  padding: 2px 0 2px 12px;
  border-left: 3px solid #0ea5e9;
  color: var(--text-secondary, #64748b);
}

.memory-preview a,
.prompt-preview a {
  color: #0ea5e9;
  text-decoration: none;
}

.memory-preview a:hover,
.prompt-preview a:hover {
  text-decoration: underline;
}

.memory-preview table,
.prompt-preview table {
  width: 100%;
  margin: 0 0 12px;
  border-collapse: collapse;
  font-size: 13px;
}

.memory-preview th,
.memory-preview td,
.prompt-preview th,
.prompt-preview td {
  padding: 6px 10px;
  border: 1px solid var(--border-color, #e2e8f0);
  text-align: left;
}

.memory-preview th,
.prompt-preview th {
  background: rgba(100, 116, 139, 0.08);
  font-weight: 600;
}

.memory-preview hr,
.prompt-preview hr {
  margin: 16px 0;
  border: none;
  border-top: 1px solid var(--border-color, #e2e8f0);
}

.memory-preview img,
.prompt-preview img {
  max-width: 100%;
}

.memory-preview :first-child,
.prompt-preview :first-child {
  margin-top: 0;
}

.memory-preview :last-child,
.prompt-preview :last-child {
  margin-bottom: 0;
}

html[data-theme='dark'] .memory-preview,
html[data-theme='dark'] .prompt-preview {
  color: #e5e7eb;
}

html[data-theme='dark'] .memory-preview h1,
html[data-theme='dark'] .memory-preview h2,
html[data-theme='dark'] .memory-preview h3,
html[data-theme='dark'] .memory-preview h4,
html[data-theme='dark'] .memory-preview h5,
html[data-theme='dark'] .memory-preview h6,
html[data-theme='dark'] .prompt-preview h1,
html[data-theme='dark'] .prompt-preview h2,
html[data-theme='dark'] .prompt-preview h3,
html[data-theme='dark'] .prompt-preview h4,
html[data-theme='dark'] .prompt-preview h5,
html[data-theme='dark'] .prompt-preview h6 {
  color: #f8fafc;
  border-bottom-color: #30363d;
}

html[data-theme='dark'] .memory-preview blockquote,
html[data-theme='dark'] .prompt-preview blockquote {
  color: #94a3b8;
}

html[data-theme='dark'] .memory-preview table,
html[data-theme='dark'] .prompt-preview table,
html[data-theme='dark'] .memory-preview th,
html[data-theme='dark'] .memory-preview td,
html[data-theme='dark'] .prompt-preview th,
html[data-theme='dark'] .prompt-preview td {
  border-color: #30363d;
}

html[data-theme='dark'] .memory-preview th,
html[data-theme='dark'] .prompt-preview th {
  background: rgba(148, 163, 184, 0.12);
}
</style>

<style>
/* 设置页 Markdown 预览：与聊天区一致的代码块结构（语言标签 + 复制按钮）。
   全局样式用于穿透 v-html 的动态节点。 */
.markdown-body .code-block-wrapper {
  margin: 0 0 12px;
  border: 1px solid #e1e4e8;
  border-radius: 8px;
  overflow: hidden;
}

.markdown-body .code-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: #f1f3f5;
  padding: 8px 14px;
  border-bottom: 1px solid #e1e4e8;
}

.markdown-body .code-lang {
  font-size: 12px;
  color: #57606a;
  font-weight: 500;
}

.markdown-body .code-copy-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  background: #fff;
  border: 1px solid #d0d7de;
  border-radius: 6px;
  color: #57606a;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.2s;
}

.markdown-body .code-copy-btn:hover {
  background: #f3f4f6;
  border-color: #8c959f;
  color: #24292e;
}

.markdown-body .code-copy-btn.copied {
  color: #22c55e;
}

.markdown-body .code-copy-btn svg {
  width: 13px;
  height: 13px;
}

.markdown-body .code-block-wrapper pre,
.markdown-body .code-block-wrapper .shiki {
  margin: 0;
  padding: 12px 14px;
  border-radius: 0;
  border: none;
  border-top: none;
  background: #f6f8fa;
  color: #24292e;
  overflow-x: auto;
}

.markdown-body .code-block-wrapper pre code,
.markdown-body .code-block-wrapper .shiki code {
  display: block;
  margin: 0;
  padding: 0;
  background: transparent;
  white-space: pre;
  font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
  font-size: 13px;
  line-height: 1.5;
}

html[data-theme="dark"] .markdown-body .code-block-wrapper {
  border-color: #30363d;
}

html[data-theme="dark"] .markdown-body .code-header {
  background: #161b22;
  border-bottom-color: #30363d;
}

html[data-theme="dark"] .markdown-body .code-lang,
html[data-theme="dark"] .markdown-body .code-copy-btn {
  color: #8b949e;
}

html[data-theme="dark"] .markdown-body .code-copy-btn {
  background: #21262d;
  border-color: #30363d;
}

html[data-theme="dark"] .markdown-body .code-copy-btn:hover {
  background: #30363d;
  border-color: #8b949e;
  color: #c9d1d9;
}

html[data-theme="dark"] .markdown-body .code-block-wrapper pre,
html[data-theme="dark"] .markdown-body .code-block-wrapper .shiki,
html[data-theme="dark"] .markdown-body .code-block-wrapper pre code,
html[data-theme="dark"] .markdown-body .code-block-wrapper .shiki code {
  background: transparent;
  color: #c9d1d9;
}

html[data-theme="dark"] .memory-preview :not(pre) > code,
html[data-theme="dark"] .prompt-preview :not(pre) > code {
  background: #30363d !important;
  color: #c9d1d9 !important;
}
</style>
