<template>
  <!-- v-html 内容不带 scope id，样式由父组件的 ::v-deep 规则接管（父组件 class 会合并到本根节点） -->
  <div v-html="html"></div>
</template>

<script setup>
import { computed } from 'vue'
import { renderMarkdown } from '../composables/useMarkdownRenderer.js'

const props = defineProps({
  content: {
    type: String,
    default: '',
  },
})

const html = computed(() => renderMarkdown(props.content))
</script>
