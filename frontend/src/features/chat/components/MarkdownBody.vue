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
import { createRenderScheduler } from '../../../shared/markdown/renderScheduler.js'
import { API_BASE_URL } from '../../../config.js'
import { VISUAL_FRAME_PATH } from '../../../shared/visual/fence.js'
import { mountVisuals } from '../../../shared/visual/mount.js'

const props = defineProps({
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
  /** 回答仍在流式输出。未闭合的图表 / 流程图 / HTML 保持骨架，不交给渲染器。 */
  streaming: { type: Boolean, default: false },
})

// 流式期间最多每 80ms 出一次中间帧；分片停止 150ms 后按原文做一次最终渲染
const FRAME_MS = 80
const SETTLE_MS = 150

const root = ref(null)
let pending = ''
let blocks = []
let rendered = []
let started = false
let changedNodes = []

const scheduler = createRenderScheduler({
  frameMs: FRAME_MS,
  settleMs: SETTLE_MS,
  render: () => render(true),
  settle: () => render(false),
})

function flush() {
  if (!root.value) return
  changedNodes = []
  rendered = patchMarkdownBlocks(root.value, rendered, blocks, nodes => {
    changedNodes.push(...nodes)
  })
  // ```echarts / ```mermaid / ```widget 代码块挂成沙箱 iframe 卡片（幂等：已挂载、仍在流式中的跳过）
  mountVisuals(root.value, {
    frameUrl: `${API_BASE_URL}${VISUAL_FRAME_PATH}`,
    targets: changedNodes,
  })
}

function render(partial) {
  blocks = renderMarkdownBlocks(linkKnowledgeCitations(pending, props.citeMap, props.compactCitations), {
    partial,
    streaming: props.streaming,
  })
  flush()
}

function queue(value) {
  pending = value || ''
  // 首次渲染（历史消息、静态内容）直接出最终态，不走流式中间帧
  if (!started) {
    started = true
    scheduler.cancel()
    render(false)
    return
  }
  scheduler.queue()
}

watch(() => props.content, queue, { immediate: true })
watch(() => props.citeMap, () => {
  if (started) {
    scheduler.cancel()
    render(false)
  }
}, { deep: true })
// shiki 异步就绪后重渲一次：首屏 / 历史消息在高亮器就绪前渲染的代码块补上高亮
// （以前用 computed 渲染会自动追踪高亮器，改成命令式渲染后要显式监听）
watch(() => props.compactCitations, () => {
  if (started) {
    scheduler.cancel()
    render(false)
  }
})
watch(highlighterVersion, () => {
  if (started) {
    scheduler.cancel()
    render(false)
  }
})
watch(() => props.streaming, (value) => {
  if (!started || value) return
  scheduler.cancel()
  render(false)
})
onMounted(flush)
onBeforeUnmount(() => scheduler.dispose())
</script>

<style src="./visual.css"></style>
