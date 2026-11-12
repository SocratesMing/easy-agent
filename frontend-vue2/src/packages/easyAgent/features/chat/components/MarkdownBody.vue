<template>
  <!-- 子节点由 patchMarkdownBlocks 逐块维护（不用 v-html、不加包裹层）：DOM 结构与整篇渲染一致，
       父组件 p:first-child 等结构选择器照常生效；内容没变的块保留原节点，流式期间选中的文字不丢、图片不闪。
       样式由父组件（ChatMessage/FilePreview）的 ::v-deep 规则接管 -->
  <div></div>
</template>

<script>
import { linkKnowledgeCitations } from '../../../shared/markdown/citations.js'
import { patchMarkdownBlocks } from '../../../shared/markdown/blockDom.js'
import { renderMarkdownBlocks } from '../../../shared/markdown/render.js'

// 流式期间每帧最多一次中间帧；分片停止 150ms 后按原文做一次最终渲染
const FRAME_MS = 80
const SETTLE_MS = 150

export default {
  name: 'MarkdownBody',
  props: {
    compactCitations: { type: Boolean, default: false },
    content: {
      type: String,
      default: '',
    },
    /** 知识依据编号映射（`[知识依据N]` → 展示编号）；无知识引用时传空对象即可 */
    citeMap: {
      type: Object,
      default: () => ({}),
    },
  },
  watch: {
    content: {
      immediate: true,
      handler(value) {
        this.queue(value)
      },
    },
    compactCitations() { if (this.md.started) this.render(false) },
    citeMap: {
      deep: true,
      handler() {
        if (this.md.started) this.render(false)
      },
    },
  },
  beforeCreate() {
    // 非响应式状态：rendered 里存的是 DOM 节点，放进 data 会被 Vue 2 深度观测
    this.md = { pending: '', blocks: [], rendered: [], frameTimer: null, frameId: null, settleTimer: null, started: false }
  },
  mounted() {
    // 首次渲染发生在挂载前（immediate watcher），挂载后把结果写进 DOM
    this.flush()
  },
  beforeDestroy() {
    this.clearTimers()
  },
  methods: {
    clearTimers() {
      const md = this.md
      if (md.frameTimer) clearTimeout(md.frameTimer)
      if (md.settleTimer) clearTimeout(md.settleTimer)
      if (md.frameId != null && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(md.frameId)
      md.frameTimer = null
      md.frameId = null
      md.settleTimer = null
    },
    queue(value) {
      const md = this.md
      md.pending = value || ''
      this.clearTimers()
      // 首次渲染（历史消息、静态内容）直接出最终态，不走流式中间帧
      if (!md.started) {
        md.started = true
        this.render(false)
        return
      }
      if (typeof requestAnimationFrame === 'function') {
        md.frameId = requestAnimationFrame(() => this.render(true))
      } else {
        md.frameTimer = setTimeout(() => this.render(true), FRAME_MS)
      }
      md.settleTimer = setTimeout(() => this.render(false), SETTLE_MS)
    },
    render(partial) {
      this.md.blocks = renderMarkdownBlocks(linkKnowledgeCitations(this.md.pending, this.citeMap, this.compactCitations), { partial })
      this.flush()
    },
    flush() {
      if (!this.$el || this.$el.nodeType !== 1) return
      this.md.rendered = patchMarkdownBlocks(this.$el, this.md.rendered, this.md.blocks)
    },
  },
}
</script>
