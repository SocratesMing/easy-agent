<template>
  <div class="tcc" :class="[card.card, { error: card.error }]">
    <button type="button" class="tcc-head" @click="expanded = !expanded" :aria-expanded="expanded">
      <svg v-if="kindKey === 'terminal'" class="tcc-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="4 17 10 11 4 5"></polyline><line x1="12" y1="19" x2="20" y2="19"></line>
      </svg>
      <svg v-else-if="kindKey === 'list'" class="tcc-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
        <line x1="8" y1="6" x2="21" y2="6"></line><line x1="8" y1="12" x2="21" y2="12"></line><line x1="8" y1="18" x2="21" y2="18"></line>
        <line x1="3" y1="6" x2="3.01" y2="6"></line><line x1="3" y1="12" x2="3.01" y2="12"></line><line x1="3" y1="18" x2="3.01" y2="18"></line>
      </svg>
      <svg v-else-if="kindKey === 'search'" class="tcc-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
        <circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line>
      </svg>
      <svg v-else-if="kindKey === 'read'" class="tcc-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline>
      </svg>
      <svg v-else-if="kindKey === 'edit'" class="tcc-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path>
      </svg>
      <svg v-else-if="kindKey === 'chart'" class="tcc-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
        <line x1="6" y1="20" x2="6" y2="16"></line><line x1="12" y1="20" x2="12" y2="10"></line><line x1="18" y1="20" x2="18" y2="4"></line>
      </svg>
      <svg v-else class="tcc-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"></path>
      </svg>
      <span v-if="kindLabel" class="tcc-kind">{{ kindLabel }}</span>
      <span class="tcc-title">{{ card.title }}</span>
      <span v-if="card.meta" class="tcc-meta">{{ card.meta }}</span>
      <span v-if="pendingApproval" class="tcc-status pending">待审批</span>
      <span v-else-if="isRunning" class="tcc-status running">{{ runningLabel }}</span>
      <span v-else-if="duration != null" class="tcc-status" :class="card.error ? 'err' : 'ok'">
        <span class="tcc-mark" aria-hidden="true">{{ card.error ? '✕' : '✓' }}</span>{{ duration }}s
      </span>
      <svg class="tcc-arrow" :class="{ rotated: expanded }" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <polyline points="6 9 12 15 18 9"></polyline>
      </svg>
    </button>

    <div v-show="expanded" class="tcc-body">
      <div v-if="pendingApproval" class="tcc-approval">
        <div class="tcc-approval-prompt">
          <span>{{ approvalPrompt }}</span>
        </div>
        <ul v-if="filePaths && filePaths.length" class="tcc-approval-files">
          <li v-for="(fp, i) in filePaths" :key="i">{{ fp }}</li>
        </ul>
        <template v-if="editing">
          <textarea
            v-model="draft"
            class="tcc-edit-input"
            spellcheck="false"
            aria-label="编辑参数（JSON）"
          />
          <div v-if="editError" class="tcc-edit-error">{{ editError }}</div>
          <div class="tcc-approval-actions">
            <button type="button" class="tcc-btn approve" @click="submitEdit">保存并执行</button>
            <button type="button" class="tcc-btn" @click="cancelEdit">取消</button>
          </div>
        </template>
        <div v-else class="tcc-approval-actions">
          <button type="button" class="tcc-btn approve" @click="$emit('approve')">批准</button>
          <!-- 删除类操作后端只下发 approve/reject，不提供改参数入口 -->
          <button v-if="canEditDecision" type="button" class="tcc-btn" @click="startEdit">编辑参数</button>
          <button type="button" class="tcc-btn reject" @click="$emit('reject')">拒绝</button>
        </div>
      </div>

      <!-- 图表卡片：仅展开时挂载，避免在 v-show 隐藏容器里以 0 宽初始化 ECharts -->
      <template v-else-if="card.card === 'chart'">
        <ChartArtifactView v-if="expanded" :artifact="card.artifact" />
      </template>

      <template v-else-if="card.card === 'diff'">
        <div v-if="isRunning && streaming" class="tcc-streaming-hint">正在写入…</div>
        <pre class="tcc-code flush"><span
          v-for="(line, i) in card.diffs[0].lines"
          :key="i"
          class="tcc-code-line"
          :class="line.type"
        >{{ diffPrefix(line) }}{{ line.text }}
</span></pre>
      </template>

      <ul v-else-if="card.card === 'search' && card.shape === 'paths'" class="tcc-code-list">
        <li v-for="(p, i) in card.paths" :key="i">{{ p }}</li>
        <li v-if="!card.paths.length" class="tcc-code-empty">无匹配</li>
      </ul>

      <pre v-else-if="card.card === 'search'" class="tcc-code">{{ card.raw }}</pre>
      <pre v-else-if="card.card === 'terminal' || card.card === 'read'" class="tcc-code">{{ card.body || '(无内容)' }}</pre>

      <!-- 参数已在标题的 meta 里，展开只看结果 -->
      <pre v-else class="tcc-code">{{ card.body || '(无内容)' }}</pre>
    </div>
  </div>
</template>

<script>
import { toolCard } from '../../../utils/toolPresentation.js'
import ChartArtifactView from './ChartArtifactView.vue'

export default {
  name: 'ToolCallCard',
  components: { ChartArtifactView },
  props: {
    toolName: { type: String, default: '' },
    args: { type: [Object, Array], default: null },
    result: { type: [String, null], default: '' },
    success: { type: Boolean, default: true },
    duration: { type: [Number, null], default: null },
    pendingApproval: { type: Boolean, default: false },
    filePaths: { type: Array, default: () => [] },
    streaming: { type: Boolean, default: false },
    // 后端下发的可选项（approve/edit/reject）。空数组 = 旧载荷，按全部可选处理
    allowedDecisions: { type: Array, default: () => [] },
  },
  emits: ['approve', 'reject', 'edit'],
  data() {
    return { expanded: this.pendingApproval, editing: false, draft: '', editError: '' }
  },
  created() {
    // 自动展开是否由本轮流式写入触发，用于在 streaming 结束后收回自动展开
    this._streamOpened = false
  },
  watch: {
    // MCP 数据类工具返回 chart 时自动展开，让图表直接出现在对话里
    card: {
      immediate: true,
      handler(card) {
        if (card && card.card === 'chart' && !this.pendingApproval) this.expanded = true
      },
    },
    // 待审批状态可能比卡片首帧晚到：流式先渲染工具卡片，之后才收到 approval_required。
    // 此时 expanded 已固定为 false，需要在转为待审批时自动展开，让用户直接看到
    // 参数与「批准 / 编辑 / 拒绝」按钮。仅展开不收起，审批结束后仍可查看结果。
    pendingApproval: {
      immediate: true,
      handler(v) {
        if (v) {
          this.expanded = true
          this._streamOpened = false
        }
      },
    },
    // 参数流式写入期间自动展开，让用户实时看到正在生成的文件内容；
    // 一旦收到完整参数（streaming 转为 false）即释放自动展开，尊重用户手动折叠。
    // 待审批优先级更高：同一拍里 streaming 结束不得把刚展开的审批卡片收回。
    streaming(v) {
      if (this.pendingApproval) {
        this.expanded = true
        this._streamOpened = false
        return
      }
      if (v && !this.expanded) {
        this.expanded = true
        this._streamOpened = true
      } else if (!v && this._streamOpened && this.expanded) {
        this.expanded = false
        this._streamOpened = false
      } else if (!v) {
        this._streamOpened = false
      }
    },
  },
  computed: {
    card() {
      return toolCard(this.toolName, this.args, this.result, this.success)
    },
    kindKey() {
      if (this.card.shape === 'paths') return 'list'
      if (this.card.shape === 'matches') return 'search'
      if (this.card.card === 'diff') return 'edit'
      return this.card.card
    },
    kindLabel() {
      // 写入/编辑的标题已含动作，不再重复显示类型标签
      const map = {
        terminal: '命令', list: '列出', search: '搜索', read: '读取', edit: '', chart: '图表', other: '工具',
      }
      return this.kindKey in map ? map[this.kindKey] : '工具'
    },
    // 状态语义统一：pendingApproval 首位；isRunning = 尚未拿到工具结果(duration==null)
    // 且当前没有待审批。三类工具（写入 / 命令 / 读取）此时展示一致的前缀与颜色。
    isRunning() {
      return this.duration == null && !this.pendingApproval
    },
    runningLabel() {
      return this.streaming ? '写入中…' : '执行中…'
    },
    canEditDecision() {
      if (!this.allowedDecisions.length) return true
      return this.allowedDecisions.indexOf('edit') !== -1
    },
    // 审批提示：删除类操作会带 file_paths（rm 等命令解析出的目标），其余
    // （SQL、MCP 写操作、知识库物化）用通用文案，避免误报「将删除文件」。
    approvalPrompt() {
      return this.filePaths && this.filePaths.length
        ? '此操作将删除文件，需要您的确认'
        : '此操作需要您的确认'
    },
  },
  methods: {
    startEdit() {
      this.expanded = true
      this.editing = true
      this.editError = ''
      this.draft = JSON.stringify(this.args || {}, null, 2)
    },
    cancelEdit() {
      this.editing = false
      this.editError = ''
    },
    submitEdit() {
      let parsed
      try {
        parsed = JSON.parse(this.draft)
      } catch (e) {
        this.editError = `JSON 解析失败：${e.message}`
        return
      }
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        this.editError = '参数必须是 JSON 对象'
        return
      }
      this.editing = false
      this.$emit('edit', parsed)
    },
    // diff 前缀：写入(全 add)不需要前缀，让文字与命令/读取从同一缩进起点开始；
    // 编辑(有 del/add)保留 +/- 前缀，表达「这一行来自哪个方向」。
    diffPrefix(line) {
      const diffs = this.card && this.card.diffs
      const lines = diffs && diffs[0] ? diffs[0].lines : null
      if (lines && lines.length > 0 && lines.every(l => l.type === 'add')) return ''
      return line.type === 'add' ? '+ ' : '- '
    },
  },
}
</script>

<style scoped>
.tcc {
  margin: 6px 0;
  border: 1px solid var(--border-color, #e2e8f0);
  border-radius: 8px;
  background: var(--bg-secondary, #fff);
  overflow: hidden;
}
.tcc.error { border-color: #fca5a5; }

.tcc-head {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 6px 10px;
  border: none;
  background: none;
  font: inherit;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  color: var(--text-primary, #1f2937);
}
.tcc-head:hover { background: var(--bg-tertiary, #f1f5f9); }
.tcc-icon { width: 15px; height: 15px; flex-shrink: 0; color: var(--text-secondary, #64748b); }
.tcc-kind {
  flex-shrink: 0;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 11px;
  line-height: 1.5;
  background: var(--bg-tertiary, #f1f5f9);
  color: var(--text-secondary, #64748b);
}
.tcc-title { font-weight: 500; color: var(--text-secondary, #64748b); flex-shrink: 0; max-width: 40%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tcc-meta { color: var(--text-secondary, #64748b); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; min-width: 0; }
.tcc-status {
  margin-left: auto;
  flex-shrink: 0;
  min-width: 4.5em;
  text-align: right;
  font-variant-numeric: tabular-nums;
  font-size: 12px;
  color: var(--text-secondary, #64748b);
}
.tcc-status.err { color: #dc2626; }
.tcc-mark { margin-right: 2px; }
.tcc-arrow { width: 14px; height: 14px; flex-shrink: 0; color: var(--text-secondary, #94a3b8); transition: transform 0.15s ease; }
.tcc-arrow.rotated { transform: rotate(180deg); }

.tcc-body { border-top: 1px solid var(--border-color, #e2e8f0); padding: 8px 10px; }
.tcc-code {
  margin: 0;
  padding: 8px 10px;
  background: #0d1117;
  color: #e6edf3;
  border-radius: 6px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 320px;
  overflow: auto;
  tab-size: 4;
}
.tcc-code.flush { padding-left: 0; padding-right: 0; }
.tcc-code-line { display: block; padding: 0 10px; }
.tcc-code-line.add { background: rgba(63, 185, 80, 0.15); }
.tcc-code-line.del { background: rgba(248, 81, 73, 0.15); }
.tcc-code-list { margin: 0; padding: 8px 10px 8px 28px; background: #0d1117; color: #e6edf3; border-radius: 6px; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace; font-size: 12px; line-height: 1.6; max-height: 320px; overflow: auto; }
.tcc-code-empty { list-style: none; margin-left: -18px; color: var(--text-secondary, #64748b); }
.tcc-section + .tcc-section { margin-top: 8px; }
.tcc-label { font-size: 12px; color: var(--text-secondary, #64748b); margin-bottom: 4px; }
.tcc-streaming-hint { margin-bottom: 4px; font-size: 12px; color: var(--text-secondary, #64748b); }

.tcc-approval-prompt { font-size: 13px; color: #b45309; margin-bottom: 6px; }
.tcc-approval-files { margin: 0 0 8px; padding-left: 18px; font-size: 12px; color: var(--text-secondary, #64748b); }
.tcc-approval-actions { display: flex; gap: 8px; }
.tcc-edit-input { width: 100%; min-height: 120px; box-sizing: border-box; margin-bottom: 8px; padding: 8px 10px; border: 1px solid var(--border-color, #d1d5db); border-radius: 6px; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace; font-size: 12px; line-height: 1.5; resize: vertical; }
.tcc-edit-error { margin-bottom: 6px; font-size: 12px; color: #dc2626; }
.tcc-btn { padding: 4px 14px; border-radius: 6px; font-size: 13px; cursor: pointer; border: 1px solid transparent; }
.tcc-btn.approve { background: #16a34a; color: #fff; }
.tcc-btn.reject { background: #fff; color: #dc2626; border-color: #fca5a5; }
</style>
