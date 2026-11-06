<template>
  <!-- v-html 产物不带 scope id，样式由父组件（ChatMessage/FilePreview）的 ::v-deep 规则接管 -->
  <div>
    <!-- 逐块 v-html：流式期间已完成块的 DOM 不会被整体替换，文字可选中、图片不闪。
         display:contents 让包裹层不产生盒子，父组件的既有样式选择器无需改动 -->
    <div v-for="block in blocks" :key="block.key" class="md-block" style="display:contents" v-html="block.html"></div>
  </div>
</template>

<script>
import { linkKnowledgeCitations } from '../../../shared/markdown/citations.js'
import { renderMarkdownBlocks } from '../../../shared/markdown/render.js'

// 流式期间每帧最多一次中间帧；分片停止 150ms 后按原文做一次最终渲染
const FRAME_MS = 80
const SETTLE_MS = 150

export default {
  name: 'MarkdownBody',
  props: {
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
  data() {
    return {
      blocks: [],
      pending: '',
      frameTimer: null,
      frameId: null,
      settleTimer: null,
      started: false,
    }
  },
  watch: {
    content: {
      immediate: true,
      handler(value) {
        this.queue(value)
      },
    },
    citeMap: {
      deep: true,
      handler() {
        if (this.started) this.render(false)
      },
    },
  },
  beforeDestroy() {
    this.clearTimers()
  },
  methods: {
    clearTimers() {
      if (this.frameTimer) clearTimeout(this.frameTimer)
      if (this.settleTimer) clearTimeout(this.settleTimer)
      if (this.frameId != null && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(this.frameId)
      this.frameTimer = null
      this.frameId = null
      this.settleTimer = null
    },
    queue(value) {
      this.pending = value || ''
      this.clearTimers()
      // 首次渲染（历史消息、静态内容）直接出最终态，不走流式中间帧
      if (!this.started) {
        this.started = true
        this.render(false)
        return
      }
      if (typeof requestAnimationFrame === 'function') {
        this.frameId = requestAnimationFrame(() => this.render(true))
      } else {
        this.frameTimer = setTimeout(() => this.render(true), FRAME_MS)
      }
      this.settleTimer = setTimeout(() => this.render(false), SETTLE_MS)
    },
    render(partial) {
      const text = linkKnowledgeCitations(this.pending, this.citeMap)
      this.blocks = renderMarkdownBlocks(text, { partial })
    },
  },
}
</script>
