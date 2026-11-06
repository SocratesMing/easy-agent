<template>
  <!-- v-html 内容不带 scope id，样式由父组件的 ::v-deep 规则接管（父组件 class 会合并到本根节点） -->
  <div>
    <!-- 逐块 v-html：流式期间已完成块的 DOM 不会被整体替换，文字可选中、图片不闪。
         display:contents 让包裹层不产生盒子，父组件的既有样式选择器无需改动 -->
    <div v-for="block in blocks" :key="block.key" class="md-block" style="display:contents" v-html="block.html"></div>
  </div>
</template>

<script setup>
import { ref, watch, onBeforeUnmount } from 'vue'
import { renderMarkdownBlocks } from '../composables/useMarkdownRenderer.js'
import { linkKnowledgeCitations } from '../../../shared/markdown/citations.js'

const props = defineProps({
  content: {
    type: String,
    default: '',
  },
  /** 知识依据编号映射（`[知识依据N]` → 展示编号）；无知识引用时传空对象即可 */
  citeMap: {
    type: Object,
    default: () => ({}),
  },
})

// 流式期间每帧最多一次中间帧；分片停止 150ms 后按原文做一次最终渲染
const FRAME_MS = 80
const SETTLE_MS = 150

const blocks = ref([])
let pending = props.content || ''
let frameTimer = null
let frameId = null
let settleTimer = null
let started = false

function clearTimers() {
  if (frameTimer) clearTimeout(frameTimer)
  if (settleTimer) clearTimeout(settleTimer)
  if (frameId != null && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(frameId)
  frameTimer = null
  frameId = null
  settleTimer = null
}

function render(partial) {
  blocks.value = renderMarkdownBlocks(linkKnowledgeCitations(pending, props.citeMap), { partial })
}

function queue(value) {
  pending = value || ''
  clearTimers()
  if (!started) {
    started = true
    render(false)
    return
  }
  if (typeof requestAnimationFrame === 'function') {
    frameId = requestAnimationFrame(() => render(true))
  } else {
    frameTimer = setTimeout(() => render(true), FRAME_MS)
  }
  settleTimer = setTimeout(() => render(false), SETTLE_MS)
}

watch(() => props.content, queue, { immediate: true })
watch(() => props.citeMap, () => { if (started) render(false) }, { deep: true })
onBeforeUnmount(clearTimers)
</script>
