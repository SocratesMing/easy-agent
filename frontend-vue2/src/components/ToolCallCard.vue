<template>
  <div class="tcc" :class="[card.card, { error: card.error }]">
    <button type="button" class="tcc-head" @click="expanded = !expanded" :aria-expanded="expanded">
      <svg class="tcc-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"></path>
      </svg>
      <span class="tcc-title">{{ card.title }}</span>
      <span v-if="card.meta" class="tcc-meta">{{ card.meta }}</span>
      <span v-if="pendingApproval" class="tcc-status pending">待审批</span>
      <span v-else-if="duration != null" class="tcc-status" :class="card.error ? 'err' : 'ok'">
        <span class="tcc-mark" aria-hidden="true">{{ card.error ? '✕' : '✓' }}</span>{{ duration }}s
      </span>
      <span v-else class="tcc-status running">执行中…</span>
      <svg class="tcc-arrow" :class="{ rotated: expanded }" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <polyline points="6 9 12 15 18 9"></polyline>
      </svg>
    </button>

    <div v-show="expanded" class="tcc-body">
      <div v-if="pendingApproval" class="tcc-approval">
        <div class="tcc-approval-prompt">
          <span>此操作将删除文件，需要您的确认</span>
        </div>
        <ul v-if="filePaths && filePaths.length" class="tcc-approval-files">
          <li v-for="(fp, i) in filePaths" :key="i">{{ fp }}</li>
        </ul>
        <div class="tcc-approval-actions">
          <button type="button" class="tcc-btn approve" @click="$emit('approve')">批准</button>
          <button type="button" class="tcc-btn reject" @click="$emit('reject')">拒绝</button>
        </div>
      </div>

      <template v-else-if="card.card === 'diff'">
        <pre class="tcc-diff"><span
          v-for="(line, i) in card.diffs[0].lines"
          :key="i"
          class="tcc-diff-line"
          :class="line.type"
        >{{ line.type === 'add' ? '+ ' : '- ' }}{{ line.text }}
</span></pre>
      </template>

      <ul v-else-if="card.card === 'search' && card.shape === 'paths'" class="tcc-paths">
        <li v-for="(p, i) in card.paths" :key="i">{{ p }}</li>
        <li v-if="!card.paths.length" class="tcc-empty">无匹配</li>
      </ul>

      <pre v-else-if="card.card === 'search'" class="tcc-pre">{{ card.raw }}</pre>
      <pre v-else-if="card.card === 'terminal' || card.card === 'read'" class="tcc-pre">{{ card.body || '(无内容)' }}</pre>

      <template v-else>
        <div v-if="card.args && Object.keys(card.args).length" class="tcc-section">
          <div class="tcc-label">参数</div>
          <pre class="tcc-pre">{{ prettyArgs }}</pre>
        </div>
        <div v-if="card.body" class="tcc-section">
          <div class="tcc-label">结果</div>
          <pre class="tcc-pre">{{ card.body }}</pre>
        </div>
      </template>
    </div>
  </div>
</template>

<script>
import { toolCard } from '../utils/toolPresentation.js'

export default {
  name: 'ToolCallCard',
  props: {
    toolName: { type: String, default: '' },
    args: { type: [Object, Array], default: null },
    result: { type: [String, null], default: '' },
    success: { type: Boolean, default: true },
    duration: { type: [Number, null], default: null },
    pendingApproval: { type: Boolean, default: false },
    filePaths: { type: Array, default: () => [] },
  },
  emits: ['approve', 'reject'],
  data() {
    return { expanded: this.pendingApproval }
  },
  computed: {
    card() {
      return toolCard(this.toolName, this.args, this.result, this.success)
    },
    prettyArgs() {
      try {
        return JSON.stringify(this.args || {}, null, 2).slice(0, 1000)
      } catch (_) {
        return String(this.args)
      }
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
.tcc-title { font-weight: 600; flex-shrink: 0; max-width: 40%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tcc-meta { color: var(--text-secondary, #64748b); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; min-width: 0; }
.tcc-status { flex-shrink: 0; font-size: 12px; color: var(--text-secondary, #64748b); }
.tcc-status.err { color: #dc2626; }
.tcc-mark { margin-right: 2px; }
.tcc-arrow { width: 14px; height: 14px; flex-shrink: 0; color: var(--text-secondary, #94a3b8); transition: transform 0.15s ease; }
.tcc-arrow.rotated { transform: rotate(180deg); }

.tcc-body { border-top: 1px solid var(--border-color, #e2e8f0); padding: 8px 10px; }
.tcc-pre {
  margin: 0;
  padding: 8px 10px;
  background: #0d1117;
  color: #c9d1d9;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 320px;
  overflow: auto;
}
.tcc-diff { margin: 0; font-size: 12px; line-height: 1.5; background: #0d1117; border-radius: 6px; padding: 6px 0; overflow: auto; max-height: 320px; }
.tcc-diff-line { display: block; padding: 0 10px; color: #c9d1d9; white-space: pre-wrap; word-break: break-all; }
.tcc-diff-line.add { background: rgba(63, 185, 80, 0.15); color: #7ee787; }
.tcc-diff-line.del { background: rgba(248, 81, 73, 0.15); color: #ffa198; }
.tcc-paths { margin: 0; padding-left: 18px; font-size: 12px; line-height: 1.6; color: var(--text-primary, #1f2937); max-height: 320px; overflow: auto; }
.tcc-empty { list-style: none; color: var(--text-secondary, #64748b); margin-left: -12px; }
.tcc-section + .tcc-section { margin-top: 8px; }
.tcc-label { font-size: 12px; color: var(--text-secondary, #64748b); margin-bottom: 4px; }

.tcc-approval-prompt { font-size: 13px; color: #b45309; margin-bottom: 6px; }
.tcc-approval-files { margin: 0 0 8px; padding-left: 18px; font-size: 12px; color: var(--text-secondary, #64748b); }
.tcc-approval-actions { display: flex; gap: 8px; }
.tcc-btn { padding: 4px 14px; border-radius: 6px; font-size: 13px; cursor: pointer; border: 1px solid transparent; }
.tcc-btn.approve { background: #16a34a; color: #fff; }
.tcc-btn.reject { background: #fff; color: #dc2626; border-color: #fca5a5; }
</style>
