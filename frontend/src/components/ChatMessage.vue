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
      
      <!-- 等待响应动画：assistant 消息正在加载且无任何内容 -->
      <div v-if="message.role === 'assistant' && message.loading && !hasAnyContent" class="waiting-animation">
        <div class="waiting-dots">
          <span></span><span></span><span></span>
        </div>
        <span class="waiting-text">正在响应</span>
      </div>

      <!-- 按顺序渲染内容块 -->
      <template v-if="sortedBlocks.length > 0">
        <div v-if="processBlocks.length > 0" class="process-wrapper" :class="{ 'process-active': isProcessActive, 'process-expanded': processExpanded }">
          <div class="process-header" :class="{ 'is-stuck': isStuck }" ref="processHeaderRef" @click="toggleProcess">
            <svg class="process-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2z"></path>
              <path d="M8 14s1.5 2 4 2 4-2 4-2"></path>
              <line x1="9" y1="9" x2="9.01" y2="9"></line>
              <line x1="15" y1="9" x2="15.01" y2="9"></line>
            </svg>
            <span class="process-title">执行过程</span>
            <span class="process-step-count">{{ processStepCount }} 个步骤</span>
            <svg class="toggle-arrow" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" :class="{ rotated: processExpanded }">
              <polyline points="6 9 12 15 18 9"></polyline>
            </svg>
          </div>
          <div v-if="processExpanded" class="process-body">
            <template v-for="(block, idx) in processBlocks" :key="'p'+block.origIndex">
              <!-- 思考内容块 -->
              <div v-if="block.type === 'thinking'" class="thinking-block" :class="{ 'thinking-active': block.duration == null && message.loading }">
                <div class="thinking-header" @click="toggleThinking(block.origIndex)">
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
                  <svg class="toggle-arrow" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" :class="{ rotated: isExpandedThinking(block.origIndex) }">
                    <polyline points="6 9 12 15 18 9"></polyline>
                  </svg>
                </div>
                <div v-if="isExpandedThinking(block.origIndex)" class="thinking-content">
                  <div class="thinking-text" v-html="renderMarkdown(block.content)"></div>
                </div>
              </div>

              <!-- 工具调用块（合并参数、结果、耗时）- 隐藏 write_todos，因为已在侧边栏显示 -->
              <div v-if="block.type === 'tool_call' && block.tool_name !== 'write_todos'" class="tool-call-block" :class="{ error: block.success === false }">
                <div class="tool-call-header" @click="toggleToolCall(block.origIndex)">
                  <svg class="tool-icon" :class="{ success: block.success === true, error: block.success === false, spinning: isToolRunning(block) }" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"></path>
                  </svg>
                  <span class="tool-name-badge">{{ block.tool_name }}</span>
                  <span v-if="block.approval_status" class="approval-badge" :class="'status-' + block.approval_status">
                    <svg v-if="block.approval_status === 'pending'" class="badge-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>
                    <svg v-else-if="block.approval_status === 'approved'" class="badge-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"></polyline></svg>
                    <svg v-else class="badge-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                    <span class="badge-text">{{ block.approval_status === 'pending' ? '待审批' : block.approval_status === 'approved' ? '已批准' : '已拒绝' }}</span>
                  </span>
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
                  <span v-else-if="isToolRunning(block) && !block.approval_status" class="tool-status-text" :class="block.pending_approval ? 'pending' : 'executing'">
                    {{ block.pending_approval ? '等待确认' : '执行中...' }}
                  </span>
                  <svg class="toggle-arrow" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" :class="{ rotated: isExpandedToolCall(block.origIndex) }">
                    <polyline points="6 9 12 15 18 9"></polyline>
                  </svg>
                </div>
                <div v-if="isExpandedToolCall(block.origIndex)" class="tool-call-body">
                  <div v-if="block.pending_approval" class="tool-approval-section">
                    <div class="approval-prompt">
                      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="approval-warning-icon">
                        <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path>
                        <line x1="12" y1="9" x2="12" y2="13"></line>
                        <line x1="12" y1="17" x2="12.01" y2="17"></line>
                      </svg>
                      <span>此操作将删除文件，需要您的确认</span>
                    </div>
                    <div v-if="block.file_paths && block.file_paths.length > 0" class="approval-file-list">
                      <div v-for="(fp, fpi) in block.file_paths" :key="fpi" class="approval-file-item">
                        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="approval-file-icon">
                          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
                          <polyline points="14 2 14 8 20 8"></polyline>
                        </svg>
                        <span class="approval-file-path">{{ fp }}</span>
                      </div>
                    </div>
                    <div class="approval-buttons">
                      <button class="approval-btn approve" @click="emit('approve')">
                        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                          <polyline points="20 6 9 17 4 12"></polyline>
                        </svg>
                        批准
                      </button>
                      <button class="approval-btn reject" @click="emit('reject')">
                        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                          <line x1="18" y1="6" x2="6" y2="18"></line>
                          <line x1="6" y1="6" x2="18" y2="18"></line>
                        </svg>
                        拒绝
                      </button>
                    </div>
                  </div>
                  <div v-if="hasArgs(block.arguments) && !block.pending_approval" class="tool-section">
                    <div class="tool-section-label">参数</div>
                    <pre class="tool-section-content">{{ truncateResult(formatJson(block.arguments), 1000) }}</pre>
                  </div>
                  <div v-if="block.result" class="tool-section">
                    <div class="tool-section-label">结果</div>
                    <pre class="tool-section-content" :class="{ error: block.success === false }">{{ truncateResult(block.result, 1000) }}</pre>
                  </div>
                  <div v-else-if="isToolRunning(block) && !block.pending_approval" class="tool-section">
                    <div class="tool-section-label">结果</div>
                    <div class="tool-executing-hint">等待执行结果...</div>
                  </div>
                </div>
              </div>

              <!-- 中间穿插的正文（其后仍有思考/工具）放进处理过程内部按原序展示 -->
              <div v-if="block.type === 'content'" class="message-text process-inline-content" v-html="renderMarkdown(block.content)"></div>
            </template>
          </div>
        </div>
        <template v-for="(block, idx) in finalContentBlocks" :key="'c'+block.origIndex">
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

        <div v-if="message.tool_calls && message.tool_calls.filter(t => t.tool_name !== 'write_todos').length > 0" class="tool-calls-block">
          <div class="tool-calls-header">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"></path>
            </svg>
            <span>工具调用</span>
          </div>
          <div class="tool-calls-list">
            <div v-for="(tool, idx) in message.tool_calls.filter(t => t.tool_name !== 'write_todos')" :key="idx" class="tool-call-item">
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

        <!-- 错误提示 -->
        <div v-if="message.error" class="message-error">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="error-icon">
            <circle cx="12" cy="12" r="10"></circle>
            <line x1="12" y1="8" x2="12" y2="12"></line>
            <line x1="12" y1="16" x2="12.01" y2="16"></line>
          </svg>
          <span>{{ message.error }}</span>
        </div>

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
import { ref, computed, onMounted, shallowRef, watch, nextTick, onBeforeUnmount } from 'vue'
import { createHighlighter } from 'shiki'
import { marked } from 'marked'
import { setupMarkedExtensions, normalizeMathDelimiters } from '../markdownSetup.js'
import FileIcon from './FileIcon.vue'

// 注册 KaTeX 数学公式 + emoji 短代码扩展（幂等，仅执行一次）
setupMarkedExtensions()

const props = defineProps({
  message: {
    type: Object,
    required: true
  }
})

const emit = defineEmits(['removeFile', 'viewGeneratedFiles', 'retry', 'approve', 'reject'])

const showThinking = ref(false)
const expandedThinking = ref({})
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
  expandedTool.value = {}
})

// 判断 assistant 消息是否有任何可见内容
const hasAnyContent = computed(() => {
  const m = props.message
  if (m.blocks && m.blocks.length > 0) return true
  if (m.thinking) return true
  if (m.content) return true
  if (m.tool_calls && m.tool_calls.length > 0) return true
  return false
})

const sortedBlocks = computed(() => {
  // 从 blocks 字段构建（优先使用）
  if (props.message.blocks && props.message.blocks.length > 0) {
    // 严格按大模型返回顺序（创建顺序 order）展示，不做按 step/类型的二次重排：
    // 旧数据 content 块无 step，按 step 重排会把它排到顶部；按 order 原序则正文
    //（创建最晚、order 最大）自然落在最后。同一 step「先思考后工具」由创建顺序保证
    //（reopen 时思考晚于工具创建的极端情况，已在 addBlock 中用更小的 order 纠正）。
    const sorted = [...props.message.blocks].sort((a, b) => (a.order || 0) - (b.order || 0))
    // 兼容数据：blocks 存在但没有 content 类型的 block 时，从 message.content 补充。
    // 正文是模型最终输出的回答，应排在所有思考/工具块之后（与实时流式中 content
    // 事件最后到达的顺序一致）；原先插到首个思考块之前，会导致历史会话正文显示在最顶部。
    const hasContentBlock = sorted.some(b => b.type === 'content')
    if (!hasContentBlock && props.message.content) {
      sorted.push({
        type: 'content',
        content: props.message.content,
        order: sorted.length
      })
      // 重新排列 order
      sorted.forEach((b, i) => { b.order = i })
    }
    // 防御性合并：将同一 step 的思考块合并为一张「思考过程」卡片。
    // 即使流式过程中因事件时序/乱序产生了重复思考块，也能保证一个 step 的思考内容
    // 渲染为单张卡片，而非被拆成「我是」「大模型」等多段。
    const merged = []
    const thinkingByStep = new Map()
    for (const b of sorted) {
      if (b.type === 'thinking') {
        const key = (b.step === undefined || b.step === null) ? '__nostep__' : b.step
        const prev = thinkingByStep.get(key)
        if (prev) {
          prev.content = (prev.content || '') + (b.content || '')
          if (b.duration != null) prev.duration = b.duration
          continue
        }
        const clone = { ...b }
        thinkingByStep.set(key, clone)
        merged.push(clone)
        continue
      }
      merged.push(b)
    }
    return merged
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
      const tcArgs = tool.arguments && typeof tool.arguments === 'object' && Object.keys(tool.arguments).length > 0
        ? tool.arguments
        : {}
      blocks.push({
        type: 'tool_call',
        tool_name: tool.tool_name,
        arguments: tcArgs,
        result: tool.result || '',
        success: tool.success !== false,
        duration: tool.duration,
        step: tool.step || 0,
        approval_status: tool.approval_status || undefined,
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

// 处理过程：思考 + 工具调用（排除已在侧边栏显示的 write_todos）。
// 正文统一由 finalContentBlocks 完整渲染，不再作为「中间穿插」放进执行过程——
// 后端按模型 turn 把正文拆成多个 content 块，若只取最后一块，被工具调用隔开的
// 表格/代码片段会丢失；拼接所有块才能保证实时与历史渲染一致。
// origIndex 保留在 sortedBlocks 中的原始下标，供折叠状态函数定位 block。
const _isProcessType = (b) =>
  b.type === 'thinking' || (b.type === 'tool_call' && b.tool_name !== 'write_todos')

// 最终正文：仅当消息完成（非流式且无待审批 HITL）时，取 order 最大的一段 content
// 展示在处理过程之后；思考/工具执行过程中到达的中间正文按返回顺序渲染在执行过程
// 内部（process-inline-content），不混入最终正文区。
const finalContentBlocks = computed(() => {
  const contents = sortedBlocks.value
    .map((b, i) => ({ ...b, origIndex: i }))
    .filter((b) => b.type === 'content')
  if (contents.length === 0) return []
  contents.sort((a, b) => (a.order || 0) - (b.order || 0))
  // 有思考/工具执行过程时：流式/HITL 期间中间正文留在执行过程内部按序展示，
  // 完成后才把最后一段正文移到过程之后；
  // 无执行过程（纯正文回复）时：流式中也要实时显示在过程外。
  const hasProcess = sortedBlocks.value.some(_isProcessType)
  if (!isMessageFinished.value && hasProcess) return []
  return [contents[contents.length - 1]]
})

const processBlocks = computed(() => {
  const finalOrigIndex = finalContentBlocks.value[0]?.origIndex
  const result = []
  sortedBlocks.value.forEach((b, i) => {
    if (_isProcessType(b)) {
      result.push({ ...b, origIndex: i })
      return
    }
    // 非最终正文的 content -> 中间穿插，按返回顺序纳入执行过程内部展示
    if (b.type === 'content' && i !== finalOrigIndex) {
      result.push({ ...b, origIndex: i })
    }
  })
  return result
})

// 消息是否已真正完成：非流式中（loading=false）且无待审批（pending_approval）
const isMessageFinished = computed(() =>
  !props.message.loading && !props.message.pending_approval
)

// 「步骤数」仅统计思考与工具调用，不含穿插的正文。
const processStepCount = computed(() =>
  processBlocks.value.filter((b) => _isProcessType(b)).length
)

// 处理是否仍在进行：消息仍在流式（loading）即视为处理中，扫光动画保持；流式结束
// （loading=false）动画消失。与正文位置解耦，避免正文一出现动画就关、后续工具仍在
// 跑却无动画提示的问题。
const isProcessActive = computed(() => !!props.message.loading)

// 处理过程默认折叠（含实时会话），由用户手动展开/折叠；新过程到达不自动展开，
// 避免打断用户已收起的查看状态。
const processExpanded = ref(false)

// 流式期间只要出现穿插正文（思考/工具之间的中间正文），自动展开执行过程，
// 让中间正文按返回顺序可见；完成后保持用户手动展开/收起的状态。
watch(
  () => [
    props.message.loading,
    processBlocks.value.some((b) => b.type === 'content'),
  ],
  ([loading, hasInlineContent]) => {
    if (loading && hasInlineContent && !processExpanded.value) {
      processExpanded.value = true
    }
  },
  { immediate: true }
)

// 出现待审批的工具调用（HITL）时自动展开，便于用户查看审批提示。
const hasPendingApproval = computed(() =>
  processBlocks.value.some(b => b.type === 'tool_call' && b.approval_status === 'pending')
)
watch(() => hasPendingApproval.value, (pending) => {
  if (pending) processExpanded.value = true
})

function toggleProcess() {
  processExpanded.value = !processExpanded.value
}

// 执行过程头部冻结（sticky 卡住）检测：卡住时补绘顶边框——wrapper 顶边框已随滚动
// 移出可视区，用 is-stuck 类驱动 header 伪元素重绘一条与 wrapper 圆角一致的顶边框。
const processHeaderRef = ref(null)
const isStuck = ref(false)
let _scrollEl = null
let _onScroll = null

function updateStuck() {
  const header = processHeaderRef.value
  if (!header || !processExpanded.value) { isStuck.value = false; return }
  const wrapper = header.parentElement
  if (!wrapper) { isStuck.value = false; return }
  // 卡住时 header 钉在可视区顶部、wrapper 顶边随滚动上移，二者顶边差增大；
  // 正常流中 header 紧贴 wrapper 顶边（差≈边框 1px）。比较二者不依赖具体吸附点。
  isStuck.value = header.getBoundingClientRect().top - wrapper.getBoundingClientRect().top > 2
}

watch(processExpanded, (expanded) => {
  isStuck.value = false
  nextTick(() => {
    if (expanded) {
      if (processHeaderRef.value) _scrollEl = processHeaderRef.value.closest('.chat-messages')
      if (_scrollEl) {
        if (!_onScroll) {
          _onScroll = () => updateStuck()
          _scrollEl.addEventListener('scroll', _onScroll, { passive: true })
        }
        updateStuck()
      }
    } else {
      if (_scrollEl && _onScroll) {
        _scrollEl.removeEventListener('scroll', _onScroll)
        _onScroll = null
      }
      _scrollEl = null
    }
  })
})

// 展开时内容增长（流式）会改变布局，重新判定冻结状态
watch(() => processBlocks.value.length, () => {
  if (processExpanded.value) nextTick(updateStuck)
})

onBeforeUnmount(() => {
  if (_scrollEl && _onScroll) _scrollEl.removeEventListener('scroll', _onScroll)
})

// 使用 block 的唯一标识来跟踪展开状态
function getBlockKey(block, index) {
  return block.id || `${block.type}-${block.step || 0}-${block.tool_name || ''}-${index}`
}

function isExpandedThinking(index) {
  const block = sortedBlocks.value[index]
  if (!block) return false
  // Default collapsed: only expand if explicitly toggled open
  const key = getBlockKey(block, index)
  return expandedThinking.value[key] === true
}

function toggleThinking(index) {
  const block = sortedBlocks.value[index]
  if (!block) return
  const key = getBlockKey(block, index)
  expandedThinking.value[key] = !expandedThinking.value[key]
}



function isToolRunning(block) {
  // Tool is still running if: no duration AND the message is still loading
  return block.duration == null && props.message.loading === true
}

function hasArgs(args) {
  if (!args) return false
  if (typeof args === 'string') return args.trim().length > 0
  if (typeof args === 'object') return Object.keys(args).length > 0
  return false
}

function toggleToolCall(index) {
  const block = sortedBlocks.value[index]
  if (!block) return
  if (block.pending_approval) return
  const key = getBlockKey(block, index)
  expandedTool.value[key] = !expandedTool.value[key]
}

function isExpandedToolCall(index) {
  const block = sortedBlocks.value[index]
  if (!block) return false
  if (block.pending_approval) return true
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
    const normalized = normalizeMathDelimiters(content)
    return marked.parse(normalized, { renderer, breaks: true, gfm: true })
  } catch (e) {
    console.error('Markdown 渲染失败:', e)
    return escapeHtml(content)
  }
}

function formatJson(obj) {
  try {
    if (typeof obj === 'string') {
      try {
        const parsed = JSON.parse(obj)
        return JSON.stringify(parsed, null, 2)
      } catch {
        return obj
      }
    }
    if (obj && typeof obj === 'object') {
      if ('raw' in obj && Object.keys(obj).length === 1) {
        try {
          const parsed = JSON.parse(obj.raw)
          return JSON.stringify(parsed, null, 2)
        } catch {
          return obj.raw
        }
      }
      if (Object.keys(obj).length === 0) return ''
      return JSON.stringify(obj, null, 2)
    }
    return String(obj)
  } catch (e) {
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

// 兼容历史脏数据：早期版本可能把注入给模型的工作区前缀/记忆上下文一并存进
// 用户消息，复制时应剔除这些内部前缀，只复制用户真正输入的需求内容。
function cleanUserContent(content) {
  let text = String(content == null ? '' : content)
  const lines = text.split('\n')
  const wsIdx = lines.findIndex(l => /^\[workspace: .*shell: cd .*\]$/.test(l.trim()))
  if (wsIdx !== -1) {
    // 去掉 [workspace: ...] 标记行及前面的记忆/上下文前缀
    text = lines.slice(wsIdx + 1).join('\n')
  }
  return text.replace(/^\s+/, '').replace(/\s+$/, '')
}

// 统一复制入口：优先 Clipboard API，失败时降级为隐藏 textarea + execCommand，
// 兼容非 HTTPS（LAN IP 访问）等 Clipboard API 不可用的场景。
async function copyTextToClipboard(text) {
  const content = String(text == null ? '' : text)
  if (!content) return false
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(content)
      return true
    }
  } catch (e) {
    console.warn('Clipboard API 不可用，降级复制:', e)
  }
  try {
    const textarea = document.createElement('textarea')
    textarea.value = content
    textarea.setAttribute('readonly', '')
    textarea.style.position = 'fixed'
    textarea.style.top = '-9999px'
    document.body.appendChild(textarea)
    textarea.select()
    textarea.setSelectionRange(0, content.length)
    const ok = document.execCommand('copy')
    document.body.removeChild(textarea)
    return ok
  } catch (e) {
    console.error('复制失败:', e)
    return false
  }
}

async function copyMessage(ev) {
  if (!props.message.content) return

  const text = cleanUserContent(props.message.content)
  try {
    const ok = await copyTextToClipboard(text)
    const btn = ev?.currentTarget
    if (ok && btn) {
      const original = btn.title
      btn.title = '已复制'
      setTimeout(() => { btn.title = original }, 1500)
    }
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

// 代码块复制：markdown 渲染产物通过 v-html 注入，按钮用 inline onclick 调全局函数。
// 定义在模块级（而非 onMounted），保证任何渲染时机点击都能找到该函数。
window.copyCode = async function(btn) {
  const wrapper = btn.closest('.code-block-wrapper')
  const codeEl = wrapper.querySelector('pre code') || wrapper.querySelector('pre')
  const code = codeEl?.textContent || ''

  const span = btn.querySelector('span')
  const originalText = span ? span.textContent : ''
  const ok = await copyTextToClipboard(code)
  if (span) {
    span.textContent = ok ? '已复制!' : '复制失败'
    btn.classList.add(ok ? 'copied' : 'copy-error')
    setTimeout(() => {
      span.textContent = originalText
      btn.classList.remove('copied', 'copy-error')
    }, 2000)
  }
}
</script>

<style scoped>
/* 处理过程折叠容器：包裹一轮会话中的所有思考 + 工具调用，正文展示在其后 */
.process-wrapper {
  /* message-content 为 align-items: flex-start（子项不拉伸），需显式撑满，
     使折叠（仅 header）与展开（含 body）时宽度一致，且与输入框对齐。
     不能使用 overflow: hidden：否则它会成为 .process-header position:sticky
     的滚动祖先（粘性上下文），导致头部无法相对 .chat-messages 冻结。
     圆角裁剪改由 header/body 各自的 border-radius 承担。 */
  width: 100%;
  margin: 6px 0 10px;
  border: 1px solid var(--border-color, #e5e7eb);
  border-radius: 10px;
  background: var(--bg-secondary, #f9fafb);
}
.process-wrapper.process-active {
  border-color: #c7d2fe;
}
.process-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 14px;
  cursor: pointer;
  user-select: none;
  font-size: 13px;
  color: var(--text-secondary, #64748b);
  transition: background 0.15s;
  /* 显式不透明背景：sticky 冻结时遮挡其后滚动的流程内容；
     折叠态圆角与外层 wrapper 一致。 */
  background: var(--bg-secondary, #f9fafb);
  border-radius: 10px;
}
/* 展开态：头部冻结在可视区顶部，折叠按钮始终可达；
   仅顶部圆角与 body 的底部圆角拼合 wrapper 的圆角。 */
.process-wrapper.process-expanded .process-header {
  position: sticky;
  top: 0;
  z-index: 5;
  border-radius: 10px 10px 0 0;
}
/* 冻结（sticky 卡住）时补绘执行过程顶边框：wrapper 顶边框已随滚动移出可视区，
   用 header 伪元素在可视区顶部重绘一条与 wrapper 圆角一致的顶边框，并与左右边框相接。 */
.process-wrapper.process-expanded .process-header.is-stuck::before {
  content: '';
  position: absolute;
  left: -1px;
  right: -1px;
  top: 0;
  height: 0;
  border-top: 1px solid var(--border-color, #e5e7eb);
  border-top-left-radius: 10px;
  border-top-right-radius: 10px;
  pointer-events: none;
}
.process-header:hover {
  background: var(--bg-tertiary, #f1f5f9);
}
.process-icon {
  width: 16px;
  height: 16px;
  color: #94a3b8;
  flex-shrink: 0;
}
.process-title {
  font-weight: 500;
  color: var(--text-secondary, #475569);
}
/* 处理进行中：标题「执行过程」文字扫光。
   激活态文字用靛蓝色（与未激活的灰色拉开差异），其上一道更亮的高光从左到右循环
   扫过，扫光更明细且文字始终清晰可读。 */
.process-wrapper.process-active .process-title {
  background: linear-gradient(
    100deg,
    #6366f1 0%,
    #6366f1 42%,
    #c7d2fe 50%,
    #6366f1 58%,
    #6366f1 100%
  );
  background-size: 200% 100%;
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  color: transparent;
  animation: process-shimmer 2.2s linear infinite;
}
@keyframes process-shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
.process-step-count {
  font-size: 11px;
  color: #94a3b8;
}
.process-header .toggle-arrow {
  margin-left: auto;
}
.process-body {
  padding: 4px 10px 10px;
  border-top: 1px solid var(--border-color, #e5e7eb);
  /* 补偿 wrapper 移除的 overflow:hidden：裁剪内部子项背景到圆角。
     body 是 header 的兄弟而非祖先，其 overflow 不影响 header 的 sticky。 */
  overflow: hidden;
  border-radius: 0 0 10px 10px;
}
.process-body .thinking-block,
.process-body .tool-call-block {
  margin: 6px 0;
}
.process-body .process-inline-content {
  margin: 6px 0;
  padding: 8px 12px;
  border-left: 3px solid var(--border-color, #e5e7eb);
  background: var(--process-inline-bg, rgba(0, 0, 0, 0.02));
  border-radius: 0 6px 6px 0;
  font-size: 14px;
}
html[data-theme="dark"] .process-body .process-inline-content {
  background: var(--process-inline-bg, rgba(255, 255, 255, 0.04));
}
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
  gap: 2px;
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















.thinking-block {
  display: flex;
  flex-direction: column;
  background: transparent;
  width: 100%;
}

.thinking-header {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 4px 10px;
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
  padding: 8px 12px;
  font-size: 15px;
  color: #475569;
  line-height: 1.7;
  width: calc(100% - 24px);
  margin: 0 12px;
  box-sizing: border-box;
  background: #f8fafc;
  border-radius: 8px;
  overflow: hidden;
}

.thinking-text {
  color: #64748b;
  padding: 4px 0 8px;
  max-height: 220px;
  overflow-y: auto;
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

.thinking-text :deep(.code-header) {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: #f1f3f5;
  padding: 8px 12px;
  border-bottom: 1px solid #e1e4e8;
}

.thinking-text :deep(.code-lang) {
  font-size: 11px;
  color: #57606a;
  font-weight: 500;
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

.thinking-text :deep(.code-copy-btn) {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 4px 8px;
  background: #ffffff;
  border: 1px solid #d0d7de;
  border-radius: 4px;
  color: #57606a;
  font-size: 11px;
  cursor: pointer;
  transition: all 0.2s;
  height: 24px;
}

.thinking-text :deep(.code-copy-btn:hover) {
  background: #f3f4f6;
  border-color: #8c959f;
  color: #24292e;
}

.thinking-text :deep(.code-copy-btn svg) {
  width: 12px;
  height: 12px;
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
  width: 100%;
}

.tool-call-block.error .tool-call-header {
  color: #dc2626;
}

.tool-call-header {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 4px 10px;
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

.tool-status-text.pending {
  color: #d97706;
  background: #fef3c7;
  animation: pulse 1.5s ease-in-out infinite;
}

/* HITL 审批状态徽章（待审批 / 已批准 / 已拒绝） */
.approval-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 500;
  line-height: 1.4;
}
.approval-badge .badge-icon {
  width: 12px;
  height: 12px;
  flex-shrink: 0;
}
.approval-badge.status-pending {
  color: #d97706;
  background: #fef3c7;
}
.approval-badge.status-approved {
  color: #16a34a;
  background: #dcfce7;
}
.approval-badge.status-rejected {
  color: #dc2626;
  background: #fee2e2;
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

.tool-approval-section {
  background: #fffbeb;
  border: 1px solid #fcd34d;
  border-radius: 8px;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.approval-prompt {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #92400e;
  font-weight: 500;
}

.approval-warning-icon {
  width: 18px;
  height: 18px;
  color: #f59e0b;
  flex-shrink: 0;
}

.approval-file-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 6px 8px;
  background: rgba(146, 64, 14, 0.06);
  border-radius: 6px;
  border: 1px solid rgba(245, 158, 11, 0.2);
}

.approval-file-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #78350f;
  word-break: break-all;
}

.approval-file-icon {
  width: 14px;
  height: 14px;
  color: #b45309;
  flex-shrink: 0;
}

.approval-file-path {
  font-family: 'Fira Code', Consolas, monospace;
  font-size: 12px;
}

.approval-buttons {
  display: flex;
  gap: 8px;
}

.approval-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 16px;
  border: none;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s ease;
}

.approval-btn svg {
  width: 14px;
  height: 14px;
}

.approval-btn.approve {
  background: #16a34a;
  color: #ffffff;
}

.approval-btn.approve:hover {
  background: #15803d;
}

.approval-btn.reject {
  background: #dc2626;
  color: #ffffff;
}

.approval-btn.reject:hover {
  background: #b91c1c;
}

html[data-theme="dark"] .tool-approval-section {
  background: #422006;
  border-color: #a16207;
}

html[data-theme="dark"] .approval-prompt {
  color: #fbbf24;
}

html[data-theme="dark"] .approval-warning-icon {
  color: #f59e0b;
}

html[data-theme="dark"] .approval-file-list {
  background: rgba(251, 191, 36, 0.08);
  border-color: rgba(161, 98, 7, 0.4);
}

html[data-theme="dark"] .approval-file-item {
  color: #fde68a;
}

html[data-theme="dark"] .approval-file-icon {
  color: #fbbf24;
}

html[data-theme="dark"] .tool-status-text.pending {
  color: #fbbf24;
  background: #78350f;
}

html[data-theme="dark"] .approval-badge.status-pending {
  color: #fbbf24;
  background: #78350f;
}
html[data-theme="dark"] .approval-badge.status-approved {
  color: #4ade80;
  background: #14532d;
}
html[data-theme="dark"] .approval-badge.status-rejected {
  color: #f87171;
  background: #7f1d1d;
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
  max-height: 220px;
  overflow-y: auto;
}

.tool-section-content.error {
  color: #dc2626;
  background: transparent;
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
  background: transparent;
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
  max-height: 220px;
  overflow-y: auto;
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

/* GitHub 风格 emoji 短代码渲染后的 unicode 字符 */
.message-text :deep(.github-emoji) {
  display: inline;
  vertical-align: -0.125em;
  font-size: 1.1em;
  line-height: 1;
}

/* KaTeX 数学公式块级与行内展示 */
.message-text :deep(.katex) {
  font-size: 1.05em;
  /* 行内公式作为一个整体，避免被 word-break 从中间断开 */
  white-space: nowrap;
}

.message-text :deep(.katex-display) {
  margin: 12px 0;
  padding: 4px 0;
  max-width: 100%;
  /* 不显示滚动条：公式完整渲染，超出气泡宽度时直接溢出而非裁切/滚动 */
  overflow: visible;
}

/* 视口较窄时自动缩小块级公式，尽量避免溢出气泡（用 @media 而非 @container，避免影响布局） */
@media (max-width: 640px) {
  .message-text :deep(.katex-display) {
    font-size: 0.9em;
  }
}

@media (max-width: 520px) {
  .message-text :deep(.katex-display) {
    font-size: 0.78em;
  }
}

@media (max-width: 400px) {
  .message-text :deep(.katex-display) {
    font-size: 0.66em;
  }
}

.message-error {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px 14px;
  margin: 4px 16px;
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: 10px;
  color: #dc2626;
  font-size: 14px;
  line-height: 1.5;
  word-break: break-word;
}

.message-error .error-icon {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
  margin-top: 1px;
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
  border-radius: 18px 18px 4px 18px;
  border: none;
  padding: 12px 18px;
  box-shadow: none;
  max-width: 85%;
  font-family: 'Microsoft YaHei', '微软雅黑', sans-serif;
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

/* ========== 黑色主题：代码块 ========== */
/* shiki 用 github-light 主题生成内联白色背景的 HTML，
   dark 主题下需强制覆盖，否则代码块背景/边框仍为白色 */
html[data-theme="dark"] .message-text :deep(.code-block-wrapper pre) {
  background: transparent !important;
  border-color: #30363d !important;
}

html[data-theme="dark"] .message-text :deep(.code-block-wrapper pre code) {
  color: #c9d1d9;
}

html[data-theme="dark"] .message-text :deep(.code-block-wrapper .shiki) {
  background: transparent !important;
}

html[data-theme="dark"] .message-text :deep(.code-block-wrapper .shiki code) {
  color: #c9d1d9;
}

html[data-theme="dark"] .message-text :deep(.code-header) {
  background: #161b22;
  border-color: #30363d;
}

html[data-theme="dark"] .message-text :deep(.code-lang) {
  color: #8b949e;
}

html[data-theme="dark"] .message-text :deep(.code-copy-btn) {
  background: #21262d;
  border-color: #30363d;
  color: #8b949e;
}

html[data-theme="dark"] .message-text :deep(.code-copy-btn:hover) {
  background: #30363d;
  border-color: #8b949e;
  color: #c9d1d9;
}

/* 思考过程中的代码块：全局深色规则会把 pre/code 统一成灰底，
   这里与正文代码块保持一致（深色 GitHub 风格），避免显示灰色/白色底。 */
html[data-theme="dark"] .thinking-text :deep(.code-block-wrapper) {
  background: transparent;
  border-color: #30363d;
}

html[data-theme="dark"] .thinking-text :deep(.code-block-wrapper pre),
html[data-theme="dark"] .thinking-text :deep(.code-block-wrapper pre code) {
  background: transparent !important;
  color: #c9d1d9;
}

html[data-theme="dark"] .thinking-text :deep(.code-block-wrapper .shiki),
html[data-theme="dark"] .thinking-text :deep(.code-block-wrapper .shiki code) {
  background: transparent !important;
  color: #c9d1d9;
}

html[data-theme="dark"] .thinking-text :deep(.code-header) {
  background: #161b22;
  border-color: #30363d;
}

html[data-theme="dark"] .thinking-text :deep(.code-lang) {
  color: #8b949e;
}

html[data-theme="dark"] .thinking-text :deep(.code-copy-btn) {
  background: #21262d;
  border-color: #30363d;
  color: #8b949e;
}

html[data-theme="dark"] .thinking-text :deep(.code-copy-btn:hover) {
  background: #30363d;
  border-color: #8b949e;
  color: #c9d1d9;
}

/* 行内代码深色适配 */
html[data-theme="dark"] .message-text :deep(code),
html[data-theme="dark"] .thinking-text :deep(code) {
  background: #30363d !important;
  color: #c9d1d9 !important;
}

/* ========== 用户气泡 / 文件卡片 / 表格 / 工具块 深色适配 ========== */
html[data-theme="dark"] .message.user .message-text {
  background: var(--bg-tertiary) !important;
  color: var(--text-primary) !important;
  border-color: var(--border-color) !important;
}

html[data-theme="dark"] .file-card {
  background: var(--bg-tertiary) !important;
  border-color: var(--border-color) !important;
}
html[data-theme="dark"] .file-name {
  color: var(--text-primary) !important;
}
html[data-theme="dark"] .remove-file-btn {
  background: var(--bg-secondary) !important;
}
html[data-theme="dark"] .remove-file-btn svg {
  color: var(--text-secondary) !important;
}

html[data-theme="dark"] .message-text :deep(th),
html[data-theme="dark"] .message-text :deep(td) {
  border-color: var(--border-color) !important;
}
html[data-theme="dark"] .message-text :deep(th) {
  background: var(--bg-tertiary) !important;
}
html[data-theme="dark"] .message-text :deep(tr:nth-child(even)) {
  background: var(--bg-tertiary) !important;
}
html[data-theme="dark"] .message-text :deep(tr:hover) {
  background: var(--bg-secondary) !important;
}

html[data-theme="dark"] .tool-name-badge {
  background: color-mix(in srgb, var(--accent-color) 20%, transparent) !important;
  color: var(--accent-color) !important;
}
html[data-theme="dark"] .tool-section {
  background: var(--bg-tertiary) !important;
}
html[data-theme="dark"] .tool-status-text.executing {
  color: #38bdf8 !important;
  background: rgba(56, 189, 248, 0.15) !important;
}

.waiting-animation {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 0;
}

.waiting-dots {
  display: flex;
  align-items: center;
  gap: 5px;
}

.waiting-dots span {
  width: 8px;
  height: 8px;
  background: #7c6aef;
  border-radius: 50%;
  animation: waiting-bounce 1.4s infinite ease-in-out both;
}

.waiting-dots span:nth-child(1) { animation-delay: -0.32s; }
.waiting-dots span:nth-child(2) { animation-delay: -0.16s; }

@keyframes waiting-bounce {
  0%, 80%, 100% { transform: scale(0); }
  40% { transform: scale(1); }
}

.waiting-text {
  font-size: 13px;
  color: #6b7280;
  animation: waiting-fade 2s infinite;
}

@keyframes waiting-fade {
  0%, 100% { opacity: 0.5; }
  50% { opacity: 1; }
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

/* 自定义滚动条样式 - 思考内容和工具结果区域 */
.thinking-content::-webkit-scrollbar,
.tool-section-content::-webkit-scrollbar,
.tool-result::-webkit-scrollbar {
  width: 5px;
}

.thinking-content::-webkit-scrollbar-track,
.tool-section-content::-webkit-scrollbar-track,
.tool-result::-webkit-scrollbar-track {
  background: transparent;
}

.thinking-content::-webkit-scrollbar-thumb,
.tool-section-content::-webkit-scrollbar-thumb,
.tool-result::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 3px;
}

.thinking-content::-webkit-scrollbar-thumb:hover,
.tool-section-content::-webkit-scrollbar-thumb:hover,
.tool-result::-webkit-scrollbar-thumb:hover {
  background: #94a3b8;
}

</style>
