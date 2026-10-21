<template>
  <div class="message" :class="[message.role]">
    <div class="message-content" @click="handleContentClick">
      <!-- AI 侧边栏发起的消息：展示随消息附带的页面上下文 -->
      <div
        v-if="message.role === 'user' && message.page_context"
        class="page-context-chip"
        :title="message.page_context"
      >
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"></path>
          <circle cx="12" cy="10" r="3"></circle>
        </svg>
        <span>{{ pageContextText }}</span>
      </div>
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
            <span class="file-name" :title="file.filename">{{ file.filename }}</span>
            <div class="file-meta">
              <span class="file-size">{{ formatSize(file.size) }}</span>
              <span class="file-type">{{ getFileExtension(file.filename) }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 专家标识：选中专家回答时，在回答面板左上角展示「头像 + 名称」（发送瞬间即出现，
           常驻到消息结束）；未选专家时无此元素，沿用通用样式。
           仅 assistant 消息渲染：用户消息落库后也带 expert 字段（标识的数据来源），
           这里用角色判断避免标识跑到用户气泡上。
           放在等待动画之前：加载中（正在响应）也显示在标识下方 -->
      <div v-if="message.role === 'assistant' && expertInfo" class="expert-badge">
        <span class="expert-badge-avatar" :class="expertAvatarClass(expertInfo)">{{ expertAvatarChar(expertInfo) }}</span>
        <span class="expert-badge-name">{{ expertInfo.name }}</span>
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
            <template v-for="(block, idx) in processBlocks">
              <!-- 思考内容块 -->
              <div v-if="block.type === 'thinking'" :key="'p'+block.origIndex" class="thinking-block" :class="{ 'thinking-active': block.duration == null && message.loading }">
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
                  <MarkdownBody class="thinking-text" :content="block.content" :cite-map="citeNumberMap" />
                </div>
              </div>

              <!-- 工具调用卡片（dsh 风格，默认收起；隐藏 write_todos）-->
              <ToolCallCard
                v-if="block.type === 'tool_call' && block.tool_name !== 'write_todos'"
                :key="'p'+block.origIndex"
                :tool-name="block.tool_name"
                :args="block.arguments"
                :result="block.result"
                :success="block.success !== false"
                :duration="block.duration"
                :pending-approval="!!block.pending_approval"
                :file-paths="block.file_paths || []"
                :allowed-decisions="block.allowed_decisions || []"
                :streaming="!!block.streaming"
                @approve="$emit('approve', approvalTarget(block))"
                @reject="$emit('reject', approvalTarget(block))"
                @edit="$emit('edit', Object.assign(approvalTarget(block), { editedArgs: $event }))"
              />

              <!-- 中间穿插的正文（其后仍有思考/工具）放进处理过程内部按原序展示 -->
              <MarkdownBody v-if="block.type === 'content'" :key="'p'+block.origIndex" class="message-text process-inline-content" :content="block.content" :cite-map="citeNumberMap" />
            </template>
          </div>
        </div>
        <!-- 子代理内部触发的审批：主消息里没有对应工具卡片，单独列出（每项只回答自己的中断） -->
        <div v-if="pendingPanelActions.length > 0" class="pending-approval-panel">
          <ToolCallCard
            v-for="(ar, i) in pendingPanelActions"
            :key="'hitl-' + (ar.interrupt_id || 'legacy') + '-' + i"
            :tool-name="ar.tool_name"
            :args="ar.arguments"
            :pending-approval="true"
            :file-paths="ar.file_paths || []"
            :allowed-decisions="ar.allowed_decisions || []"
            @approve="$emit('approve', { interruptId: ar.interrupt_id || null })"
            @reject="$emit('reject', { interruptId: ar.interrupt_id || null })"
            @edit="$emit('edit', { interruptId: ar.interrupt_id || null, actionIndex: ar.action_index != null ? ar.action_index : 0, actionName: ar.name || ar.tool_name || '', editedArgs: $event })"
          />
        </div>
        <template v-for="(block, idx) in finalContentBlocks">
          <!-- 用户消息：技能标签与正文放在同一个气泡内横向排列（标签在左、正文在右），
               避免标签独占一行；正文里的 /技能名 指令已去掉，由标签承载 -->
          <div
            v-if="block.type === 'content' && message.role === 'user'"
            :key="'cu'+block.origIndex"
            class="message-text user-bubble"
          >
            <span v-if="userSkillTokens.length > 0" class="message-skill-chips">
              <span
                v-for="skill in userSkillTokens"
                :key="skill.dir_name"
                class="message-skill-chip"
                :title="skill.description || skill.dir_name"
              >
                <SkillIcon :icon="skill.icon || ''" :category="iconCategoryFor(skill.category_id)" size="xs" />
                <span class="message-skill-chip-name">{{ skill.name || skill.dir_name }}</span>
              </span>
            </span>
            <MarkdownBody v-if="block.content" class="user-bubble-body" :content="block.content" :cite-map="citeNumberMap" />
          </div>
          <MarkdownBody v-else-if="block.type === 'content'" :key="'c'+block.origIndex" class="message-text" :content="block.content" :cite-map="citeNumberMap" />
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
            <MarkdownBody class="thinking-text" :content="message.thinking" :cite-map="citeNumberMap" />
          </div>
        </div>

        <div v-if="message.tool_calls && message.tool_calls.filter(t => t.tool_name !== 'write_todos').length > 0" class="tool-calls-block">
          <ToolCallCard
            v-for="(tool, idx) in message.tool_calls.filter(t => t.tool_name !== 'write_todos')"
            :key="idx"
            :tool-name="tool.tool_name"
            :args="tool.arguments"
            :result="tool.result"
            :success="tool.success !== false"
            :duration="tool.duration"
          />
        </div>

        <MarkdownBody v-if="displayContent" class="message-text" :content="displayContent" :cite-map="citeNumberMap" />

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
          <button class="generated-files-btn" @click="$emit('view-generated-files')" title="查看生成的文件">
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
      
      <KnowledgeEvidence v-if="message.role === 'assistant'" ref="knowledgeEvidence" :evidence="message.knowledge_evidence || []" :warnings="message.knowledge_warnings || []" :content="citationSourceText" />

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

<script>
import 'highlight.js/styles/github-dark.css'
import {
  setupMarkedExtensions,
  installCodeCopyHandler,
  copyTextToClipboard,
} from '../../../markdownSetup.js'
import MarkdownBody from './MarkdownBody.vue'
import FileIcon from '../../../shared/components/FileIcon.vue'
import KnowledgeEvidence from '../../knowledge/KnowledgeEvidence.vue'
import SkillIcon from '../../../shared/components/SkillIcon.vue'
import ToolCallCard from './ToolCallCard.vue'
import { approvalTargetForBlock, unmatchedApprovalRequests } from '../../../utils/hitlApproval.js'
import { pageContextLabel } from '../../../utils/pageContext.js'
import { iconCategoryFor } from '../../../utils/skillCategories.js'
// 专家头像取字 / 配色：与技能中心、+ 号下拉、输入框胶囊共用同一套规则（按 ID 稳定取档）
import {
  expertAvatarChar as buildExpertAvatarChar,
  expertAvatarClass as buildExpertAvatarClass
} from '../../../utils/expertAvatar.js'
import { parseLeadingSkillTokens } from '../../../utils/skillTokens.js'
import { highlightCode } from '../../../shared/markdown/highlight.js'
import { ensureSkillDirectoryLoaded, skillDirectoryStore } from '../../skills/store/skillDirectoryStore.js'

// 注册 KaTeX 数学公式 + emoji 短代码扩展（幂等，仅执行一次）
setupMarkedExtensions()


// 处理过程类型：思考 + 工具调用（排除已在侧边栏显示的 write_todos）
function isProcessType(b) {
  return (
    b.type === 'thinking' ||
    (b.type === 'tool_call' && b.tool_name !== 'write_todos')
  )
}

// 将回答中的 [知识依据N] / [知识库依据N] 标记转为可点击锚点，
// 点击后由 handleContentClick 委托处理，展开证据面板并滚动到对应依据卡片。
// citeMap：原始依据编号 → 展示编号（面板仅展示被引用依据并按引用顺序重新编号，
// 正文锚点同步改写，保证正文标记与面板卡片编号一致）。

// 统一的 markdown 渲染器（代码块结构、外链处理由 markdownSetup 提供），
// 代码高亮使用上面的 highlight.js 实现

// 代码块复制按钮的全局处理函数（幂等），FilePreview 也复用同一实现
installCodeCopyHandler()

export default {
  components: { FileIcon, ToolCallCard, KnowledgeEvidence, SkillIcon, MarkdownBody },
  props: {
    message: {
      type: Object,
      required: true
    },
    /** 本轮（该 assistant 消息前面最近一条用户消息）携带的专家信息 { id, name }。
        由 Chat.vue 计算后传入，用于刷新/历史加载后的专家标识（内存占位消息则用
        消息自身的 message.expert）。未选专家时为 null，不渲染标识。 */
    userExpert: {
      type: Object,
      default: null
    }
  },
  data() {
    return {
      showThinking: false,
      expandedThinking: {},
      expandedTool: {},
      // 处理过程默认展开（含实时会话），由用户手动展开/折叠；新过程到达不自动展开，
      // 避免打断用户已收起的查看状态。
      processExpanded: true,
      // 用户是否手动操作过执行过程的展开/折叠：一旦手动操作，本次消息内不再自动展开。
      // 必须加这道闸——流式期间 blocks 每次变化都会触发自动展开 watcher，否则用户
      // 点折叠会被「自动展开」立刻顶回去，表现为「流式返回时点击折叠按钮无效」。
      processToggledByUser: false,
      isStuck: false,
    }
  },
  created() {
    // 技能目录（目录名 → 技能信息）：用于把用户消息里的 /技能名 前缀还原成技能标签
    ensureSkillDirectoryLoaded()
  },
  computed: {
    pageContextText() {
      return pageContextLabel(this.message.page_context)
    },
    /**
     * 用户消息的技能标签与正文拆分。
     *
     * 优先读结构化的 `message.skills`（技能目录名，后端落库），展示名/图标按目录名反查
     * 技能目录；老数据（改造前的消息、用户手打的 `/技能名`）没有该字段，仍按正文开头的
     * `/目录名` 令牌解析还原——目录名不匹配时不解析，避免把 /usr/bin 这类路径误当技能。
     * 其余角色的消息原样返回。
     */
    userSkillSplit() {
      const content = this.message.content
      if (this.message.role !== 'user') return { skills: [], body: content }

      // 新数据：技能目录名由后端落库在 message.skills（结构化字段），按它渲染标签即可，
      // 不再从正文里反扒前缀，也不依赖技能目录接口是否加载完成（展示名反查不到时用目录名兜底）。
      const dirNames = Array.isArray(this.message.skills) ? this.message.skills : []
      if (dirNames.length > 0) {
        const parsed = parseLeadingSkillTokens(content, dirNames)
        return {
          skills: dirNames.map(name => skillDirectoryStore.byDirName[name] || { dir_name: name, name }),
          body: parsed.body,
        }
      }

      // 老数据（改造前落库的消息 / 手打 `/技能名`）：前缀还拼在正文里，按技能目录反查还原
      const names = skillDirectoryStore.names
      if (names.length === 0) return { skills: [], body: content }
      const parsed = parseLeadingSkillTokens(content, names)
      return {
        skills: parsed.dirNames.map(name => skillDirectoryStore.byDirName[name]).filter(Boolean),
        body: parsed.body,
      }
    },
    /** 用户消息携带的技能标签（按 /技能名 出现顺序） */
    userSkillTokens() {
      return this.userSkillSplit.skills
    },
    /** 展示用正文：用户消息去掉开头的技能令牌，技能已由标签承载 */
    displayContent() {
      return this.userSkillSplit.body
    },
    // 判断 assistant 消息是否有任何可见内容
    hasAnyContent() {
      const m = this.message
      if (m.blocks && m.blocks.length > 0) return true
      if (m.thinking) return true
      if (m.content) return true
      if (m.tool_calls && m.tool_calls.length > 0) return true
      return false
    },
    // 供证据面板解析实际引用的完整正文（兼容 content 直存与 blocks 分块两种消息形态）
    citationSourceText() {
      const blocks = Array.isArray(this.message.blocks) ? this.message.blocks : []
      const blockText = blocks
        .filter(block => block && block.type === 'content')
        .map(block => block.content || '')
        .join('\n')
      return `${this.message.content || ''}\n${blockText}`
    },
    // 引用编号重排映射：按正文出现顺序把原始依据编号映射为 1..N 连续编号，
    // 与 KnowledgeEvidence 的展示编号规则保持一致（仅重排有效引用编号）
    citeNumberMap() {
      const evidenceCount = (this.message.knowledge_evidence || []).length
      if (!evidenceCount) return {}
      const pattern = /\[知识(?:库)?依据\s*(\d+)\]/g
      const seen = []
      let match
      while ((match = pattern.exec(this.citationSourceText || '')) !== null) {
        const value = Number(match[1])
        if (value > 0 && value <= evidenceCount && seen.indexOf(value) === -1) {
          seen.push(value)
        }
      }
      const map = {}
      seen.forEach((value, i) => { map[value] = i + 1 })
      return map
    },
    sortedBlocks() {
      // 从 blocks 字段构建（优先使用）
      if (this.message.blocks && this.message.blocks.length > 0) {
        // 严格按大模型返回顺序（创建顺序 order）展示，不做按 step/类型的二次重排：
        // 旧数据 content 块无 step，按 step 重排会把它排到顶部；按 order 原序则正文
        //（创建最晚、order 最大）自然落在最后。同一 step「先思考后工具」由创建顺序保证
        //（reopen 时思考晚于工具创建的极端情况，已在 addBlock 中用更小的 order 纠正）。
        const sorted = [...this.message.blocks].sort((a, b) => (a.order || 0) - (b.order || 0))
        // 兼容数据：blocks 存在但没有 content 类型的 block 时，从 message.content 补充。
        // 正文是模型最终输出的回答，应排在所有思考/工具块之后（与实时流式中 content
        // 事件最后到达的顺序一致）；原先插到首个思考块之前，会导致历史会话正文显示在最顶部。
        // 用 displayContent：用户消息的 /技能名 前缀已渲染成技能标签，正文里不再重复。
        const hasContentBlock = sorted.some(b => b.type === 'content')
        if (!hasContentBlock && this.displayContent) {
          sorted.push({
            type: 'content',
            content: this.displayContent,
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
      if (this.message.thinking) {
        blocks.push({
          type: 'thinking',
          content: this.message.thinking,
          duration: this.message.thinking_duration,
          step: 0,
          order: 0
        })
      }

      // 添加工具调用 blocks（合并参数、结果、耗时到一个卡片）
      if (this.message.tool_calls && this.message.tool_calls.length > 0) {
        this.message.tool_calls.forEach((tool, idx) => {
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

      // 添加内容 block。用户消息即使正文为空（只挂了技能）也要建块：
      // 技能标签挂在气泡里展示，没有气泡就没地方放标签。
      const hasSkillChips = this.message.role === 'user' && this.userSkillTokens.length > 0
      if (this.displayContent || hasSkillChips) {
        blocks.push({
          type: 'content',
          content: this.displayContent,
          order: blocks.length + 1
        })
      }

      return blocks
    },
    // 最终正文：仅当消息完成（非流式且无待审批 HITL）时，取 order 最大的一段 content
    // 展示在处理过程之后；思考/工具执行过程中到达的中间正文按返回顺序渲染在执行过程
    // 内部（process-inline-content），不混入最终正文区。
    finalContentBlocks() {
      const contents = this.sortedBlocks
        .map((b, i) => ({ ...b, origIndex: i }))
        .filter((b) => b.type === 'content')
      if (contents.length === 0) return []
      contents.sort((a, b) => (a.order || 0) - (b.order || 0))
      // 有思考/工具执行过程时：流式/HITL 期间中间正文留在执行过程内部按序展示，
      // 完成后才把最后一段正文移到过程之后；
      // 无执行过程（纯正文回复）时：流式中也要实时显示在过程外。
      const hasProcess = this.sortedBlocks.some(isProcessType)
      if (!this.isMessageFinished && hasProcess) return []
      return [contents[contents.length - 1]]
    },
    processBlocks() {
      const finalOrigIndex = this.finalContentBlocks[0]?.origIndex
      const result = []
      this.sortedBlocks.forEach((b, i) => {
        if (isProcessType(b)) {
          result.push({ ...b, origIndex: i })
          return
        }
        // 非最终正文的 content -> 中间穿插，按返回顺序纳入执行过程内部展示
        if (b.type === 'content' && i !== finalOrigIndex) {
          result.push({ ...b, origIndex: i })
        }
      })
      return result
    },
    // 消息是否已真正完成：非流式中（loading=false）且无待审批（pending_approval）
    isMessageFinished() {
      return !this.message.loading && !this.message.pending_approval
    },
    // 「步骤数」仅统计思考与工具调用，不含穿插的正文。
    processStepCount() {
      return this.processBlocks.filter((b) => isProcessType(b)).length
    },
    // 本次回答使用的专家：优先用消息自身的 expert（发送时由 handleSendMessage 写在
    // assistant 占位消息上，发送瞬间即显示）；回答完成/刷新后消息由服务端历史回填，
    // 该字段不存在，回退到「本轮用户消息携带的专家」（Chat.vue 传入的 userExpert，随
    // 消息落库，因此刷新后仍在）。两者都没有（未选专家 / 改版前的旧历史）返回 null，
    // 走通用样式。
    expertInfo() {
      const own = this.message && this.message.expert
      if (own && own.name) return own
      const fromUser = this.userExpert
      return fromUser && fromUser.name ? fromUser : null
    },
    // 处理是否仍在进行：消息仍在流式（loading）即视为处理中，扫光动画保持；流式结束
    // （loading=false）动画消失。与正文位置解耦，避免正文一出现动画就关、后续工具仍在
    // 跑却无动画提示的问题。
    isProcessActive() {
      return !!this.message.loading
    },
    // 出现待审批的工具调用（HITL）时自动展开，便于用户查看审批提示。
    hasPendingApproval() {
      return this.processBlocks.some(b => b.type === 'tool_call' && b.approval_status === 'pending')
    },
    // 没有对应工具卡片的待审批操作（子代理内部触发），在消息级面板里单独给出按钮。
    pendingPanelActions() {
      return this.message.role === 'assistant' ? unmatchedApprovalRequests(this.message) : []
    },
    // 执行过程中是否包含中间穿插正文（思考/工具之间的 content）
    hasProcessInlineContent() {
      return this.processBlocks.some((b) => b.type === 'content')
    },
  },
  watch: {
    'message.id'() {
      this.expandedThinking = {}
      this.expandedTool = {}
    },
    // 流式期间只要出现穿插正文（思考/工具之间的中间正文），自动展开执行过程，
    // 让中间正文按返回顺序可见；完成后保持用户手动展开/收起的状态。
    hasProcessInlineContent(val) {
      if (this.processToggledByUser) return
      if (this.message.loading && val && !this.processExpanded) {
        this.processExpanded = true
      }
    },
    'message.loading': {
      immediate: true,
      handler(loading) {
        if (this.processToggledByUser) return
        if (loading && this.hasProcessInlineContent && !this.processExpanded) {
          this.processExpanded = true
        }
      }
    },
    // 出现待审批的工具调用（HITL）时自动展开，便于用户查看审批提示。
    hasPendingApproval(pending) {
      if (pending) this.processExpanded = true
    },
    processExpanded(expanded) {
      this.isStuck = false
      this.$nextTick(() => {
        if (expanded) {
          const header = this.$refs.processHeaderRef
          this._scrollEl = header ? header.closest('.chat-messages') : null
          if (this._scrollEl) {
            if (!this._onScroll) {
              this._onScroll = () => this.updateStuck()
              this._scrollEl.addEventListener('scroll', this._onScroll, { passive: true })
            }
            this.updateStuck()
          }
        } else {
          if (this._scrollEl && this._onScroll) {
            this._scrollEl.removeEventListener('scroll', this._onScroll)
            this._onScroll = null
          }
          this._scrollEl = null
        }
      })
    },
    // 展开时内容增长（流式）会改变布局，重新判定冻结状态
    'processBlocks.length'() {
      if (this.processExpanded) this.$nextTick(this.updateStuck)
    },
  },
  beforeDestroy() {
    if (this._scrollEl && this._onScroll) this._scrollEl.removeEventListener('scroll', this._onScroll)
  },
  methods: {
    /** 暴露给模板：类别 id → SkillIcon 配色 key */
    iconCategoryFor(categoryId) {
      return iconCategoryFor(categoryId)
    },
    /** 暴露给模板：专家头像取字 / 配色（全局 8 色板，按 export_agent_id 稳定取档） */
    expertAvatarChar(item) {
      return buildExpertAvatarChar(item)
    },
    expertAvatarClass(item) {
      return buildExpertAvatarClass(item)
    },
    // 使用 block 的唯一标识来跟踪展开状态
    getBlockKey(block, index) {
      return block.id || `${block.type}-${block.step || 0}-${block.tool_name || ''}-${index}`
    },
    isExpandedThinking(index) {
      const block = this.sortedBlocks[index]
      if (!block) return false
      // 默认展开：仅当用户显式折叠过（值为 false）才收起
      const key = this.getBlockKey(block, index)
      return this.expandedThinking[key] !== false
    },
    toggleThinking(index) {
      const block = this.sortedBlocks[index]
      if (!block) return
      const key = this.getBlockKey(block, index)
      // Vue 2 中对响应式对象新增属性不会触发更新，整体替换对象以保证响应性
      this.expandedThinking = { ...this.expandedThinking, [key]: !this.expandedThinking[key] }
    },
    // 工具卡片上的审批按钮回答它所属的中断（DA-02）
    approvalTarget(block) {
      return approvalTargetForBlock(this.message, block)
    },
    isToolRunning(block) {
      // Tool is still running if: no duration AND the message is still loading
      return block.duration == null && this.message.loading === true
    },
    hasArgs(args) {
      if (!args) return false
      if (typeof args === 'string') return args.trim().length > 0
      if (typeof args === 'object') return Object.keys(args).length > 0
      return false
    },
    toggleToolCall(index) {
      const block = this.sortedBlocks[index]
      if (!block) return
      if (block.pending_approval) return
      const key = this.getBlockKey(block, index)
      // Vue 2 中对响应式对象新增属性不会触发更新，整体替换对象以保证响应性
      this.expandedTool = { ...this.expandedTool, [key]: !this.expandedTool[key] }
    },
    isExpandedToolCall(index) {
      const block = this.sortedBlocks[index]
      if (!block) return false
      if (block.pending_approval) return true
      // 默认展开：仅当用户显式折叠过（值为 false）才收起
      const key = this.getBlockKey(block, index)
      return this.expandedTool[key] !== false
    },
    toggleProcess() {
      this.processToggledByUser = true
      this.processExpanded = !this.processExpanded
    },
    // 执行过程头部冻结（sticky 卡住）检测：卡住时补绘顶边框——wrapper 顶边框已随滚动
    // 移出可视区，用 is-stuck 类驱动 header 伪元素重绘一条与 wrapper 圆角一致的顶边框。
    updateStuck() {
      const header = this.$refs.processHeaderRef
      if (!header || !this.processExpanded) { this.isStuck = false; return }
      const wrapper = header.parentElement
      if (!wrapper) { this.isStuck = false; return }
      // 卡住时 header 钉在可视区顶部、wrapper 顶边随滚动上移，二者顶边差增大；
      // 正常流中 header 紧贴 wrapper 顶边（差≈边框 1px）。比较二者不依赖具体吸附点。
      this.isStuck = header.getBoundingClientRect().top - wrapper.getBoundingClientRect().top > 2
    },
    // 事件委托：点击正文里的 [知识依据N] 锚点 → 展开证据面板并定位到第 N 条依据
    handleContentClick(event) {
      const target = event.target
      if (!target || typeof target.closest !== 'function') return
      const link = target.closest('a.ke-cite')
      if (!link) return
      event.preventDefault()
      const evidence = this.$refs.knowledgeEvidence
      if (evidence && typeof evidence.jumpTo === 'function') {
        evidence.jumpTo(Number(link.dataset.cite) || 0)
      }
    },
    formatJson(obj) {
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
    },
    truncateResult(result, maxLen = 500) {
      if (!result) return ''
      const str = String(result)
      return str.length > maxLen ? str.substring(0, maxLen) + '...' : str
    },
    formatSize(bytes) {
      if (bytes < 1024) return bytes + ' B'
      if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
      return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
    },
    getFileExtension(filename) {
      const parts = filename.split('.')
      if (parts.length > 1) {
        return parts[parts.length - 1].toUpperCase()
      }
      return 'FILE'
    },
    // 兼容历史脏数据：早期版本可能把注入给模型的工作区前缀/记忆上下文一并存进
    // 用户消息，复制时应剔除这些内部前缀，只复制用户真正输入的需求内容。
    cleanUserContent(content) {
      let text = String(content == null ? '' : content)
      const lines = text.split('\n')
      const wsIdx = lines.findIndex(l => /^\[workspace: .*shell: cd .*\]$/.test(l.trim()))
      if (wsIdx !== -1) {
        // 去掉 [workspace: ...] 标记行及前面的记忆/上下文前缀
        text = lines.slice(wsIdx + 1).join('\n')
      }
      return text.replace(/^\s+/, '').replace(/\s+$/, '')
    },
    async copyMessage(ev) {
      if (!this.message.content) return

      // 复制气泡里真正展示的正文：发送时拼接的 /技能名 前缀已由技能标签承载，
      // 不再带进剪贴板（见 displayContent / userSkillSplit）
      const text = this.cleanUserContent(this.displayContent)
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
    },
    retryMessage() {
      // 触发重试事件，传递消息内容（确保是纯字符串）
      const content = String(this.message.content || '')
      this.$emit('retry', content)
    },
    removeFile(index) {
      if (this.message.files && this.message.files[index]) {
        const file = this.message.files[index]
        // 通知父组件删除文件
        this.$emit('remove-file', file)
      }
    },
  },
}
</script>

<style scoped>
/* 子代理内部触发的待审批操作：没有对应工具卡片，单独排在执行过程之后 */
.pending-approval-panel {
  width: 100%;
  margin: 0 0 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
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
/* 冻结（sticky 卡住）时不再用与静态边框同深的实线，改为「更淡的顶边 + 向下柔和阴影」，
   保留顶部分隔层次的同时避免滚动时突兀的硬线。 */
.process-wrapper.process-expanded .process-header.is-stuck::before {
  content: '';
  position: absolute;
  left: -1px;
  right: -1px;
  top: 0;
  height: 0;
  border-top: 1px solid rgba(15, 23, 42, 0.06);
  border-top-left-radius: 10px;
  border-top-right-radius: 10px;
  pointer-events: none;
}
.process-wrapper.process-expanded .process-header.is-stuck {
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06), 0 2px 8px rgba(15, 23, 42, 0.05);
}
html[data-theme="dark"] .process-wrapper.process-expanded .process-header.is-stuck::before {
  border-top-color: rgba(255, 255, 255, 0.08);
}
html[data-theme="dark"] .process-wrapper.process-expanded .process-header.is-stuck {
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.45), 0 2px 8px rgba(0, 0, 0, 0.3);
}
.process-header:hover {
  background: var(--bg-tertiary, #f1f5f9);
}
/* 专家标识：回答面板左上角独立一行「头像 + 名称」（选中专家回答时出现）。
   头像取专家名称首字的圆形字标，底色由全局 .ea-0 ~ .ea-7 按 export_agent_id
   稳定取档（utils/expertAvatar.js），与技能中心专家 Tab、+ 号下拉、输入框胶囊
   四处同色；此处只负责尺寸/排版，不写死底色。 */
.expert-badge {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  margin: 2px 0 8px;
}
.expert-badge-avatar {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  /* 底色/字色由全局 .ea-0 ~ .ea-7 提供（按专家 ID 稳定取档） */
  font-size: 12px;
  font-weight: 600;
  flex-shrink: 0;
}
.expert-badge-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary, #1e293b);
  white-space: nowrap;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
}
html[data-theme="dark"] .expert-badge-name {
  color: var(--text-primary, #e2e8f0);
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

/* AI 侧边栏消息的页面上下文胶囊 */
.page-context-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 100%;
  margin-bottom: 4px;
  padding: 2px 9px 2px 7px;
  border: 1px solid var(--border-color, #e2e8f0);
  border-radius: 999px;
  background: var(--bg-primary, #f8fafc);
  color: var(--text-secondary, #64748b);
  font-size: 12px;
  line-height: 1.6;
  cursor: help;
}

.page-context-chip svg {
  width: 12px;
  height: 12px;
  flex-shrink: 0;
  color: var(--accent-color, #0ea5e9);
}

.page-context-chip span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 用户气泡：技能标签与正文同行（标签在左、正文在右并自行换行）。
   align-items: center 让标签与正文行盒垂直居中；正文多行时标签也贴行盒中线，
   不会出现「标签顶着首行、视觉上高一块」的错位感。
   窄屏放不下时标签独占一行，正文换到下一行。 */
.message.user .message-text.user-bubble {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

/* 用户气泡内的技能标签：与输入框「已选技能」同一套胶囊样式，但整体缩一档，
   让标签高度（约 24px）落在正文行高（16px × 1.7 ≈ 27px）之内，居中后不突兀 */
.message-skill-chips {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  max-width: 100%;
}

.user-bubble .message-skill-chips {
  flex-shrink: 0;
}

.message-skill-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  max-width: 100%;
  padding: 2px 8px 2px 3px;
  border: 1px solid color-mix(in srgb, var(--accent-color, #0ea5e9) 40%, transparent);
  border-radius: 999px;
  /* 半透明底色：气泡底色深浅变化时都能自然贴合 */
  background: color-mix(in srgb, var(--accent-color, #0ea5e9) 12%, transparent);
}

.message-skill-chip-name {
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-primary, #1e293b);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 180px;
}

/* SkillIcon 的 xs 档（22px）在气泡里偏大：这里按标签比例缩到 18px */
.message-skill-chip ::v-deep .skill-icon {
  width: 18px;
  height: 18px;
  border-radius: 6px;
}

.message-skill-chip ::v-deep .skill-icon svg {
  width: 11px;
  height: 11px;
}

.message-skill-chip ::v-deep .skill-icon .icon-emoji {
  font-size: 11px;
}

/* 气泡内正文：占满标签之后的剩余宽度，长文本正常换行 */
.user-bubble-body {
  flex: 1 1 auto;
  min-width: 0;
}

/* 文件容器样式 */
.files-container {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 4px;
  width: 100%;
}

/* 用户消息：文件卡片整体右移，右缘与消息文本右缘对齐（助手消息保持左侧布局） */
.message.user .message-content .files-container {
  justify-content: flex-end;
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

.thinking-text ::v-deep p {
  margin: 0 0 12px 0;
}

.thinking-text ::v-deep p:last-child {
  margin-bottom: 0;
}

.thinking-text ::v-deep ol,
.thinking-text ::v-deep ul {
  margin: 12px 0;
  padding-left: 24px;
}

.thinking-text ::v-deep li {
  margin: 6px 0;
}

.thinking-text ::v-deep code {
  background: #e2e8f0;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 13px;
  color: #475569;
  font-family: 'Fira Code', 'Consolas', monospace;
}

.thinking-text ::v-deep pre {
  background: #f6f8fa;
  color: #24292e;
  padding: 14px;
  border-radius: 8px;
  overflow-x: auto;
  margin: 16px 0;
  border: 1px solid #e1e4e8;
}

.thinking-text ::v-deep pre code {
  background: transparent;
  padding: 0;
  font-family: 'Fira Code', 'Consolas', 'Monaco', monospace;
  font-size: 13px;
}

.thinking-text ::v-deep .code-block-wrapper {
  background: #0d1117;
  border: 1px solid #21262d;
  border-radius: 8px;
  margin: 16px 0;
  overflow: hidden;
}

.thinking-text ::v-deep .code-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: #161b22;
  padding: 8px 12px;
  border-bottom: 1px solid #21262d;
}

.thinking-text ::v-deep .code-lang {
  font-size: 11px;
  color: #8b949e;
  font-weight: 500;
}

.thinking-text ::v-deep .code-block-wrapper pre {
  margin: 0;
  border: none;
  padding: 14px;
}

.thinking-text ::v-deep .code-block-wrapper pre code {
  font-family: 'Fira Code', 'Consolas', 'Monaco', monospace;
  font-size: 13px;
}

.thinking-text ::v-deep .code-block-wrapper .hljs {
  background: transparent !important;
  margin: 0;
}

.thinking-text ::v-deep .code-block-wrapper .hljs code {
  font-family: 'Fira Code', 'Consolas', 'Monaco', monospace;
  font-size: 13px;
}

.thinking-text ::v-deep .code-copy-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 4px 8px;
  background: #21262d;
  border: 1px solid #30363d;
  border-radius: 4px;
  color: #8b949e;
  font-size: 11px;
  cursor: pointer;
  transition: all 0.2s;
  height: 24px;
}

.thinking-text ::v-deep .code-copy-btn:hover {
  background: #30363d;
  border-color: #30363d;
  color: #c9d1d9;
}

.thinking-text ::v-deep .code-copy-btn svg {
  width: 12px;
  height: 12px;
}

.thinking-text ::v-deep blockquote {
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
/* 知识依据锚点：[知识依据N] 渲染为可点击链接，点击跳转证据面板对应卡片 */
/* 正文内超链接：全局 a 重置（style.css 里 color/text-decoration: inherit）
   会抹掉链接外观，这里恢复可辨识样式，保证 [标题](URL) 与裸 URL 一眼可点 */
.message-text ::v-deep a {
  color: var(--accent-color, #2563eb);
  text-decoration: underline;
  cursor: pointer;
  overflow-wrap: anywhere;
}

.message-text ::v-deep a:hover {
  text-decoration: none;
  opacity: 0.8;
}

/* 来源卡片：AI 回复里「标题 + 链接」独立行美化成卡片，不展示裸 URL，点击跳转。
   连续卡片装进 flex 容器，从左到右排列、装不下自动换行 */
.message-text ::v-deep .msg-source-cards {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  max-width: 100%;
  margin: 3px 0;
}

.message-text ::v-deep a.msg-source-card {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  max-width: 100%;
  margin: 0;
  padding: 7px 12px;
  border: 1px solid rgba(37, 99, 235, 0.28);
  border-radius: 10px;
  background: rgba(37, 99, 235, 0.05);
  color: #2563eb;
  font-weight: 500;
  line-height: 1.4;
  text-decoration: none;
  cursor: pointer;
  overflow-wrap: anywhere;
  transition: background 0.15s ease-out, border-color 0.15s ease-out;
}

.message-text ::v-deep a.msg-source-card:hover {
  background: rgba(37, 99, 235, 0.1);
  border-color: rgba(37, 99, 235, 0.5);
  text-decoration: none;
}

.message-text ::v-deep a.msg-source-card svg {
  flex-shrink: 0;
  width: 15px;
  height: 15px;
}

html[data-theme="dark"] .message-text ::v-deep a.msg-source-card {
  background: rgba(96, 165, 250, 0.1);
  border-color: rgba(96, 165, 250, 0.35);
}

.message-text ::v-deep a.ke-cite {
  color: #2563eb;
  text-decoration: underline;
  cursor: pointer;
  font-weight: 600;
}

.message-text ::v-deep a.ke-cite:hover {
  color: #1d4ed8;
  background: #eff6ff;
}

.message-text ::v-deep .github-emoji {
  display: inline;
  vertical-align: -0.125em;
  font-size: 1.1em;
  line-height: 1;
}

/* KaTeX 数学公式块级与行内展示 */
.message-text ::v-deep .katex {
  font-size: 1.05em;
  /* 行内公式作为一个整体，避免被 word-break 从中间断开 */
  white-space: nowrap;
}

.message-text ::v-deep .katex-display {
  margin: 12px 0;
  padding: 4px 0;
  max-width: 100%;
  /* 不显示滚动条：公式完整渲染，超出气泡宽度时直接溢出而非裁切/滚动 */
  overflow: visible;
}

/* 视口较窄时自动缩小块级公式，尽量避免溢出气泡（用 @media 而非 @container，避免影响布局） */
@media (max-width: 640px) {
  .message-text ::v-deep .katex-display {
    font-size: 0.9em;
  }
}

@media (max-width: 520px) {
  .message-text ::v-deep .katex-display {
    font-size: 0.78em;
  }
}

@media (max-width: 400px) {
  .message-text ::v-deep .katex-display {
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

.message-text ::v-deep pre {
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

.message-text ::v-deep pre code {
  font-family: 'Fira Code', 'Consolas', 'Monaco', monospace;
  font-size: 13px;
}

.message-text ::v-deep code {
  font-family: 'Fira Code', 'Consolas', monospace;
  font-size: 13px;
}

.message-text ::v-deep p {
  margin: 20px 0;
}

.message-text ::v-deep p:first-child {
  margin-top: 0;
}

.message-text ::v-deep p:last-child {
  margin-bottom: 0;
}

.message-text ::v-deep ul, .message-text ::v-deep ol {
  margin: 20px 0;
  padding-left: 28px;
  /* 全局 reset（style.css）抹掉了 list-style，这里恢复项目符号，
     否则列表只剩 28px 缩进、看不到圆点/序号，像凭空缩进 */
  list-style-position: outside;
}

.message-text ::v-deep ul {
  list-style: disc;
}

.message-text ::v-deep ol {
  list-style: decimal;
}

/* 嵌套列表：符号逐层变化，层级归属更清晰 */
.message-text ::v-deep ul ul {
  list-style: circle;
}

.message-text ::v-deep ul ul ul {
  list-style: square;
}

.message-text ::v-deep ul ul, .message-text ::v-deep ol ol,
.message-text ::v-deep ul ol, .message-text ::v-deep ol ul {
  margin: 6px 0 2px;
}

.message-text ::v-deep li {
  margin: 12px 0;
}

.message-text ::v-deep blockquote {
  border-left: 3px solid #0ea5e9;
  margin: 20px 0;
  padding-left: 16px;
  color: #64748b;
}

.message-text ::v-deep h1,
.message-text ::v-deep h2,
.message-text ::v-deep h3,
.message-text ::v-deep h4,
.message-text ::v-deep h5,
.message-text ::v-deep h6 {
  margin: 28px 0 20px 0;
  font-weight: 600;
  line-height: 1.4;
}

.message-text ::v-deep h1:first-child,
.message-text ::v-deep h2:first-child,
.message-text ::v-deep h3:first-child,
.message-text ::v-deep h4:first-child,
.message-text ::v-deep h5:first-child,
.message-text ::v-deep h6:first-child {
  margin-top: 0;
}

.message-text ::v-deep table {
  border-collapse: collapse;
  width: 100%;
  margin: 24px 0;
  font-size: 13px;
}

.message-text ::v-deep th,
.message-text ::v-deep td {
  border: 1px solid #e2e8f0;
  padding: 10px 14px;
  text-align: left;
}

.message-text ::v-deep th {
  background: #f1f5f9;
  font-weight: 600;
}

.message-text ::v-deep tr:nth-child(even) {
  background: #f8fafc;
}

.message-text ::v-deep tr:hover {
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

.message.user .message-text ::v-deep pre {
  background: rgba(30, 41, 59, 0.08);
  border: 1px solid rgba(30, 41, 59, 0.15);
}

.message.user .message-text ::v-deep blockquote {
  border-left-color: rgba(30, 41, 59, 0.2);
}

.message.user .message-text ::v-deep table {
  border-color: rgba(30, 41, 59, 0.15);
}

.message.user .message-text ::v-deep th,
.message.user .message-text ::v-deep td {
  border-color: rgba(30, 41, 59, 0.15);
}

.message.user .message-text ::v-deep th {
  background: rgba(30, 41, 59, 0.06);
}

.message.user .message-text ::v-deep tr:nth-child(even) {
  background: rgba(30, 41, 59, 0.03);
}

.message.user .message-text ::v-deep tr:hover {
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

.message-text ::v-deep .code-block-wrapper {
  position: relative;
  margin: 8px 0;
  width: 100%;
}

.message-text ::v-deep .code-block-wrapper pre {
  margin: 0;
  padding: 12px 16px;
  overflow-x: auto;
  border-radius: 0 0 8px 8px;
  background: #0d1117 !important;
  border: 1px solid #21262d;
  border-top: none;
}

.message-text ::v-deep .code-block-wrapper pre code {
  font-family: 'Fira Code', 'Consolas', 'Monaco', monospace;
  font-size: 13px;
  line-height: 1.5;
  color: #c9d1d9;
}

.message-text ::v-deep .code-block-wrapper .hljs {
  background: #0d1117 !important;
  padding: 12px 16px;
  margin: 0;
  border-radius: 0 0 8px 8px;
  overflow-x: auto;
}

.message-text ::v-deep .code-block-wrapper .hljs code {
  display: block;
  font-family: 'Fira Code', 'Consolas', 'Monaco', monospace;
  font-size: 13px;
  line-height: 1.5;
}

.message-text ::v-deep .code-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: #161b22;
  padding: 10px 16px;
  border-radius: 8px 8px 0 0;
  min-height: 40px;
  border: 1px solid #21262d;
  border-bottom: none;
}

.message-text ::v-deep .code-lang {
  font-size: 12px;
  color: #8b949e;
  font-weight: 500;
  display: flex;
  align-items: center;
}

.message-text ::v-deep .code-copy-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 6px 10px;
  background: #21262d;
  border: 1px solid #30363d;
  border-radius: 6px;
  color: #8b949e;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.2s;
  height: 28px;
}

.message-text ::v-deep .code-copy-btn:hover {
  background: #30363d;
  border-color: #30363d;
  color: #c9d1d9;
}

.message-text ::v-deep .code-copy-btn.copied {
  color: #22c55e;
}

.message-text ::v-deep .code-copy-btn svg {
  width: 14px;
  height: 14px;
}

/* ========== 黑色主题：代码块 ========== */
/* 代码块统一使用亮黑底色（highlight.js 的 github-dark 主题），
   深色主题下把 pre 背景置为透明，让底色跟随外层容器，避免出现两层色差 */
html[data-theme="dark"] .message-text ::v-deep .code-block-wrapper pre {
  background: transparent !important;
  border-color: #30363d !important;
}

html[data-theme="dark"] .message-text ::v-deep .code-block-wrapper pre code {
  color: #c9d1d9;
}

html[data-theme="dark"] .message-text ::v-deep .code-block-wrapper .hljs {
  background: transparent !important;
}

html[data-theme="dark"] .message-text ::v-deep .code-block-wrapper .hljs code {
  color: #c9d1d9;
}

html[data-theme="dark"] .message-text ::v-deep .code-header {
  background: #161b22;
  border-color: #30363d;
}

html[data-theme="dark"] .message-text ::v-deep .code-lang {
  color: #8b949e;
}

html[data-theme="dark"] .message-text ::v-deep .code-copy-btn {
  background: #21262d;
  border-color: #30363d;
  color: #8b949e;
}

html[data-theme="dark"] .message-text ::v-deep .code-copy-btn:hover {
  background: #30363d;
  border-color: #8b949e;
  color: #c9d1d9;
}

/* 思考过程中的代码块：全局深色规则会把 pre/code 统一成灰底，
   这里与正文代码块保持一致（深色 GitHub 风格），避免显示灰色/白色底。 */
html[data-theme="dark"] .thinking-text ::v-deep .code-block-wrapper {
  background: transparent;
  border-color: #30363d;
}

html[data-theme="dark"] .thinking-text ::v-deep .code-block-wrapper pre,
html[data-theme="dark"] .thinking-text ::v-deep .code-block-wrapper pre code {
  background: transparent !important;
  color: #c9d1d9;
}

html[data-theme="dark"] .thinking-text ::v-deep .code-block-wrapper .hljs,
html[data-theme="dark"] .thinking-text ::v-deep .code-block-wrapper .hljs code {
  background: transparent !important;
  color: #c9d1d9;
}

html[data-theme="dark"] .thinking-text ::v-deep .code-header {
  background: #161b22;
  border-color: #30363d;
}

html[data-theme="dark"] .thinking-text ::v-deep .code-lang {
  color: #8b949e;
}

html[data-theme="dark"] .thinking-text ::v-deep .code-copy-btn {
  background: #21262d;
  border-color: #30363d;
  color: #8b949e;
}

html[data-theme="dark"] .thinking-text ::v-deep .code-copy-btn:hover {
  background: #30363d;
  border-color: #8b949e;
  color: #c9d1d9;
}

/* 行内代码深色适配 */
html[data-theme="dark"] .message-text ::v-deep code,
html[data-theme="dark"] .thinking-text ::v-deep code {
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

html[data-theme="dark"] .message-text ::v-deep th,
html[data-theme="dark"] .message-text ::v-deep td {
  border-color: var(--border-color) !important;
}
html[data-theme="dark"] .message-text ::v-deep th {
  background: var(--bg-tertiary) !important;
}
html[data-theme="dark"] .message-text ::v-deep tr:nth-child(even) {
  background: var(--bg-tertiary) !important;
}
html[data-theme="dark"] .message-text ::v-deep tr:hover {
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
