<template>
  <div v-if="evidence.length || warnings.length" class="knowledge-citations">
    <button
      type="button"
      class="knowledge-citations-toggle"
      :aria-expanded="expanded ? 'true' : 'false'"
      @click="expanded = !expanded"
    >
      <span>
        知识依据 {{ displayCount }} 条<template v-if="citedCount > 0 && citedCount < evidence.length">（共检索 {{ evidence.length }} 条）</template>
      </span>
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
        :data-cite="index + 1"
        class="knowledge-citation-card"
        :class="{ cited: isCited(index) }"
      >
        <div class="knowledge-citation-title">
          <span>[知识依据{{ index + 1 }}]</span>
          {{ item.document_name }}
          <em v-if="isCited(index)" class="knowledge-citation-badge">已引用</em>
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
export default {
  name: 'KnowledgeEvidence',
  props: {
    evidence: { type: Array, default: () => [] },
    warnings: { type: Array, default: () => [] },
    messageId: { type: [String, Number], default: '' },
    // 本条回答的完整正文，用于解析 [知识依据N] 标记统计实际引用数
    content: { type: String, default: '' },
  },
  data() {
    return { expanded: false }
  },
  computed: {
    // 正文中出现过的有效依据编号（去重；忽略超出检索范围的编号）
    citedNumbers() {
      const numbers = []
      const text = this.content || ''
      const pattern = /\[知识(?:库)?依据\s*(\d+)\]/g
      let match
      while ((match = pattern.exec(text)) !== null) {
        const value = Number(match[1])
        if (value > 0 && value <= this.evidence.length && numbers.indexOf(value) === -1) {
          numbers.push(value)
        }
      }
      return numbers
    },
    citedCount() {
      return this.citedNumbers.length
    },
    // 头部展示条数：回答有明确引用时以引用数为准，否则回退为检索条数
    displayCount() {
      return this.citedCount > 0 ? this.citedCount : this.evidence.length
    },
  },
  watch: {
    messageId() {
      this.expanded = false
    },
  },
  methods: {
    isCited(index) {
      return this.citedNumbers.indexOf(index + 1) !== -1
    },
    // 展开面板并滚动定位到第 number 条依据卡片（供正文锚点点击调用）
    jumpTo(number) {
      if (!number) return
      this.expanded = true
      this.$nextTick(() => {
        const card = this.$el.querySelector(`[data-cite="${number}"]`)
        if (!card) return
        card.scrollIntoView({ behavior: 'smooth', block: 'center' })
        card.classList.add('flash')
        setTimeout(() => card.classList.remove('flash'), 1600)
      })
    },
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
.knowledge-citation-card.cited { border-color: #2563eb; background: #f0f6ff; }
.knowledge-citation-card.flash { animation: ke-cite-flash 1.6s ease; }
@keyframes ke-cite-flash {
  0%, 100% { box-shadow: none; }
  20%, 60% { box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.25); }
}
.knowledge-citation-title { color: #0f172a; font-size: 13px; font-weight: 600; }
.knowledge-citation-title span { color: #2563eb; margin-right: 5px; }
.knowledge-citation-badge { display: inline-block; margin-left: 6px; padding: 1px 6px; border-radius: 999px; background: #2563eb; color: #fff; font-size: 10px; font-weight: 600; font-style: normal; vertical-align: 1px; }
.knowledge-citation-base, .knowledge-citation-score { color: #64748b; font-size: 11px; }
.knowledge-citation-card p { color: #334155; font-size: 12px; line-height: 1.55; margin: 6px 0; white-space: pre-wrap; max-height: 120px; overflow: auto; }
</style>
