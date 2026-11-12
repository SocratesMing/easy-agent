<template>
  <section :id="popoverId" class="citation-popover" popover="manual" role="tooltip" :aria-label="'知识依据 ' + number" :style="position"
    @mouseenter="$emit('keep')" @mouseleave="$emit('leave')" @focusin="$emit('keep')" @focusout="handleFocusOut">
    <header class="citation-preview-header">
      <span class="citation-preview-number">{{ number }}</span>
      <span>{{ metadata.base_name || '知识库' }}</span>
      <time v-if="metadata.publish_date">{{ metadata.publish_date }}</time>
    </header>
    <h3>{{ metadata.report_title || item.document_name }}</h3>
    <p v-if="item.snippet" class="citation-preview-snippet">{{ item.snippet }}</p>
    <footer v-if="sourceLine" class="citation-preview-source">{{ sourceLine }}</footer>
  </section>
</template>

<script>
export default {
  name: 'CitationPopover',
  props: {
    item: { type: Object, required: true },
    number: { type: Number, required: true },
    popoverId: { type: String, required: true },
    position: { type: Object, default: () => ({}) },
  },
  mounted() {
    // 浏览器顶层让卡片不受聊天区 overflow、宿主 transform 和工作区层级裁切。
    if (typeof this.$el.showPopover === 'function') this.$el.showPopover()
    this.citationResizeObserver = new ResizeObserver(() => this.$emit('resize'))
    this.citationResizeObserver.observe(this.$el)
  },
  beforeUnmount() {
    if (this.citationResizeObserver) this.citationResizeObserver.disconnect()
  },
  computed: {
    metadata() { return this.item.metadata || {} },
    sourceLine() {
      return [this.metadata.report_second_source, this.metadata.report_tag,
        this.item.page ? `第 ${this.item.page} 页` : ''].filter(Boolean).join(' · ')
    },
  },
  methods: {
    handleFocusOut(event) {
      if (!event.relatedTarget || !this.$el.contains(event.relatedTarget)) this.$emit('leave')
    },
  },
}
</script>

<style scoped>
.citation-popover {
  position: fixed;
  z-index: 100;
  margin: 0;
  inset: auto;
  width: min(420px, calc(100vw - 24px));
  max-height: calc(100vh - 24px);
  box-sizing: border-box;
  overflow: auto;
  overscroll-behavior: contain;
  padding: 16px 20px;
  border: 1px solid var(--border-color, #e2e8f0);
  border-radius: 12px;
  background: var(--bg-secondary, #fff);
  color: var(--text-primary, #1e293b);
  box-shadow: 0 8px 28px rgba(15, 23, 42, .14);
  font-size: 13px;
  line-height: 1.7;
}
.citation-preview-header { display: flex; align-items: center; gap: 8px; color: var(--text-secondary, #64748b); font-size: 12px; }
.citation-preview-header > span:nth-child(2) { min-width: 0; overflow-wrap: anywhere; }
.citation-preview-number { display: inline-flex; flex-shrink: 0; align-items: center; justify-content: center; width: 20px; height: 20px; border-radius: 50%; color: var(--accent-color, #2563eb); background: var(--bg-tertiary, #edf2ff); }
.citation-preview-header time { margin-left: auto; white-space: nowrap; font-size: 11px; }
.citation-popover h3 { margin: 12px 0 8px; font-size: 15px; font-weight: 600; line-height: 1.6; overflow-wrap: anywhere; }
.citation-preview-snippet { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.citation-preview-source { margin-top: 12px; color: var(--text-secondary, #64748b); font-size: 12px; overflow-wrap: anywhere; }
</style>
