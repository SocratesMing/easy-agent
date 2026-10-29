<template>
  <!-- v-html 内容不带 scope id，样式由父组件的 ::v-deep 规则接管（父组件 class 会合并到本根节点） -->
  <div v-html="html"></div>
</template>

<script setup>
import { computed } from 'vue'
import { renderMarkdown } from '../composables/useMarkdownRenderer.js'
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

const html = computed(() => renderMarkdown(linkKnowledgeCitations(props.content, props.citeMap)))
</script>
