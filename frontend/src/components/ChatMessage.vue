<template>
  <div class="message" :class="[message.role]">
    <div class="message-content">
      <!-- 文件卡片 -->
      <div v-if="message.files && message.files.length > 0" class="files-container">
        <div 
          v-for="(file, index) in message.files" 
          :key="index" 
          class="file-card"
        >
          <div class="file-icon">
            <FileIcon :filename="file.filename" :size="40" />
          </div>
          <div class="file-info">
            <span class="file-name">{{ file.filename }}</span>
            <div class="file-meta">
              <span class="file-size">{{ formatSize(file.size) }}</span>
              <span class="file-type">{{ getFileExtension(file.filename) }}</span>
            </div>
          </div>
        </div>
      </div>
      
      <!-- 按顺序渲染内容块 -->
      <template v-if="sortedBlocks.length > 0">
        <template v-for="(block, index) in sortedBlocks" :key="index">
          <!-- 知识库检索块 -->
          <div v-if="block.type === 'knowledge_base'" class="knowledge-base-block">
            <div class="kb-header" @click="toggleKnowledgeBase(index)">
              <svg class="kb-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="11" cy="11" r="8"></circle>
                <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
              </svg>
              <span class="kb-title">知识库检索</span>
              <span v-if="block.docs" class="kb-count">{{ block.docs.length }}篇文档</span>
              <svg class="toggle-arrow" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" :class="{ rotated: isExpandedKnowledgeBase(index) }">
                <polyline points="6 9 12 15 18 9"></polyline>
              </svg>
            </div>
            <div v-if="isExpandedKnowledgeBase(index)" class="kb-content">
              <div v-if="block.docs && block.docs.length > 0" class="kb-docs-list">
                <div v-for="(doc, docIndex) in block.docs" :key="docIndex" class="kb-doc-item">
                  <div class="kb-doc-icon">📄</div>
                  <div class="kb-doc-info">
                    <span class="kb-doc-name">{{ doc.file_name }}</span>
                    <span class="kb-doc-score">相关度: {{ (doc.score * 100).toFixed(1) }}%</span>
                  </div>
                </div>
              </div>
              <div v-else class="kb-no-docs">未找到相关文档</div>
            </div>
          </div>

          <!-- 思考内容块 -->
          <div v-if="block.type === 'thinking'" class="thinking-block" :class="{ 'thinking-active': block.duration == null && message.loading }">
            <div class="thinking-header" @click="toggleThinking(index)">
              <div v-if="block.duration == null && message.loading" class="thinking-spinner">
                <span></span><span></span><span></span>
              </div>
              <svg v-else class="thinking-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="12" r="10"></circle>
                <path d="M12 16v-4M12 8h.01"></path>
                <path d="M9.5 9.5c.5-.5 1.5-1 2.5-1s2 .5 2.5 1c.5.5.5 1.5 0 2.5-.5.5-1.5 1-2.5 1"></path>
              </svg>
              <span class="thinking-title">{{ block.duration == null && message.loading ? '正在思考...' : '思考过程' }}</span>
              <span v-if="block.duration != null" class="thinking-duration">用时 {{ block.duration }} 秒</span>
              <svg class="toggle-arrow" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" :class="{ rotated: isExpandedThinking(index) }">
                <polyline points="6 9 12 15 18 9"></polyline>
              </svg>
            </div>
            <div v-if="isExpandedThinking(index)" class="thinking-content">
              <div class="thinking-text" v-html="renderMarkdown(block.content)"></div>
            </div>
          </div>

          <!-- 工具调用块（合并参数、结果、耗时） -->
          <div v-if="block.type === 'tool_call'" class="tool-call-block" :class="{ error: block.success === false }">
            <div class="tool-call-header" @click="toggleToolCall(index)">
              <svg class="tool-icon" :class="{ success: block.success === true, error: block.success === false, spinning: block.duration == null }" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"></path>
              </svg>
              <span class="tool-name-badge">{{ block.tool_name }}</span>
              <template v-if="block.duration != null">
                <svg v-if="block.success" class="tool-status-icon success" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
                  <polyline points="22 4 12 14.01 9 11.01"></polyline>
                </svg>
                <svg v-else class="tool-status-icon error" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <circle cx="12" cy="12" r="10"></circle>
                  <line x1="15" y1="9" x2="9" y2="15"></line>
                  <line x1="9" y1="9" x2="15" y2="15"></line>
                </svg>
              </template>
              <span v-if="block.duration != null" class="tool-duration">用时 {{ block.duration }} 秒</span>
              <span v-else class="tool-status-text executing">执行中...</span>
              <svg class="toggle-arrow" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" :class="{ rotated: isExpandedToolCall(index) }">
                <polyline points="6 9 12 15 18 9"></polyline>
              </svg>
            </div>
            <div v-if="isExpandedToolCall(index)" class="tool-call-body">
              <div v-if="block.arguments && Object.keys(block.arguments).length > 0" class="tool-section">
                <div class="tool-section-label">参数</div>
                <pre class="tool-section-content">{{ truncateResult(formatJson(block.arguments), 1000) }}</pre>
              </div>
              <div v-if="block.result" class="tool-section">
                <div class="tool-section-label">结果</div>
                <pre class="tool-section-content" :class="{ error: block.success === false }">{{ truncateResult(block.result, 1000) }}</pre>
              </div>
              <div v-else-if="block.duration == null" class="tool-section">
                <div class="tool-section-label">结果</div>
                <div class="tool-executing-hint">等待执行结果...</div>
              </div>
            </div>
          </div>

          <!-- 内容块 -->
          <div v-if="block.type === 'content'" class="message-text" v-html="renderMarkdown(block.content)"></div>
        </template>
      </template>

      <!-- 兼容旧数据：没有blocks时的渲染 -->
      <template v-else>
        <div v-if="message.thinking" class="thinking-block" :class="{ 'thinking-active': message.thinking_duration == null && message.loading }">
          <div class="thinking-header" @click="showThinking = !showThinking">
            <div v-if="message.thinking_duration == null && message.loading" class="thinking-spinner">
              <span></span><span></span><span></span>
            </div>
            <svg v-else class="thinking-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="10"></circle>
              <path d="M12 16v-4M12 8h.01"></path>
              <path d="M9.5 9.5c.5-.5 1.5-1 2.5-1s2 .5 2.5 1c.5.5.5 1.5 0 2.5-.5.5-1.5 1-2.5 1"></path>
            </svg>
            <span class="thinking-title">{{ message.thinking_duration == null && message.loading ? '正在思考...' : '思考过程' }}</span>
            <span v-if="message.thinking_duration != null" class="thinking-duration">用时 {{ message.thinking_duration }} 秒</span>
            <svg class="toggle-arrow" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" :class="{ rotated: showThinking }">
              <polyline points="6 9 12 15 18 9"></polyline>
            </svg>
          </div>
          <div v-if="showThinking" class="thinking-content">
            <div class="thinking-text" v-html="renderMarkdown(message.thinking)"></div>
          </div>
        </div>

        <div v-if="message.tool_calls && message.tool_calls.length > 0" class="tool-calls-block">
          <div class="tool-calls-header">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"></path>
            </svg>
            <span>工具调用</span>
          </div>
          <div class="tool-calls-list">
            <div v-for="(tool, idx) in message.tool_calls" :key="idx" class="tool-call-item">
              <div class="tool-name">
                {{ tool.tool_name }}
                <span v-if="tool.duration !== undefined" class="tool-duration">耗时 {{ tool.duration }}s</span>
              </div>
              <div v-if="tool.arguments" class="tool-args">
                <pre>{{ formatJson(tool.arguments) }}</pre>
              </div>
              <div v-if="tool.result" class="tool-result" :class="{ error: !tool.success }">
                <span class="result-label">{{ tool.success ? '结果:' : '错误:' }}</span>
                <span class="result-content">{{ truncateResult(tool.result, 1000) }}</span>
              </div>
            </div>
          </div>
        </div>

        <div v-if="message.content" class="message-text" v-html="renderMarkdown(message.content)"></div>

        <!-- 生成的文件按钮 -->
        <div v-if="message.role === 'assistant' && message.generated_files && message.generated_files.length > 0" class="generated-files-btn-container">
          <button class="generated-files-btn" @click="emit('viewGeneratedFiles')" title="查看生成的文件">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
              <polyline points="14 2 14 8 20 8"></polyline>
              <line x1="16" y1="13" x2="8" y2="13"></line>
              <line x1="16" y1="17" x2="8" y2="17"></line>
              <polyline points="10 9 9 9 8 9"></polyline>
            </svg>
            <span>查看生成的文件 ({{ message.generated_files.length }})</span>
          </button>
        </div>
      </template>
      
      <!-- Token 用量显示（助手消息） -->
      <div v-if="message.role === 'assistant' && message.usage && message.usage.total_tokens > 0" class="message-usage">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="12" height="12">
          <path d="M12 2L2 7l10 5 10-5-10-5z"></path>
          <path d="M2 17l10 5 10-5"></path>
          <path d="M2 12l10 5 10-5"></path>
        </svg>
        <span class="usage-total">{{ message.usage.total_tokens.toLocaleString() }} tokens</span>
        <span class="usage-detail">↑{{ message.usage.input_tokens.toLocaleString() }} ↓{{ message.usage.output_tokens.toLocaleString() }}</span>
      </div>

      <!-- 用户消息显示复制和重试按钮 -->
      <div v-if="message.role === 'user' && message.content" class="message-actions">
        <button class="action-btn retry-btn" @click="retryMessage" title="重新发送">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="23 4 23 10 17 10"></polyline>
            <polyline points="1 20 1 14 7 14"></polyline>
            <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path>
          </svg>
        </button>
        <button class="action-btn copy-btn" @click="copyMessage" title="复制">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
          </svg>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, shallowRef, watch } from 'vue'
import { createHighlighter } from 'shiki'
import { marked } from 'marked'
import FileIcon from './FileIcon.vue'

const props = defineProps({
  message: {
    type: Object,
    required: true
  }
})

const emit = defineEmits(['removeFile', 'viewGeneratedFiles', 'retry'])

const showThinking = ref(true)
const expandedThinking = ref({})
const expandedKnowledgeBase = ref({})
const expandedTool = ref({})
const highlighter = shallowRef(null)

const langAliases = {
  'js': 'javascript',
  'ts': 'typescript',
  'py': 'python',
  'rb': 'ruby',
  'sh': 'bash',
  'yml': 'yaml',
  'md': 'markdown',
  'c++': 'cpp',
  'c#': 'csharp',
  'cs': 'csharp',
}

onMounted(async () => {
  try {
    highlighter.value = await createHighlighter({
      themes: ['github-light'],
      langs: ['javascript', 'typescript', 'python', 'java', 'cpp', 'c', 'go', 'rust', 'html', 'css', 'json', 'yaml', 'markdown', 'bash', 'shell', 'sql', 'xml', 'vue', 'jsx', 'tsx', 'text']
    })
  } catch (e) {
    console.error('Shiki 初始化失败:', e)
  }
})

watch(() => props.message.id, () => {
  expandedThinking.value = {}
  expandedKnowledgeBase.value = {}
  expandedTool.value = {}
})

const sortedBlocks = computed(() => {
  // 如果消息有 blocks，返回排序后的 blocks
  if (props.message.blocks && props.message.blocks.length > 0) {
    return [...props.message.blocks].sort((a, b) => (a.order || 0) - (b.order || 0))
  }
  
  // 否则从旧数据格式创建 blocks（用于从数据库加载的消息）
  const blocks = []
  
  // 添加思考 block
  if (props.message.thinking) {
    blocks.push({
      type: 'thinking',
      content: props.message.thinking,
      duration: props.message.thinking_duration,
      step: 0,
      order: 0
    })
  }
  
  // 添加工具调用 blocks（合并参数、结果、耗时到一个卡片）
  if (props.message.tool_calls && props.message.tool_calls.length > 0) {
    props.message.tool_calls.forEach((tool, idx) => {
      blocks.push({
        type: 'tool_call',
        tool_name: tool.tool_name,
        arguments: tool.arguments,
        result: tool.result,
        success: tool.success,
        duration: tool.duration,
        step: tool.step || 0,
        order: idx + 1
      })
    })
  }
  
  // 添加内容 block
  if (props.message.content) {
    blocks.push({
      type: 'content',
      content: props.message.content,
      order: blocks.length + 1
    })
  }
  
  return blocks
})

// 使用 block 的唯一标识来跟踪展开状态
function getBlockKey(block, index) {
  return block.id || `${block.type}-${block.step || 0}-${block.tool_name || ''}-${index}`
}

function isExpandedThinking(index) {
  const block = sortedBlocks.value[index]
  if (!block) return false
  const key = getBlockKey(block, index)
  return expandedThinking.value[key] === true
}

function toggleThinking(index) {
  const block = sortedBlocks.value[index]
  if (!block) return
  const key = getBlockKey(block, index)
  expandedThinking.value[key] = !expandedThinking.value[key]
}

function isExpandedKnowledgeBase(index) {
  const block = sortedBlocks.value[index]
  if (!block) return false
  const key = getBlockKey(block, index)
  return expandedKnowledgeBase.value[key] !== false
}

function toggleKnowledgeBase(index) {
  const block = sortedBlocks.value[index]
  if (!block) return
  const key = getBlockKey(block, index)
  expandedKnowledgeBase.value[key] = !expandedKnowledgeBase.value[key]
}

function toggleToolCall(index) {
  const block = sortedBlocks.value[index]
  if (!block) return
  const key = getBlockKey(block, index)
  expandedTool.value[key] = !expandedTool.value[key]
}

function isExpandedToolCall(index) {
  const block = sortedBlocks.value[index]
  if (!block) return false
  const key = getBlockKey(block, index)
  return expandedTool.value[key] === true
}

function highlightCode(code, lang) {
  if (!highlighter.value) {
    return `<pre style="background: #f6f8fa; padding: 12px; border-radius: 8px; overflow-x: auto; border: 1px solid #e1e4e8;"><code style="color: #24292e; font-family: 'Fira Code', Consolas, monospace; font-size: 13px;">${escapeHtml(code)}</code></pre>`
  }
  
  const normalizedLang = lang ? lang.toLowerCase() : 'text'
  const mappedLang = langAliases[normalizedLang] || normalizedLang
  
  const loadedLangs = highlighter.value.getLoadedLanguages()
  const validLang = loadedLangs.includes(mappedLang) ? mappedLang : 'text'
  
  try {
    const html = highlighter.value.codeToHtml(code, {
      lang: validLang,
      theme: 'github-light'
    })
    return html
  } catch (e) {
    console.error('Shiki 高亮失败:', e, 'lang:', validLang)
    return `<pre style="background: #f6f8fa; padding: 12px; border-radius: 8px; overflow-x: auto; border: 1px solid #e1e4e8;"><code style="color: #24292e; font-family: 'Fira Code', Consolas, monospace; font-size: 13px;">${escapeHtml(code)}</code></pre>`
  }
}

function escapeHtml(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
}

const renderer = new marked.Renderer()

renderer.code = function(token) {
  let code = ''
  let language = ''
  
  if (typeof token === 'object') {
    code = token.text || token.raw || ''
    language = token.lang || ''
  } else {
    code = arguments[0] || ''
    language = arguments[1] || ''
  }
  
  const langLabel = language || 'text'
  const highlightedCode = highlightCode(code, language)
  
  return `<div class="code-block-wrapper">
    <div class="code-header">
      <span class="code-lang">${langLabel}</span>
      <button class="code-copy-btn" onclick="copyCode(this)">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
          <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
        </svg>
        <span>复制</span>
      </button>
    </div>
    ${highlightedCode}
  </div>`
}

function renderMarkdown(content) {
  if (!content) return ''
  try {
    return marked.parse(content, { renderer, breaks: true, gfm: true })
  } catch (e) {
    console.error('Markdown 渲染失败:', e)
    return escapeHtml(content)
  }
}

function formatJson(obj) {
  try {
    if (typeof obj === 'string') {
      const parsed = JSON.parse(obj)
      return JSON.stringify(parsed, null, 2)
    }
    if (obj && typeof obj === 'object' && 'raw' in obj && Object.keys(obj).length === 1) {
      const parsed = JSON.parse(obj.raw)
      return JSON.stringify(parsed, null, 2)
    }
    return JSON.stringify(obj, null, 2)
  } catch (e) {
    if (obj && typeof obj === 'object' && 'raw' in obj) {
      return obj.raw
    }
    return String(obj)
  }
}

function truncateResult(result, maxLen = 500) {
  if (!result) return ''
  const str = String(result)
  return str.length > maxLen ? str.substring(0, maxLen) + '...' : str
}

function formatSize(bytes) {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

function getFileExtension(filename) {
  const parts = filename.split('.')
  if (parts.length > 1) {
    return parts[parts.length - 1].toUpperCase()
  }
  return 'FILE'
}

async function copyMessage() {
  if (!props.message.content) return
  
  try {
    await navigator.clipboard.writeText(props.message.content)
  } catch (err) {
    console.error('复制失败:', err)
  }
}

function retryMessage() {
  // 触发重试事件，传递消息内容（确保是纯字符串）
  const content = String(props.message.content || '')
  emit('retry', content)
}

function removeFile(index) {
  if (props.message.files && props.message.files[index]) {
    const file = props.message.files[index]
    // 通知父组件删除文件
    emit('removeFile', file)
  }
}

onMounted(() => {
  if (!window.copyCode) {
    window.copyCode = async function(btn) {
      const wrapper = btn.closest('.code-block-wrapper')
      const codeEl = wrapper.querySelector('pre code') || wrapper.querySelector('pre')
      const code = codeEl?.textContent || ''
      
      try {
        await navigator.clipboard.writeText(code)
        const span = btn.querySelector('span')
        const originalText = span.textContent
        span.textContent = '已复制!'
        btn.classList.add('copied')
        setTimeout(() => {
          span.textContent = originalText
          btn.classList.remove('copied')
        }, 2000)
      } catch (err) {
        console.error('复制失败:', err)
      }
    }
  }
})
</script>

<style scoped>
.message {
  display: flex;
  gap: 12px;
  padding: 12px 0;
  animation: fadeIn 0.3s ease-out;
}

@keyframes fadeIn {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.message.user {
  justify-content: flex-end;
  width: 80%;
  max-width: 900px;
  margin: 0 auto;
}

.message.assistant {
  justify-content: flex-start;
  width: 80%;
  max-width: 900px;
  margin: 0 auto;
}

.message-content {
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 100%;
}

.message.user .message-content {
  align-items: flex-end;
  width: fit-content;
  max-width: 100%;
}

.message.user .message-content .message-text {
  width: fit-content;
  max-width: 100%;
}

.message.assistant .message-content {
  align-items: flex-start;
  width: 100%;
}

/* 文件容器样式 */
.files-container {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 4px;
  width: 100%;
}

/* 文件卡片样式 */
.file-card {
  display: flex;
  align-items: center;
  gap: 8px;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  padding: 10px 12px;
  border-radius: 10px;
  max-width: 250px;
  flex-shrink: 0;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.05);
  transition: all 0.2s ease;
}

.remove-file-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  background: #f1f5f9;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.2s ease;
  flex-shrink: 0;
}

.remove-file-btn:hover {
  background: #fee2e2;
  transform: scale(1.05);
}

.remove-file-btn svg {
  width: 14px;
  height: 14px;
  color: #64748b;
}

.remove-file-btn:hover svg {
  color: #dc2626;
}

.file-card:hover {
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
  transform: translateY(-1px);
}

/* 文件图标样式 */
.file-icon {
  width: 48px;
  height: 48px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.file-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.file-name {
  font-size: 14px;
  font-weight: 500;
  color: #1E293B;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.file-meta {
  display: flex;
  align-items: center;
  gap: 8px;
}

.file-size {
  font-size: 12px;
  color: #64748B;
}

.file-type {
  font-size: 11px;
  color: #94A3B8;
  text-transform: uppercase;
  font-weight: 500;
  padding: 2px 6px;
  border-radius: 6px;
  background: #F1F5F9;
}

.knowledge-base-block {
  display: flex;
  flex-direction: column;
  background: transparent;
  margin-bottom: 12px;
  width: 100%;
}

.knowledge-base-block .kb-header {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: transparent;
  border-radius: 8px;
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
  color: #475569;
  user-select: none;
  transition: background 0.2s;
  align-self: flex-start;
}

.knowledge-base-block .kb-header:hover {
  background: #f1f5f9;
}

.knowledge-base-block .kb-icon {
  width: 16px;
  height: 16px;
  color: #64748b;
  flex-shrink: 0;
}

.knowledge-base-block .kb-title {
  flex: 1;
}

.knowledge-base-block .kb-count {
  font-size: 11px;
  color: #94a3b8;
  font-weight: 400;
}

.knowledge-base-block .kb-content {
  padding: 12px 14px;
  font-size: 15px;
  color: #475569;
  line-height: 1.7;
  width: 100%;
  box-sizing: border-box;
}

.knowledge-base-block .kb-docs-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.knowledge-base-block .kb-doc-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
}

.knowledge-base-block .kb-doc-icon {
  font-size: 16px;
}

.knowledge-base-block .kb-doc-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.knowledge-base-block .kb-doc-name {
  font-size: 13px;
  font-weight: 500;
  color: #1e293b;
}

.knowledge-base-block .kb-doc-score {
  font-size: 11px;
  color: #64748b;
}

.knowledge-base-block .kb-no-docs {
  font-size: 13px;
  color: #64748b;
  text-align: center;
  padding: 8px;
}

.thinking-block {
  display: flex;
  flex-direction: column;
  background: transparent;
  margin-bottom: 12px;
  width: 100%;
}

.thinking-header {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: transparent;
  border-radius: 8px;
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
  color: #475569;
  user-select: none;
  transition: background 0.2s;
  align-self: flex-start;
}

.thinking-header:hover {
  background: #f1f5f9;
}

.thinking-icon {
  width: 16px;
  height: 16px;
  color: #64748b;
  flex-shrink: 0;
}

.thinking-title {
  flex: 1;
}

.thinking-active .thinking-title {
  color: #6366f1;
}

.thinking-spinner {
  display: flex;
  align-items: center;
  gap: 3px;
  width: 16px;
  height: 16px;
  flex-shrink: 0;
}

.thinking-spinner span {
  display: block;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: #6366f1;
  animation: thinking-bounce 1.2s ease-in-out infinite;
}

.thinking-spinner span:nth-child(2) {
  animation-delay: 0.15s;
}

.thinking-spinner span:nth-child(3) {
  animation-delay: 0.3s;
}

@keyframes thinking-bounce {
  0%, 80%, 100% {
    transform: scale(0.6);
    opacity: 0.4;
  }
  40% {
    transform: scale(1);
    opacity: 1;
  }
}

.thinking-duration {
  font-size: 11px;
  color: #94a3b8;
  font-weight: 400;
}

.toggle-arrow {
  width: 14px;
  height: 14px;
  transition: transform 0.2s;
  color: #64748b;
  flex-shrink: 0;
}

.toggle-arrow.rotated {
  transform: rotate(180deg);
}

.thinking-content {
  padding: 12px 14px;
  font-size: 15px;
  color: #475569;
  line-height: 1.7;
  width: 100%;
  box-sizing: border-box;
}

.thinking-text {
  color: #64748b;
}

.thinking-text :deep(p) {
  margin: 0 0 12px 0;
}

.thinking-text :deep(p:last-child) {
  margin-bottom: 0;
}

.thinking-text :deep(ol),
.thinking-text :deep(ul) {
  margin: 12px 0;
  padding-left: 24px;
}

.thinking-text :deep(li) {
  margin: 6px 0;
}

.thinking-text :deep(code) {
  background: #e2e8f0;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 13px;
  color: #475569;
  font-family: 'Fira Code', 'Consolas', monospace;
}

.thinking-text :deep(pre) {
  background: #f6f8fa;
  color: #24292e;
  padding: 14px;
  border-radius: 8px;
  overflow-x: auto;
  margin: 16px 0;
  border: 1px solid #e1e4e8;
}

.thinking-text :deep(pre code) {
  background: transparent;
  padding: 0;
  font-family: 'Fira Code', 'Consolas', 'Monaco', monospace;
  font-size: 13px;
}

.thinking-text :deep(.code-block-wrapper) {
  background: #f6f8fa;
  border: 1px solid #e1e4e8;
  border-radius: 8px;
  margin: 16px 0;
  overflow: hidden;
}

.thinking-text :deep(.code-block-wrapper pre) {
  margin: 0;
  border: none;
  padding: 14px;
}

.thinking-text :deep(.code-block-wrapper pre code) {
  font-family: 'Fira Code', 'Consolas', 'Monaco', monospace;
  font-size: 13px;
}

.thinking-text :deep(.code-block-wrapper .shiki) {
  background: transparent !important;
  margin: 0;
}

.thinking-text :deep(.code-block-wrapper .shiki code) {
  font-family: 'Fira Code', 'Consolas', 'Monaco', monospace;
  font-size: 13px;
}

.thinking-text :deep(blockquote) {
  border-left: 3px solid #cbd5e1;
  padding-left: 16px;
  margin: 12px 0;
  color: #64748b;
}

.thinking-quote {
  border-left: 3px solid #cbd5e1;
  padding-left: 16px;
  color: #64748b;
}

.tool-call-block {
  display: flex;
  flex-direction: column;
  background: transparent;
  margin-bottom: 8px;
  width: 100%;
}

.tool-call-block.error .tool-call-header {
  color: #dc2626;
}

.tool-call-header {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: transparent;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 500;
  color: #475569;
  cursor: pointer;
  user-select: none;
  transition: background 0.2s;
  align-self: flex-start;
}

.tool-call-header:hover {
  background: #f1f5f9;
}

.tool-call-block.error .tool-call-header:hover {
  background: #fef2f2;
}

.tool-icon {
  width: 16px;
  height: 16px;
  color: #64748b;
  flex-shrink: 0;
}

.tool-icon.success {
  color: #22c55e;
}

.tool-icon.error {
  color: #ef4444;
}

.tool-icon.spinning {
  color: #0ea5e9;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.tool-status-text {
  font-size: 11px;
  font-weight: 400;
  padding: 2px 6px;
  border-radius: 4px;
}

.tool-status-text.executing {
  color: #0ea5e9;
  background: #e0f2fe;
  animation: pulse 1.5s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

.tool-executing-hint {
  padding: 8px 12px;
  font-size: 12px;
  color: #94a3b8;
  font-style: italic;
}

.tool-status-icon {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
}

.tool-status-icon.success {
  color: #22c55e;
}

.tool-status-icon.error {
  color: #ef4444;
}

.tool-name-badge {
  background: #e0f2fe;
  color: #0369a1;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 500;
}

.tool-duration {
  font-size: 11px;
  color: #94a3b8;
  font-weight: 400;
}

.tool-call-body {
  padding: 8px 12px;
  width: 100%;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.tool-section {
  background: #f8fafc;
  border-radius: 8px;
  overflow: hidden;
}

.tool-section-label {
  font-size: 11px;
  font-weight: 600;
  color: #94a3b8;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  padding: 8px 12px 4px;
}

.tool-section-content {
  padding: 4px 12px 10px;
  font-size: 12px;
  color: #475569;
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
}

.tool-section-content.error {
  color: #dc2626;
  background: #fef2f2;
  border-radius: 6px;
  margin: 0 8px 8px;
  padding: 8px;
}

.toggle-arrow {
  width: 14px;
  height: 14px;
  transition: transform 0.2s;
  color: #64748b;
  flex-shrink: 0;
}

.toggle-arrow.rotated {
  transform: rotate(180deg);
}

.tool-calls-block {
  display: flex;
  flex-direction: column;
  background: transparent;
  margin-bottom: 8px;
  width: 100%;
}

.tool-calls-header {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: transparent;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 500;
  color: #475569;
  align-self: flex-start;
}

.tool-calls-header svg {
  width: 16px;
  height: 16px;
  color: #64748b;
}

.tool-calls-list {
  padding: 8px 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}

.tool-call-item {
  background: #f8fafc;
  border-radius: 8px;
  padding: 10px 14px;
}

.tool-name {
  font-weight: 500;
  font-size: 13px;
  color: #0369a1;
  margin-bottom: 6px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.tool-duration {
  font-weight: 400;
  font-size: 11px;
  color: #94a3b8;
  background: #f1f5f9;
  padding: 2px 6px;
  border-radius: 4px;
}

.tool-args {
  font-size: 12px;
  color: #64748b;
  margin-bottom: 6px;
}

.tool-args pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
}

.tool-result {
  font-size: 12px;
  color: #166534;
  background: #f0fdf4;
  padding: 8px 12px;
  border-radius: 6px;
  margin-top: 6px;
}

.tool-result.error {
  color: #dc2626;
  background: #fef2f2;
}

.result-label {
  font-weight: 500;
  margin-right: 4px;
}

.result-content {
  word-break: break-all;
}

.message-text {
  background: transparent;
  padding: 12px 16px;
  border-radius: 16px;
  font-size: 16px;
  line-height: 1.7;
  color: #1e293b;
  word-break: break-word;
  width: 100%;
  box-sizing: border-box;
}

.message-text :deep(pre) {
  background: #f6f8fa;
  color: #24292e;
  padding: 14px;
  border-radius: 8px;
  overflow-x: auto;
  margin: 16px 0;
  border: 1px solid #e1e4e8;
  width: 100%;
  box-sizing: border-box;
}

.message-text :deep(pre code) {
  font-family: 'Fira Code', 'Consolas', 'Monaco', monospace;
  font-size: 13px;
}

.message-text :deep(code) {
  font-family: 'Fira Code', 'Consolas', monospace;
  font-size: 13px;
}

.message-text :deep(p) {
  margin: 20px 0;
}

.message-text :deep(p:first-child) {
  margin-top: 0;
}

.message-text :deep(p:last-child) {
  margin-bottom: 0;
}

.message-text :deep(ul), .message-text :deep(ol) {
  margin: 20px 0;
  padding-left: 28px;
}

.message-text :deep(li) {
  margin: 12px 0;
}

.message-text :deep(blockquote) {
  border-left: 3px solid #0ea5e9;
  margin: 20px 0;
  padding-left: 16px;
  color: #64748b;
}

.message-text :deep(h1),
.message-text :deep(h2),
.message-text :deep(h3),
.message-text :deep(h4),
.message-text :deep(h5),
.message-text :deep(h6) {
  margin: 28px 0 20px 0;
  font-weight: 600;
  line-height: 1.4;
}

.message-text :deep(h1:first-child),
.message-text :deep(h2:first-child),
.message-text :deep(h3:first-child),
.message-text :deep(h4:first-child),
.message-text :deep(h5:first-child),
.message-text :deep(h6:first-child) {
  margin-top: 0;
}

.message-text :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 24px 0;
  font-size: 13px;
}

.message-text :deep(th),
.message-text :deep(td) {
  border: 1px solid #e2e8f0;
  padding: 10px 14px;
  text-align: left;
}

.message-text :deep(th) {
  background: #f1f5f9;
  font-weight: 600;
}

.message-text :deep(tr:nth-child(even)) {
  background: #f8fafc;
}

.message-text :deep(tr:hover) {
  background: #f1f5f9;
}

.message.user .message-text {
  background: #f0f9ff;
  color: #1e293b;
  border-radius: 16px;
}

.message.user .message-text :deep(pre) {
  background: rgba(30, 41, 59, 0.08);
  border: 1px solid rgba(30, 41, 59, 0.15);
}

.message.user .message-text :deep(blockquote) {
  border-left-color: rgba(30, 41, 59, 0.2);
}

.message.user .message-text :deep(table) {
  border-color: rgba(30, 41, 59, 0.15);
}

.message.user .message-text :deep(th),
.message.user .message-text :deep(td) {
  border-color: rgba(30, 41, 59, 0.15);
}

.message.user .message-text :deep(th) {
  background: rgba(30, 41, 59, 0.06);
}

.message.user .message-text :deep(tr:nth-child(even)) {
  background: rgba(30, 41, 59, 0.03);
}

.message.user .message-text :deep(tr:hover) {
  background: rgba(30, 41, 59, 0.06);
}

.message-usage {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 8px;
  padding: 4px 0;
  font-size: 11px;
  color: #94a3b8;
}

.message-usage svg {
  opacity: 0.6;
  flex-shrink: 0;
}

.message-usage .usage-total {
  color: #64748b;
  font-weight: 500;
}

.message-usage .usage-detail {
  color: #94a3b8;
  font-size: 10px;
}

.message-actions {
  visibility: hidden;
  margin-top: 4px;
}

.message.user:hover .message-actions {
  visibility: visible;
  display: flex;
  justify-content: flex-end;
}

.message-actions {
  display: flex;
  gap: 4px;
}

.message-actions .action-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  background: transparent;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.message-actions .action-btn svg {
  width: 14px;
  height: 14px;
  color: #64748b;
}

.message-actions .action-btn:hover {
  background: rgba(14, 165, 233, 0.1);
}

.message-actions .action-btn:hover svg {
  color: #0ea5e9;
}

.message-actions .retry-btn:hover {
  background: rgba(16, 185, 129, 0.1);
}

.message-actions .retry-btn:hover svg {
  color: #10b981;
}

.generated-files-btn-container {
  margin-top: 12px;
  display: flex;
  justify-content: flex-start;
}

.generated-files-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  background: #f0fdf4;
  border: 1px solid #86efac;
  border-radius: 8px;
  color: #166534;
  font-size: 14px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.generated-files-btn:hover {
  background: #dcfce7;
  border-color: #4ade80;
}

.generated-files-btn svg {
  width: 18px;
  height: 18px;
}

.message-text :deep(.code-block-wrapper) {
  position: relative;
  margin: 8px 0;
  width: 100%;
}

.message-text :deep(.code-block-wrapper pre) {
  margin: 0;
  padding: 12px 16px;
  overflow-x: auto;
  border-radius: 0 0 8px 8px;
  background: #f6f8fa !important;
  border: 1px solid #e1e4e8;
  border-top: none;
}

.message-text :deep(.code-block-wrapper pre code) {
  font-family: 'Fira Code', 'Consolas', 'Monaco', monospace;
  font-size: 13px;
  line-height: 1.5;
  color: #24292e;
}

.message-text :deep(.code-block-wrapper .shiki) {
  background: #f6f8fa !important;
  padding: 12px 16px;
  margin: 0;
  border-radius: 0 0 8px 8px;
  overflow-x: auto;
}

.message-text :deep(.code-block-wrapper .shiki code) {
  display: block;
  font-family: 'Fira Code', 'Consolas', 'Monaco', monospace;
  font-size: 13px;
  line-height: 1.5;
}

.message-text :deep(.code-header) {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: #f1f3f5;
  padding: 10px 16px;
  border-radius: 8px 8px 0 0;
  min-height: 40px;
  border: 1px solid #e1e4e8;
  border-bottom: none;
}

.message-text :deep(.code-lang) {
  font-size: 12px;
  color: #57606a;
  font-weight: 500;
  display: flex;
  align-items: center;
}

.message-text :deep(.code-copy-btn) {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 6px 10px;
  background: #ffffff;
  border: 1px solid #d0d7de;
  border-radius: 6px;
  color: #57606a;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.2s;
  height: 28px;
}

.message-text :deep(.code-copy-btn:hover) {
  background: #f3f4f6;
  border-color: #8c959f;
  color: #24292e;
}

.message-text :deep(.code-copy-btn.copied) {
  color: #22c55e;
}

.message-text :deep(.code-copy-btn svg) {
  width: 14px;
  height: 14px;
}

.loading-indicator {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 8px 0;
}

.loading-indicator .dot {
  width: 6px;
  height: 6px;
  background: #94a3b8;
  border-radius: 50%;
  animation: bounce 1.4s infinite ease-in-out both;
}

.loading-indicator .dot:nth-child(1) {
  animation-delay: -0.32s;
}

.loading-indicator .dot:nth-child(2) {
  animation-delay: -0.16s;
}

@keyframes bounce {
  0%, 80%, 100% {
    transform: scale(0);
  }
  40% {
    transform: scale(1);
  }
}

</style>
