<template>
  <section :id="popoverId" class="citation-popover" popover="manual" role="tooltip" :aria-label="'引用来源 ' + number" :style="position"
    @mouseenter="$emit('keep')" @mouseleave="$emit('leave')" @focusin="$emit('keep')" @focusout="handleFocusOut">
    <header class="citation-preview-header">
      <span>{{ sourceType }}<template v-if="metadata.base_name"> · {{ metadata.base_name }}</template></span>
    </header>
    <h3 :title="metadata.report_title || item.document_name">{{ metadata.report_title || item.document_name }}</h3>
    <p v-if="item.snippet" class="citation-preview-snippet" tabindex="0" aria-label="来源内容">{{ item.snippet }}</p>
    <a v-if="sourceLink" class="citation-preview-link" :href="sourceLink" target="_blank" rel="noopener noreferrer">打开原始来源 ↗</a>
    <footer v-if="sourceLine" class="citation-preview-source">{{ sourceLine }}</footer>
  </section>
</template>

<script>
import { sourceLabel, sourceUrl } from '../../../shared/citations/sources.js'

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
  beforeDestroy() {
    if (this.citationResizeObserver) this.citationResizeObserver.disconnect()
  },
  computed: {
    metadata() { return this.item.metadata || {} },
    sourceType() { return sourceLabel(this.item) },
    sourceLink() { return sourceUrl(this.item.url) },
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
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: min(360px, calc(100vw - 24px));
  max-height: min(320px, calc(100vh - 24px));
  box-sizing: border-box;
  overflow: hidden;
  padding: 12px 16px;
  border: 1px solid var(--border-color, #e2e8f0);
  border-radius: 12px;
  background: var(--bg-secondary, #fff);
  color: var(--text-primary, #1e293b);
  box-shadow: 0 8px 28px rgba(15, 23, 42, .14);
  font-size: 13px;
  line-height: 1.6;
}
.citation-preview-header { flex-shrink: 0; display: flex; align-items: center; gap: 8px; color: var(--text-secondary, #64748b); font-size: 12px; }
.citation-preview-header > span { min-width: 0; overflow-wrap: anywhere; }
.citation-preview-link { flex-shrink: 0; align-self: flex-start; color: var(--accent-color, #2563eb); overflow-wrap: anywhere; }
.citation-popover h3 { flex-shrink: 0; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; margin: 0; font-size: 15px; font-weight: 600; line-height: 1.6; overflow-wrap: anywhere; }
.citation-preview-snippet { min-height: 0; overflow: auto; overscroll-behavior: contain; margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.citation-preview-snippet:focus-visible { outline: 2px solid var(--accent-color, #2563eb); outline-offset: -2px; }
.citation-preview-source { flex-shrink: 0; margin: 0; color: var(--text-secondary, #64748b); font-size: 12px; overflow-wrap: anywhere; }
</style>
