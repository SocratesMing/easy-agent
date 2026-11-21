<template>
  <div ref="messageEl" class="message" :class="[message.role]">
    <div class="message-content" @click="handleContentClick">
      <div v-if="message.role === 'user' && message.file_references && message.file_references.length" class="reference-history">
        <FileReferenceTag v-for="ref in message.file_references" :key="ref.path" :item="ref" />
      </div>
      <PageContextChip v-if="message.role === 'user' && message.page_context" :text="message.page_context" />
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
        <!-- 用户消息：技能标签作行内前缀接正文首行（标签在左前方，不分栏）；老数据的 /技能名 由 displayContent 去掉；正文按纯文本展示 -->
        <div v-if="message.role === 'user' && userContentBlock" class="message-text user-bubble">
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
          <UserMessageText v-if="userContentBlock.content" class="user-bubble-body" :content="userContentBlock.content" />
        </div>
        <!-- 助手消息：正文段原位常显，不挪位置、不降级样式；相邻的思考 / 工具调用（连同其间的短叙述）
             折叠成过程组，执行中只显示当前这一步。分段规则见 shared/chat/messageSegments.js -->
        <template v-else-if="message.role !== 'user'">
          <template v-for="seg in segmentViews" :key="seg.key">
            <div
              v-if="seg.kind === 'process'"
              class="process-group"
              :class="['is-' + seg.view.status, { 'is-expanded': isGroupExpanded(seg.key) }]"
            >
              <button
                type="button"
                class="process-group-header"
                :aria-expanded="isGroupExpanded(seg.key) ? 'true' : 'false'"
                @click="toggleGroup(seg.key)"
              >
                <span v-if="seg.view.status === 'running'" class="thinking-spinner"><span></span><span></span><span></span></span>
                <svg v-else-if="seg.view.status === 'approval'" class="process-group-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <circle cx="12" cy="12" r="10"></circle>
                  <line x1="10" y1="15" x2="10" y2="9"></line>
                  <line x1="14" y1="15" x2="14" y2="9"></line>
                </svg>
                <svg v-else-if="seg.view.thinkingOnly" class="process-group-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <circle cx="12" cy="12" r="10"></circle>
                  <path d="M12 16v-4M12 8h.01"></path>
                  <path d="M9.5 9.5c.5-.5 1.5-1 2.5-1s2 .5 2.5 1c.5.5.5 1.5 0 2.5-.5.5-1.5 1-2.5 1"></path>
                </svg>
                <svg v-else class="process-group-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <circle cx="12" cy="12" r="10"></circle>
                  <polyline points="8 12.5 11 15.5 16 9.5"></polyline>
                </svg>
                <span class="process-group-title">{{ seg.view.title }}</span>
                <span v-if="seg.view.summary.failedCount > 0" class="process-group-failed">{{ seg.view.summary.failedCount }} 次失败</span>
                <span v-if="seg.view.steps > 1" class="process-group-steps">{{ seg.view.steps }} 步</span>
                <svg class="toggle-arrow" :class="{ rotated: isGroupExpanded(seg.key) }" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <polyline points="6 9 12 15 18 9"></polyline>
                </svg>
              </button>
              <!-- 执行中且折叠：固定高度的实时预览，只露出当前这一步的末尾，高度不随步骤数增长 -->
              <div v-if="seg.view.preview && !isGroupExpanded(seg.key)" class="process-group-preview">
                <div class="process-group-preview-text">{{ seg.view.preview }}</div>
              </div>
              <div v-if="isGroupExpanded(seg.key)" class="process-group-body">
                <!-- 只有思考的组：展开直接看思考内容，不再套一层折叠 -->
                <template v-if="seg.view.thinkingOnly">
                  <div v-for="row in seg.view.rows" :key="row.key" class="thinking-content">
                    <MarkdownBody class="thinking-text" :content="row.item.content" :citation-footer-start="citationFooterFor(row.item)" :cite-map="citeNumberMap" :compact-citations="citationPresentation === 'popover'" :streaming="!!message.loading" />
                  </div>
                </template>
                <template v-else>
                  <template v-for="row in seg.view.rows" :key="row.key">
                    <!-- 并入组的短叙述：作为下一步的说明 -->
                    <MarkdownBody v-if="row.type === 'narration'" class="message-text process-narration" :content="row.item.content" :citation-footer-start="citationFooterFor(row.item)" :cite-map="citeNumberMap" :compact-citations="citationPresentation === 'popover'" :streaming="!!message.loading" />
                    <div v-else-if="row.type === 'thinking'" class="thinking-block" :class="{ 'thinking-active': isThinkingActive(row.item) }">
                      <div class="thinking-header" @click="toggleThinkingItem(row.item)">
                        <div v-if="isThinkingActive(row.item)" class="thinking-spinner">
                          <span></span><span></span><span></span>
                        </div>
                        <svg v-else class="thinking-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                          <circle cx="12" cy="12" r="10"></circle>
                          <path d="M12 16v-4M12 8h.01"></path>
                          <path d="M9.5 9.5c.5-.5 1.5-1 2.5-1s2 .5 2.5 1c.5.5.5 1.5 0 2.5-.5.5-1.5 1-2.5 1"></path>
                        </svg>
                        <span class="thinking-title">{{ isThinkingActive(row.item) ? '正在思考...' : '思考过程' }}</span>
                        <span v-if="row.item.duration != null" class="thinking-duration">用时 {{ row.item.duration }} 秒</span>
                        <svg class="toggle-arrow" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" :class="{ rotated: isThinkingOpen(row.item) }">
                          <polyline points="6 9 12 15 18 9"></polyline>
                        </svg>
                      </div>
                      <div v-if="isThinkingOpen(row.item)" class="thinking-content">
                        <MarkdownBody class="thinking-text" :content="row.item.content" :citation-footer-start="citationFooterFor(row.item)" :cite-map="citeNumberMap" :compact-citations="citationPresentation === 'popover'" :streaming="!!message.loading" />
                      </div>
                    </div>
                    <!-- 连续的读取 / 搜索合并成一行，点开再看每一条 -->
                    <div v-else-if="row.type === 'run'" class="process-run">
                      <button type="button" class="process-run-header" :aria-expanded="isRunExpanded(row.key) ? 'true' : 'false'" @click="toggleRun(row.key)">
                        <span class="process-run-label">{{ row.label }}</span>
                        <svg class="toggle-arrow" :class="{ rotated: isRunExpanded(row.key) }" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                          <polyline points="6 9 12 15 18 9"></polyline>
                        </svg>
                      </button>
                      <div v-if="isRunExpanded(row.key)" class="process-run-body">
                        <ToolCallCard v-for="item in row.items" :key="'i' + item.origIndex" v-bind="toolCardProps(item)" v-on="toolCardListeners(item)" />
                      </div>
                    </div>
                    <!-- 工具调用卡片（dsh 风格，默认收起）-->
                    <ToolCallCard v-else v-bind="toolCardProps(row.item)" v-on="toolCardListeners(row.item)" />
                  </template>
                </template>
              </div>
              <!-- 组折叠时，待审批的卡片和数据工具画出的图也要露出来：审批按钮、图表都是给用户看的结果 -->
              <div v-else-if="seg.view.pinned.length > 0 || seg.view.visuals.length > 0" class="process-group-pinned">
                <ToolCallCard v-for="item in seg.view.pinned.concat(seg.view.visuals)" :key="'i' + item.origIndex" v-bind="toolCardProps(item)" v-on="toolCardListeners(item)" />
              </div>
            </div>
            <MarkdownBody v-else class="message-text" :content="seg.block.content" :citation-footer-start="citationFooterFor(seg.block)" :cite-map="citeNumberMap" :compact-citations="citationPresentation === 'popover'" :streaming="!!message.loading" />
          </template>
          <!-- 一个中断挂了多项操作（同一条 AIMessage 的多个工具调用被框架打包成一次中断）：
               只给一个审批入口 + 操作清单，避免「点一次静默批了整批」（DA-28） -->
          <div v-if="pendingBatchGroups.length > 0" class="pending-approval-panel">
            <ApprovalBatchPanel
              v-for="group in pendingBatchGroups"
              :key="'hitl-batch-' + group.interruptId"
              :actions="group.actions"
              @approve="$emit('approve', { interruptId: group.interruptId })"
              @reject="$emit('reject', { interruptId: group.interruptId })"
            />
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
            <MarkdownBody class="thinking-text" :content="message.thinking" :citation-footer-start="-1" :cite-map="citeNumberMap" :compact-citations="citationPresentation === 'popover'" :streaming="!!message.loading" />
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

        <MarkdownBody v-if="message.content" class="message-text" :content="message.content" :citation-footer-start="citationFooterLayout.whole" :cite-map="citeNumberMap" :compact-citations="citationPresentation === 'popover'" :streaming="!!message.loading" />

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
      <MessageError :error="message.error" />
      <MessageFallbackNote v-if="message.role === 'assistant'" :note="message.fallback_note" />
      <div v-if="citationSourcesList.length" class="citation-sources-bar">
        <button type="button" class="citation-sources-toggle" @click="showSourcesBar = !showSourcesBar">
          <span>参考来源 ({{ citationSourcesList.length }})</span>
          <svg class="citation-sources-arrow" :class="{ open: showSourcesBar }" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="6 9 12 15 18 9"></polyline>
          </svg>
        </button>
        <div v-if="showSourcesBar" class="citation-sources-list">
          <button
            v-for="(src, idx) in citationSourcesList"
            :key="src.key || idx"
            type="button"
            class="citation-source-pill ke-cite-number"
            :data-cite="idx + 1"
            :title="(src.metadata && src.metadata.report_title) || src.document_name"
          >
            <span class="citation-source-idx">{{ idx + 1 }}</span>
            <span class="citation-source-name">{{ (src.metadata && src.metadata.report_title) || src.document_name }}</span>
            <span class="citation-source-kind">{{ sourceLabel(src) }}</span>
          </button>
        </div>
      </div>

      <KnowledgeEvidence
        v-if="message.role === 'assistant' && (citationPresentation !== 'popover' || (message.knowledge_warnings || []).length)"
        ref="knowledgeEvidence"
        :evidence="citationPresentation === 'popover' ? [] : (message.knowledge_evidence || [])"
        :warnings="message.knowledge_warnings || []"
        :content="citationSourceText"
      />

      <!-- 用户消息显示复制和重试按钮 -->
      <div v-if="message.role === 'user' && displayContent" class="message-actions">
        <button v-if="canRetryUserMessage(message)" class="action-btn retry-btn" @click="retryMessage" title="重新发送">
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
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import { ensureHighlighter } from '../composables/useMarkdownRenderer.js'
import MarkdownBody from './MarkdownBody.vue'
import UserMessageText from './UserMessageText.vue'
import { citationFooterOffsets } from '../../../shared/markdown/sourceFooter.js'
import { relatedEvidence, inlineCitationMap } from '../../../shared/markdown/citations.js'
import { sourceLabel } from '../../../shared/citations/sources.js'
import KnowledgeEvidence from '../../knowledge/KnowledgeEvidence.vue'
import FileReferenceTag from './FileReferenceTag.vue'
import FileIcon from '../../../shared/components/FileIcon.vue'
import ToolCallCard from './ToolCallCard.vue'
import ApprovalBatchPanel from './ApprovalBatchPanel.vue'
import MessageError from './MessageError.vue'
import MessageFallbackNote from './MessageFallbackNote.vue'
import { buildMessageSegments, describeProcessGroup } from '../../../shared/chat/messageSegments.js'
import { buildUserRetryRequest, canRetryUserMessage } from '../../../shared/chat/messageRetry.js'
import { approvalTargetForBlock, batchedApprovalGroups, isBatchedApprovalBlock, unmatchedApprovalRequests } from '../../../utils/hitlApproval.js'
import SkillIcon from '../../../shared/components/SkillIcon.vue'
import { iconCategoryFor } from '../../../utils/skillCategories.js'
// 专家头像取字 / 配色：与技能中心、+ 号下拉、输入框胶囊共用同一套规则（按 ID 稳定取档）
import { expertAvatarChar, expertAvatarClass } from '../../../utils/expertAvatar.js'
import PageContextChip from './PageContextChip.vue'
import { parseLeadingSkillTokens } from '../../../utils/skillTokens.js'
import { ensureSkillDirectoryLoaded, skillDirectoryStore } from '../../skills/store/skillDirectoryStore.js'

const props = defineProps({
  citationPresentation: { type: String, default: 'inline' },
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
})

const emit = defineEmits(['removeFile', 'viewGeneratedFiles', 'retry', 'approve', 'reject', 'edit'])

const showThinking = ref(false)
const expandedThinking = ref({})
const expandedTool = ref({})
// 过程组 / 合并行的展开状态（key → true）：默认折叠，执行中也不自动展开——
// 当前这一步由组内的实时预览展示，不会把用户的阅读位置顶走。
const expandedGroups = ref({})
const expandedRuns = ref({})

onMounted(() => {
  // 技能标签 / 老数据前缀解析都要按目录名反查技能（名称、图标、分类）
  ensureSkillDirectoryLoaded()
  // shiki 是模块级单例，多个消息组件共享一次初始化
  ensureHighlighter()
})

watch(() => props.message.id, () => {
  expandedThinking.value = {}
  expandedTool.value = {}
  expandedGroups.value = {}
  expandedRuns.value = {}
})

// 本次回答使用的专家：优先用消息自身的 expert（发送时写在占位消息上），
// 没有则回退到「本轮用户消息携带的专家」（随消息落库，刷新后仍在）；都没有返回 null。
const expertInfo = computed(() => {
  const own = props.message && props.message.expert
  if (own && own.name) return own
  const fromUser = props.userExpert
  return fromUser && fromUser.name ? fromUser : null
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

// 用户消息的技能标签与正文拆分：优先读结构化的 message.skills；老数据仍按正文开头的
// /目录名 令牌解析还原（目录名不匹配时不解析，避免把 /usr/bin 这类路径误当技能）。
const userSkillSplit = computed(() => {
  const content = props.message.content
  if (props.message.role !== 'user') return { skills: [], body: content }

  const dirNames = Array.isArray(props.message.skills) ? props.message.skills : []
  if (dirNames.length > 0) {
    const parsed = parseLeadingSkillTokens(content, dirNames)
    return {
      skills: dirNames.map(name => skillDirectoryStore.byDirName[name] || { dir_name: name, name }),
      body: parsed.body
    }
  }

  const names = skillDirectoryStore.names
  if (names.length === 0) return { skills: [], body: content }
  const parsed = parseLeadingSkillTokens(content, names)
  return {
    skills: parsed.dirNames.map(name => skillDirectoryStore.byDirName[name]).filter(Boolean),
    body: parsed.body
  }
})

// 用户消息里挂的技能标签（气泡内与正文同行展示）
const userSkillTokens = computed(() => userSkillSplit.value.skills)

// 用户消息展示用正文：去掉老数据里开头的 /技能名 指令前缀（新数据本来就没有）
const displayContent = computed(() => userSkillSplit.value.body)

// 供证据面板解析实际引用的完整正文（兼容 content 直存与 blocks 分块两种消息形态）
const citationSourceText = computed(() => {
  const blocks = Array.isArray(props.message.blocks) ? props.message.blocks : []
  const blockText = blocks
    .filter(block => block && block.type === 'content')
    .map(block => block.content || '')
    .join('\n')
  return `${props.message.content || ''}\n${blockText}`
})

// 引用编号重排映射：按正文出现顺序把原始依据编号映射为 1..N 连续编号，
// 与 KnowledgeEvidence 的展示编号规则保持一致（仅重排有效引用编号）
const citationFooterLayout = computed(() => citationFooterOffsets(sortedBlocks.value, props.message.content || ''))
function citationFooterFor(block) {
  return block.type === 'content' ? citationFooterLayout.value.blocks[block.origIndex] ?? -1 : -1
}

const citeNumberMap = computed(() => {
  if (props.citationPresentation === 'popover') return relatedEvidence(props.message).numberMap
  return inlineCitationMap(citationSourceText.value, (props.message.knowledge_evidence || []).length)
})

const showSourcesBar = ref(false)
const citationSourcesList = computed(() => {
  if (props.citationPresentation !== 'popover' || props.message.role !== 'assistant' || props.message.loading) return []
  return relatedEvidence(props.message).items || []
})

// 正文里的 [知识依据N] 锚点：事件委托展开证据面板并定位到对应卡片
const knowledgeEvidence = ref(null)
function handleContentClick(event) {
  const target = event.target
  if (!target || typeof target.closest !== 'function') return
  const link = target.closest('.ke-cite')
  if (!link) return
  event.preventDefault()
  if (props.citationPresentation === 'popover') {
    return
  }
  const evidence = knowledgeEvidence.value
  if (evidence && typeof evidence.jumpTo === 'function') {
    evidence.jumpTo(Number(link.dataset.cite) || 0)
  }
}

const sortedBlocks = computed(() => {
  if (props.message.blocks && props.message.blocks.length > 0) {
    // 严格按创建顺序 order 展示，不做按 step/类型的二次重排（旧数据无 step，
    // 按 step 重排会把正文排到顶部）；同一 step「先思考后工具」由创建顺序保证。
    const sorted = [...props.message.blocks].sort((a, b) => (a.order || 0) - (b.order || 0))
    // 兼容数据：blocks 存在但没有 content 类型的 block 时，从 message.content 补充。
    // 正文是模型最终输出的回答，应排在所有思考/工具块之后（与实时流式中 content
    // 事件最后到达的顺序一致）；原先插到首个思考块之前，会导致历史会话正文显示在最顶部。
    const hasContentBlock = sorted.some(b => b.type === 'content')
    if (!hasContentBlock && displayContent.value) {
      sorted.push({
        type: 'content',
        content: displayContent.value,
        order: sorted.length
      })
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

  // 添加内容 block。用户消息即使正文为空（只挂了技能）也要建块：
  // 技能标签挂在气泡里展示，没有气泡就没地方放标签。
  const hasSkillChips = props.message.role === 'user' && userSkillTokens.value.length > 0
  if (displayContent.value || hasSkillChips) {
    blocks.push({
      type: 'content',
      content: displayContent.value,
      order: blocks.length + 1
    })
  }

  return blocks
})

// 助手消息的分段：正文段原位常显，相邻的思考 / 工具调用折叠成过程组（规则见
// shared/chat/messageSegments.js）。消息仍在流式且最后一段是过程组时，该组处于执行中。
const segmentViews = computed(() => {
  if (props.message.role === 'user') return []
  const segments = buildMessageSegments(sortedBlocks.value)
  const loading = !!props.message.loading
  return segments.map((seg, i) => (seg.kind === 'process'
    ? { ...seg, view: describeProcessGroup(seg, { running: loading && i === segments.length - 1 }) }
    : seg))
})

// 用户消息的气泡正文：最后一段正文块（用户消息只有正文，没有思考 / 工具）
const userContentBlock = computed(() => {
  if (props.message.role !== 'user') return null
  const contents = sortedBlocks.value.filter(b => b.type === 'content')
  return contents.length > 0 ? contents[contents.length - 1] : null
})

// 有待用户作答的工具调用（HITL）：组折叠时卡片也会露出来，这里只用于滚动定位。
const hasPendingApproval = computed(() =>
  segmentViews.value.some(seg => seg.kind === 'process' && seg.view.pinned.length > 0)
)
const messageEl = ref(null)
function scrollPendingApprovalIntoView() {
  const scope = messageEl.value
  if (!scope || !scope.querySelector) return
  const el = scope.querySelector('.tcc-approval, .pending-approval-panel')
  if (el && el.scrollIntoView) el.scrollIntoView({ block: 'nearest' })
}

// immediate：历史里已经是待审批的消息挂载时也要定位，不能等下一次变化。
watch(() => hasPendingApproval.value, (pending) => {
  if (!pending) return
  nextTick(scrollPendingApprovalIntoView)
}, { immediate: true })

// 一个中断挂了多项操作（同一条 AIMessage 的多个工具调用被框架打包成一次中断）：整批展示（DA-28）
const pendingBatchGroups = computed(() => props.message.role === 'assistant' ? batchedApprovalGroups(props.message) : [])
const batchedInterruptIds = computed(() => new Set(pendingBatchGroups.value.map(group => group.interruptId)))
// 没有对应工具卡片的待审批操作（子代理内部触发），在消息级面板里单独给出按钮（DA-02）。
// 已归入批次面板的操作不再重复出现：同一个中断只能整批作答，给两次入口只会造成误解。
const pendingPanelActions = computed(() => {
  if (props.message.role !== 'assistant') return []
  return unmatchedApprovalRequests(props.message)
    .filter(ar => !(ar.interrupt_id && batchedInterruptIds.value.has(ar.interrupt_id)))
})
watch(() => pendingPanelActions.value.length, (len, prev) => {
  if (len && !prev) nextTick(scrollPendingApprovalIntoView)
})

// 过程组 / 合并行：默认折叠，按 key（首块下标，流式追加不变）记住用户的展开
function isGroupExpanded(key) {
  return expandedGroups.value[key] === true
}

function toggleGroup(key) {
  expandedGroups.value[key] = !expandedGroups.value[key]
}

function isRunExpanded(key) {
  return expandedRuns.value[key] === true
}

function toggleRun(key) {
  expandedRuns.value[key] = !expandedRuns.value[key]
}

function toolCardProps(block) {
  return {
    toolName: block.tool_name,
    args: block.arguments,
    result: block.result,
    success: block.success !== false,
    duration: block.duration,
    pendingApproval: !!block.pending_approval,
    // 属于「一个中断多项操作」的卡片不再单给按钮：按钮在批次面板里，一次批整批
    hideApprovalActions: isBatchedApprovalBlock(props.message, block, pendingBatchGroups.value),
    filePaths: block.file_paths || [],
    allowedDecisions: block.allowed_decisions || [],
    streaming: !!block.streaming,
    subagentSteps: Array.isArray(block.subagent_steps) ? block.subagent_steps : [],
  }
}

// 工具卡片上的审批按钮回答它所属的中断（DA-02）
function toolCardListeners(block) {
  return {
    approve: () => emit('approve', approvalTargetForBlock(props.message, block)),
    reject: () => emit('reject', approvalTargetForBlock(props.message, block)),
    edit: (editedArgs) => emit('edit', { ...approvalTargetForBlock(props.message, block), editedArgs }),
  }
}

// 使用 block 的唯一标识来跟踪展开状态
function getBlockKey(block, index) {
  return block.id || `${block.type}-${block.step || 0}-${block.tool_name || ''}-${index}`
}

function isThinkingActive(block) {
  return block.duration == null && !!props.message.loading
}

// 展开的组里的思考块：思考中默认展开（用户点开组就是想看过程），结束后默认收起；点过就以用户为准
function isThinkingOpen(block) {
  const value = expandedThinking.value[getBlockKey(block, block.origIndex)]
  return value === undefined ? isThinkingActive(block) : value
}

function toggleThinkingItem(block) {
  expandedThinking.value[getBlockKey(block, block.origIndex)] = !isThinkingOpen(block)
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

  // 复制气泡里真正展示的正文：发送时拼接的 /技能名 前缀已由技能标签承载，
  // 不再带进剪贴板（见 displayContent / userSkillSplit）
  const text = cleanUserContent(displayContent.value)
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
  const request = buildUserRetryRequest(props.message)
  if (request) emit('retry', { ...request, fileReferences: props.message.file_references || [] })
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
/* 子代理内部触发的待审批操作：没有对应工具卡片，单独排在消息末尾 */
.pending-approval-panel {
  width: 100%;
  margin: 0 0 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
/* 过程组：相邻的思考 / 工具调用（连同其间的短叙述）折叠成一行，正文段在组外原位常显。
   message-content 为 align-items: flex-start（子项不拉伸），需显式撑满，与输入框对齐。 */
.process-group {
  width: 100%;
  margin: 2px 0 6px;
}
.process-group-header {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  box-sizing: border-box;
  padding: 5px 10px;
  border: none;
  border-radius: 8px;
  background: transparent;
  font: inherit;
  font-size: 13px;
  line-height: 1.5;
  text-align: left;
  color: var(--text-secondary, #64748b);
  cursor: pointer;
  user-select: none;
  transition: background 0.15s;
}
.process-group-header:hover {
  background: var(--bg-tertiary, #f1f5f9);
}
.process-group-icon {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
  color: #94a3b8;
}
.process-group.is-approval .process-group-icon {
  color: #d97706;
}
.process-group-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
}
.process-group-failed {
  flex-shrink: 0;
  font-size: 12px;
  color: #dc2626;
}
.process-group-steps {
  flex-shrink: 0;
  font-size: 12px;
  color: #94a3b8;
  font-variant-numeric: tabular-nums;
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
/* 执行中：标题文字扫光（与「正在思考」同一靛蓝），文字始终清晰可读 */
.process-group.is-running .process-group-title {
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
/* 实时预览：固定三行高、内容贴底（只露出末尾）、顶部渐隐，高度不随步骤数增长 */
.process-group-preview {
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  height: 4.5em;
  margin: 0 0 4px 18px;
  padding: 0 10px;
  border-left: 2px solid #c7d2fe;
  overflow: hidden;
  font-size: 12px;
  line-height: 1.5;
  color: #94a3b8;
  -webkit-mask-image: linear-gradient(to bottom, transparent 0, #000 45%);
  mask-image: linear-gradient(to bottom, transparent 0, #000 45%);
}
.process-group-preview-text {
  white-space: pre-wrap;
  word-break: break-word;
}
/* 展开后的步骤列表：左侧细线串起各步 */
.process-group-body {
  margin: 2px 0 4px 17px;
  padding-left: 12px;
  border-left: 1px solid var(--border-color, #e5e7eb);
}
.process-group-body > .thinking-content {
  width: 100%;
  margin: 4px 0;
}
.process-group-body .thinking-block {
  margin: 4px 0;
}
.process-narration {
  margin: 6px 0;
  font-size: 14px;
}
.process-group-pinned {
  margin: 0 0 4px;
}
/* 合并的读取 / 搜索：外观与收起的工具卡片一致 */
.process-run-header {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  box-sizing: border-box;
  margin: 6px 0;
  padding: 6px 10px;
  border: 1px solid var(--border-color, #e2e8f0);
  border-radius: 8px;
  background: var(--bg-secondary, #fff);
  font: inherit;
  font-size: 13px;
  text-align: left;
  color: var(--text-secondary, #64748b);
  cursor: pointer;
}
.process-run-header:hover {
  background: var(--bg-tertiary, #f1f5f9);
}
.process-run-label {
  flex: 1;
  min-width: 0;
  font-weight: 500;
}
.process-run-body {
  padding-left: 12px;
}
html[data-theme="dark"] .process-group-preview {
  border-left-color: #4338ca;
  color: var(--text-secondary, #94a3b8);
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

/* 用户气泡：技能标签作行内前缀接正文首行，正文换行后回到气泡左边缘。
   不用 flex 分栏：长正文会成为标签右侧的竖列、标签被居中，看着像「独占左边一列」。 */
.message.user .message-text.user-bubble {
  display: block;
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

/* 气泡内的标签按行内元素排布，紧贴正文首行、不单独占宽；多个标签仍横排 */
.user-bubble .message-skill-chips {
  display: inline-flex;
  vertical-align: middle;
  margin-right: 6px;
}

.message-skill-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 100%;
  padding: 1px 6px 1px 4px;
  border-radius: 5px;
  background: linear-gradient(
    135deg,
    color-mix(in srgb, var(--accent-color, #0ea5e9) 12%, var(--bg-secondary, #fff)),
    color-mix(in srgb, var(--accent-color, #0ea5e9) 7%, var(--bg-secondary, #fff))
  );
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent-color, #0ea5e9) 24%, var(--border-color, rgba(15, 23, 42, 0.12)));
}

.message-skill-chip-name {
  font-size: 13px;
  line-height: 1.35;
  color: var(--text-primary, #1e293b);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 180px;
}

/* 与输入框胶囊图标对齐：无底色方块、强调色线条图标 */
.message-skill-chip :deep(.skill-icon) {
  width: 14px;
  height: 14px;
  border-radius: 0;
  color: var(--accent-color, #0ea5e9) !important;
  background: none !important;
  box-shadow: none !important;
}

.message-skill-chip :deep(.skill-icon svg) {
  width: 12px;
  height: 12px;
}

.message-skill-chip :deep(.skill-icon .icon-emoji) {
  font-size: 11px;
  line-height: 1;
}

/* 气泡内正文：行内流式排版——首行紧跟标签之后，换行回到气泡左边缘 */
.user-bubble-body {
  display: inline;
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
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  box-sizing: border-box;
  padding: 4px 10px;
  background: transparent;
  border-radius: 8px;
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
  color: #475569;
  user-select: none;
  transition: background 0.2s;
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
  margin-left: auto;
  font-size: 11px;
  color: #94a3b8;
  font-weight: 400;
  font-variant-numeric: tabular-nums;
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
  padding: 12px var(--chat-message-content-inset, 16px);
  border-radius: 16px;
  font-size: 16px;
  line-height: 1.7;
  color: #1e293b;
  word-break: break-word;
  width: 100%;
  box-sizing: border-box;
}

/* 知识依据锚点：正文中的 [知识依据N] 可点击展开证据面板 */
.message-text :deep(a.ke-cite) {
  color: #2563eb;
  text-decoration: underline;
  cursor: pointer;
  font-weight: 600;
}

.message-text :deep(a.ke-cite:hover) {
  color: #1d4ed8;
  background: #eff6ff;
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

.reference-history { display: flex; flex-wrap: wrap; gap: 6px; justify-content: flex-end; }
.citation-sources-bar { margin-top: 8px; }
.citation-sources-toggle { display: inline-flex; align-items: center; gap: 4px; padding: 3px 10px; border: 1px solid var(--border-color, #e2e8f0); border-radius: 999px; background: var(--bg-secondary, #f8fafc); color: var(--text-secondary, #64748b); font-size: 12px; cursor: pointer; }
.citation-sources-toggle:hover { color: var(--text-primary, #1e293b); border-color: var(--accent-color, #94a3b8); }
.citation-sources-arrow { width: 12px; height: 12px; transition: transform 0.15s ease; }
.citation-sources-arrow.open { transform: rotate(180deg); }
.citation-sources-list { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.citation-source-pill { display: inline-flex; align-items: center; gap: 6px; max-width: 260px; height: auto; min-width: 0; margin: 0; padding: 4px 10px; border: 1px solid var(--border-color, #e2e8f0); border-radius: 8px; background: var(--bg-secondary, #fff); color: var(--text-primary, #1e293b); font-size: 12px; line-height: 1.4; cursor: pointer; }
.citation-source-pill:hover { border-color: var(--accent-color, #2563eb); background: var(--bg-tertiary, #f8fafc); }
.citation-source-idx { flex-shrink: 0; display: inline-flex; align-items: center; justify-content: center; width: 16px; height: 16px; border-radius: 999px; background: var(--bg-tertiary, #edf2ff); color: var(--accent-color, #2563eb); font-size: 11px; font-weight: 600; }
.citation-source-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.citation-source-kind { flex-shrink: 0; color: var(--text-secondary, #64748b); font-size: 11px; }
</style>

<style scoped src="./citationNumbers.css"></style>
