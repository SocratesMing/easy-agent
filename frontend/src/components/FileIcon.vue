<script setup>
import { computed } from 'vue'
import IconPdf from '~icons/vscode-icons/file-type-pdf2'
import IconWord from '~icons/vscode-icons/file-type-word'
import IconExcel from '~icons/vscode-icons/file-type-excel'
import IconPowerpoint from '~icons/vscode-icons/file-type-powerpoint'
import IconText from '~icons/vscode-icons/file-type-text'
import IconMarkdown from '~icons/vscode-icons/file-type-markdown'
import IconImage from '~icons/vscode-icons/file-type-image'
import IconPython from '~icons/vscode-icons/file-type-python'
import IconJs from '~icons/vscode-icons/file-type-js-official'
import IconTs from '~icons/vscode-icons/file-type-typescript-official'
import IconVue from '~icons/vscode-icons/file-type-vue'
import IconHtml from '~icons/vscode-icons/file-type-html'
import IconCss from '~icons/vscode-icons/file-type-css'
import IconJson from '~icons/vscode-icons/file-type-json'
import IconJava from '~icons/vscode-icons/file-type-java'
import IconGo from '~icons/vscode-icons/file-type-go'
import IconRust from '~icons/vscode-icons/file-type-rust'
import IconC from '~icons/vscode-icons/file-type-c'
import IconCpp from '~icons/vscode-icons/file-type-cpp'
import IconSql from '~icons/vscode-icons/file-type-sql'
import IconZip from '~icons/vscode-icons/file-type-zip'
import IconDefault from '~icons/vscode-icons/default-file'

const props = defineProps({
  filename: {
    type: String,
    required: true
  },
  size: {
    type: [Number, String],
    default: 48
  }
})

const extension = computed(() => {
  return props.filename.split('.').pop().toLowerCase()
})

const iconComponent = computed(() => {
  const ext = extension.value
  
  const iconMap = {
    pdf: IconPdf,
    doc: IconWord,
    docx: IconWord,
    xls: IconExcel,
    xlsx: IconExcel,
    ppt: IconPowerpoint,
    pptx: IconPowerpoint,
    txt: IconText,
    md: IconMarkdown,
    jpg: IconImage,
    jpeg: IconImage,
    png: IconImage,
    gif: IconImage,
    svg: IconImage,
    webp: IconImage,
    bmp: IconImage,
    py: IconPython,
    js: IconJs,
    ts: IconTs,
    vue: IconVue,
    html: IconHtml,
    htm: IconHtml,
    css: IconCss,
    json: IconJson,
    java: IconJava,
    go: IconGo,
    rs: IconRust,
    c: IconC,
    cpp: IconCpp,
    cc: IconCpp,
    cxx: IconCpp,
    sql: IconSql,
    zip: IconZip,
    rar: IconZip,
    '7z': IconZip,
    tar: IconZip,
    gz: IconZip,
    csv: IconExcel,
    xml: IconHtml,
  }
  
  return iconMap[ext] || IconDefault
})

const iconSize = computed(() => {
  return typeof props.size === 'number' ? `${props.size}px` : props.size
})
</script>

<template>
  <component :is="iconComponent" class="file-icon" :style="{ width: iconSize, height: iconSize }" />
</template>

<style scoped>
.file-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
</style>
