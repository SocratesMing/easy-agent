<template>
  <div class="docx-preview-container" ref="containerRef"></div>
</template>

<script>
import { renderAsync } from 'docx-preview'
import { neutralizeUnsafeLinks } from '../markdown/sanitize.js'
import { requestArrayBuffer } from '../../utils/request.js'
export default {
  props: {
    fileUrl: {
      type: String,
      default: ''
    }
  },
  methods: {
    async loadDocx() {
      const container = this.$refs.containerRef
      if (!this.fileUrl || !container) return

      try {
        container.innerHTML = ''

        // 走统一 axios 封装（自动带鉴权头），直接拿原始字节给 docx-preview 渲染
        const arrayBuffer = await requestArrayBuffer({ url: this.fileUrl })

        await renderAsync(arrayBuffer, container, container, {
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
          // altChunk 是嵌在 docx 里的整段 HTML，docx-preview 会塞进不带 sandbox 的同源
          // iframe（srcdoc）：打开预览就会在本站执行其中的脚本。只显示正文，不渲染它
          renderAltChunks: false
        })
        // 超链接地址原样取自文档（可以是 javascript:）：去掉不安全的 href，链接文字保留
        neutralizeUnsafeLinks(container)
      } catch (e) {
        console.error('Docx preview error:', e)
        container.innerHTML = '<div class="preview-error">文档预览失败</div>'
      }
    }
  },
  mounted() {
    this.loadDocx()
  },
  watch: {
    fileUrl() {
      this.loadDocx()
    }
  },
  beforeDestroy() {
    const container = this.$refs.containerRef
    if (container) {
      container.innerHTML = ''
    }
  }
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
