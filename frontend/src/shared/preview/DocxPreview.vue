<template>
  <div class="docx-preview-container" ref="containerRef"></div>
</template>

<script setup>
import { ref, onMounted, watch, onUnmounted } from 'vue'
import { renderAsync } from 'docx-preview'
import { fitDocxPages } from '../utils/docxPageFit.js'

const props = defineProps({
  fileUrl: {
    type: String,
    default: ''
  },
  arrayBuffer: {
    type: ArrayBuffer,
    default: null
  }
})

const emit = defineEmits(['error'])
const containerRef = ref(null)
let stopPageFit = null

async function loadDocx() {
  if ((!props.fileUrl && !props.arrayBuffer) || !containerRef.value) return

  try {
    if (stopPageFit) stopPageFit()
    containerRef.value.innerHTML = ''

    let arrayBuffer = props.arrayBuffer
    if (!arrayBuffer) {
      const response = await fetch(props.fileUrl)
      arrayBuffer = await response.arrayBuffer()
    }

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
      renderEndnotes: true
    })
    if (containerRef.value) stopPageFit = fitDocxPages(containerRef.value)
  } catch (e) {
    console.error('Docx preview error:', e)
    if (containerRef.value) {
      containerRef.value.innerHTML = '<div class="preview-error">文档预览失败</div>'
      emit('error', e)
    }
  }
}

onMounted(() => {
  loadDocx()
})

watch(() => [props.fileUrl, props.arrayBuffer], () => {
  loadDocx()
})

onUnmounted(() => {
  if (stopPageFit) stopPageFit()
  if (containerRef.value) {
    containerRef.value.innerHTML = ''
  }
})
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

.docx-preview-container :deep(.docx-preview-content-wrapper) {
  box-sizing: border-box;
  width: 100%;
  min-height: 100%;
  padding: 16px 12px;
  background: #f5f5f5;
  gap: 16px;
}

.docx-preview-container :deep(.docx-page-frame) {
  flex-shrink: 0;
  overflow: hidden;
  background: white;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.docx-preview-container :deep(section.docx-preview-content) {
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
