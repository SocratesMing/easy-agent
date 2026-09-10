<template>
  <div class="docx-preview-container" ref="containerRef"></div>
</template>

<script>
import { renderAsync } from 'docx-preview'
import { requestArrayBuffer } from '@/utils/request'

export default {
  name: 'DocxPreview',
  props: {
    fileUrl: {
      type: String,
      default: '',
    },
  },
  methods: {
    async loadDocx() {
      const el = this.$refs.containerRef
      if (!this.fileUrl || !el) return

      try {
        el.innerHTML = ''

        // 走统一 axios 封装（自动带鉴权头），直接拿原始字节给 docx-preview 渲染
        const arrayBuffer = await requestArrayBuffer({ url: this.fileUrl })

        await renderAsync(arrayBuffer, el, el, {
          className: 'docx-preview-content',
          inWrapper: true,
          ignoreWidth: false,
          ignoreHeight: false,
          ignoreFonts: false,
          breakPages: true,
          ignoreLastRenderedPageBreak: true,
          experimental: false,
          trimXmlDeclaration: true,
          useBase64URL: true,
          renderHeaders: true,
          renderFooters: true,
          renderFootnotes: true,
          renderEndnotes: true,
        })
      } catch (e) {
        console.error('Docx preview error:', e)
        el.innerHTML = '<div class="preview-error">文档预览失败</div>'
      }
    },
  },
  mounted() {
    this.loadDocx()
  },
  watch: {
    fileUrl() {
      this.loadDocx()
    },
  },
  beforeDestroy() {
    const el = this.$refs.containerRef
    if (el) {
      el.innerHTML = ''
    }
  },
}
</script>

<style scoped>
.docx-preview-container {
  width: 100%;
  height: 100%;
  overflow: auto;
  background: #f5f5f5;
}

.docx-preview-container ::v-deep .docx-preview-content {
  background: white;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  margin: 16px auto;
  max-width: 816px;
}

.preview-error {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #ef4444;
  font-size: 14px;
}
</style>
