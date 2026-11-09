<template>
  <!-- 子节点由 patchMarkdownBlocks 逐块维护（不用 v-html、不加包裹层）：DOM 结构与整篇渲染一致，
       父组件 p:first-child 等结构选择器照常生效；内容没变的块保留原节点，流式期间选中的文字不丢、图片不闪。
       样式由父组件的 :deep 规则接管（父组件 class 会合并到本根节点） -->
  <div ref="root"></div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { highlighterVersion, renderMarkdownBlocks } from '../composables/useMarkdownRenderer.js'
import { linkKnowledgeCitations } from '../../../shared/markdown/citations.js'
import { patchMarkdownBlocks } from '../../../shared/markdown/blockDom.js'

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

const root = ref(null)
let pending = ''
let blocks = []
let rendered = []
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

function flush() {
  if (root.value) rendered = patchMarkdownBlocks(root.value, rendered, blocks)
}

function render(partial) {
  blocks = renderMarkdownBlocks(linkKnowledgeCitations(pending, props.citeMap), { partial })
  flush()
}

function queue(value) {
  pending = value || ''
  clearTimers()
  // 首次渲染（历史消息、静态内容）直接出最终态，不走流式中间帧
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
// shiki 异步就绪后重渲一次：首屏 / 历史消息在高亮器就绪前渲染的代码块补上高亮
// （以前用 computed 渲染会自动追踪高亮器，改成命令式渲染后要显式监听）
watch(highlighterVersion, () => { if (started) render(false) })
onMounted(flush)
onBeforeUnmount(clearTimers)
</script>
