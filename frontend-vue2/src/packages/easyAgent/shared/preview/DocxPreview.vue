<template>
  <div class="docx-preview-container" ref="containerRef"></div>
</template>

<script>
import { renderAsync } from 'docx-preview'
import { neutralizeUnsafeLinks } from '../markdown/sanitize.js'
import { requestArrayBuffer } from '../../utils/request.js'
import { fitDocxPages } from '../utils/docxPageFit.js'
export default {
  data() {
    return { stopPageFit: null }
  },
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
        if (this.stopPageFit) this.stopPageFit()
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
        // 去掉文档中的不安全链接，保留链接文字，再适配页面宽度。
        neutralizeUnsafeLinks(container)
        if (container.isConnected) {
          this.stopPageFit = fitDocxPages(container)
        }
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
    if (this.stopPageFit) this.stopPageFit()
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
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  background: #f5f5f5;
}

.docx-preview-container ::v-deep .docx-preview-content-wrapper {
  box-sizing: border-box;
  width: 100%;
  min-height: 100%;
  padding: 16px 12px;
  background: #f5f5f5;
  gap: 16px;
}

.docx-preview-container ::v-deep .docx-page-frame {
  flex-shrink: 0;
  overflow: hidden;
  background: white;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.docx-preview-container ::v-deep section.docx-preview-content {
  flex-shrink: 0;
  margin: 0;
  max-width: none;
  transform-origin: top left;
  box-shadow: none;
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
