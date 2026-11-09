<template>
  <div class="docx-preview-container" ref="containerRef"></div>
</template>

<script setup>
import { ref, onMounted, watch, onUnmounted } from 'vue'
import { renderAsync } from 'docx-preview'
import { neutralizeUnsafeLinks } from '../markdown/sanitize.js'

const props = defineProps({
  fileUrl: {
    type: String,
    default: ''
  }
})

const containerRef = ref(null)

async function loadDocx() {
  if (!props.fileUrl || !containerRef.value) return
  
  try {
    containerRef.value.innerHTML = ''
    
    const response = await fetch(props.fileUrl)
    const arrayBuffer = await response.arrayBuffer()
    
    await renderAsync(arrayBuffer, containerRef.value, containerRef.value, {
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
    neutralizeUnsafeLinks(containerRef.value)
  } catch (e) {
    console.error('Docx preview error:', e)
    containerRef.value.innerHTML = '<div class="preview-error">文档预览失败</div>'
  }
}

onMounted(() => {
  loadDocx()
})

watch(() => props.fileUrl, () => {
  loadDocx()
})

onUnmounted(() => {
  if (containerRef.value) {
    containerRef.value.innerHTML = ''
  }
})
</script>

<style scoped>
.docx-preview-container {
  width: 100%;
  height: 100%;
  overflow: auto;
  background: #f5f5f5;
}

.docx-preview-container :deep(.docx-preview-content) {
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
