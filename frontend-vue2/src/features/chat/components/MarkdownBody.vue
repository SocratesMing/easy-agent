<template>
  <!-- v-html 产物不带 scope id，样式由父组件（ChatMessage/FilePreview）的 ::v-deep 规则接管 -->
  <div v-html="html"></div>
</template>

<script>
import { linkKnowledgeCitations } from '../../../services/markdown/citations.js'
import { renderMarkdown } from '../../../services/markdown/render.js'

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
  computed: {
    html() {
      return renderMarkdown(linkKnowledgeCitations(this.content, this.citeMap))
    },
  },
}
</script>
