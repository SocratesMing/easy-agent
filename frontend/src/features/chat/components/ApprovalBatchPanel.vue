<template>
  <div class="abp">
    <div class="abp-head">
      <span class="abp-title">需要你审批</span>
      <span class="abp-count">本次审批包含 {{ rows.length }} 项操作</span>
    </div>
    <p class="abp-hint">这 {{ rows.length }} 项由同一次工具调用批次提交，只能整批作答：批准后一并执行，拒绝则全部不执行。</p>
    <ol class="abp-list">
      <li v-for="(row, i) in rows" :key="i" class="abp-item">
        <span class="abp-idx">{{ i + 1 }}</span>
        <span class="abp-tool">{{ row.name }}</span>
        <span class="abp-detail" :title="row.detail">{{ row.detail || '（无参数）' }}</span>
      </li>
    </ol>
    <div class="abp-actions">
      <button type="button" class="abp-btn approve" @click="$emit('approve')">批准全部（{{ rows.length }} 项）</button>
      <button type="button" class="abp-btn reject" @click="$emit('reject')">全部拒绝</button>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { approvalActionSummary } from '../../../utils/hitlApproval.js'

const props = defineProps({
  // 同一个 interrupt 下的全部待审批操作（已按 action_index 排序）
  actions: { type: Array, default: () => [] },
})

defineEmits(['approve', 'reject'])

const rows = computed(() => props.actions.map(approvalActionSummary))
</script>

<style scoped>
.abp {
  width: 100%;
  box-sizing: border-box;
  padding: 10px 12px;
  border: 1px solid var(--border-color, #e2e8f0);
  border-left: 3px solid #d97706;
  border-radius: 8px;
  background: var(--bg-secondary, #fff);
}
.abp-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.abp-title { font-size: 13px; font-weight: 600; color: var(--text-primary, #1f2937); }
.abp-count {
  padding: 1px 8px;
  border-radius: 10px;
  font-size: 11px;
  line-height: 1.7;
  color: #b45309;
  background: rgba(217, 119, 6, 0.12);
}
.abp-hint { margin: 6px 0 8px; font-size: 12px; line-height: 1.6; color: var(--text-secondary, #64748b); }
.abp-list {
  margin: 0 0 10px;
  padding: 6px 8px;
  list-style: none;
  border: 1px solid var(--border-color, #e2e8f0);
  border-radius: 6px;
  background: var(--bg-tertiary, #f8fafc);
}
.abp-item { display: flex; align-items: baseline; gap: 8px; padding: 3px 0; font-size: 12px; }
.abp-item + .abp-item { border-top: 1px dashed var(--border-color, #e2e8f0); }
.abp-idx {
  flex-shrink: 0;
  width: 16px;
  height: 16px;
  line-height: 16px;
  text-align: center;
  border-radius: 50%;
  font-size: 10px;
  color: var(--text-secondary, #64748b);
  background: var(--bg-secondary, #fff);
  border: 1px solid var(--border-color, #e2e8f0);
}
.abp-tool { flex-shrink: 0; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; color: var(--text-primary, #1f2937); }
.abp-detail {
  flex: 1;
  min-width: 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  color: var(--text-secondary, #64748b);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.abp-actions { display: flex; gap: 8px; }
.abp-btn {
  padding: 4px 14px;
  border-radius: 6px;
  border: 1px solid transparent;
  font-family: inherit;
  font-size: 13px;
  cursor: pointer;
}
.abp-btn.approve { background: #16a34a; color: #fff; }
.abp-btn.reject { background: transparent; color: #dc2626; border-color: #fca5a5; }
.abp-btn:hover { filter: brightness(1.05); }
</style>
