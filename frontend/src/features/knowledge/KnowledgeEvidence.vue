<template>
  <div v-if="evidence.length || warnings.length" class="knowledge-citations">
    <button
      type="button"
      class="knowledge-citations-toggle"
      :aria-expanded="expanded ? 'true' : 'false'"
      @click="expanded = !expanded"
    >
      <span>知识依据 {{ evidence.length }} 条</span>
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" :class="{ rotated: expanded }">
        <polyline points="6 9 12 15 18 9"></polyline>
      </svg>
    </button>
    <div v-if="expanded" class="knowledge-citations-body">
      <div v-if="warnings.length" class="knowledge-warning">
        {{ warnings.join('；') }}
      </div>
      <article
        v-for="(item, index) in evidence"
        :key="`${item.document_id}-${index}`"
        class="knowledge-citation-card"
      >
        <div class="knowledge-citation-title">
          <span>[知识依据{{ index + 1 }}]</span>
          {{ item.document_name }}
        </div>
        <div v-if="item.metadata && item.metadata.base_name" class="knowledge-citation-base">
          {{ item.metadata.base_name }}
        </div>
        <p>{{ item.snippet }}</p>
        <span class="knowledge-citation-score">相关度 {{ Number(item.score || 0).toFixed(3) }}</span>
      </article>
    </div>
  </div>
</template>

<script>
import { ref, watch } from 'vue'

export default {
  name: 'KnowledgeEvidence',
  props: {
    evidence: { type: Array, default: () => [] },
    warnings: { type: Array, default: () => [] },
    messageId: { type: [String, Number], default: '' },
  },
  setup(props) {
    const expanded = ref(false)
    watch(() => props.messageId, () => { expanded.value = false })
    return { expanded }
  },
}
</script>

<style scoped>
.knowledge-citations { width: 100%; margin-top: 8px; border: 1px solid #dbeafe; border-radius: 10px; background: #f8fbff; overflow: hidden; }
.knowledge-citations-toggle { width: 100%; border: 0; background: transparent; color: #1d4ed8; padding: 9px 12px; display: flex; align-items: center; justify-content: space-between; font-size: 13px; font-weight: 600; cursor: pointer; }
.knowledge-citations-toggle svg { width: 15px; height: 15px; transition: transform 0.2s; }
.knowledge-citations-toggle svg.rotated { transform: rotate(180deg); }
.knowledge-citations-body { padding: 0 10px 10px; display: grid; gap: 8px; }
.knowledge-warning { color: #92400e; background: #fffbeb; border-radius: 7px; padding: 7px 9px; font-size: 12px; }
.knowledge-citation-card { background: #fff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 9px 10px; }
.knowledge-citation-title { color: #0f172a; font-size: 13px; font-weight: 600; }
.knowledge-citation-title span { color: #2563eb; margin-right: 5px; }
.knowledge-citation-base, .knowledge-citation-score { color: #64748b; font-size: 11px; }
.knowledge-citation-card p { color: #334155; font-size: 12px; line-height: 1.55; margin: 6px 0; white-space: pre-wrap; max-height: 120px; overflow: auto; }
</style>
