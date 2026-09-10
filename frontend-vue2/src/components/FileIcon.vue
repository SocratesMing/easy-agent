<script>
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

export default {
  name: 'FileIcon',
  props: {
    filename: {
      type: String,
      required: true,
    },
    size: {
      type: [Number, String],
      default: 48,
    },
  },
  computed: {
    extension() {
      return this.filename.split('.').pop().toLowerCase()
    },
    iconSvg() {
      const ext = this.extension

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
    },
    iconSize() {
      return typeof this.size === 'number' ? `${this.size}px` : this.size
    },
  },
}
</script>

<template>
  <span class="file-icon" :style="{ width: iconSize, height: iconSize }" v-html="iconSvg"></span>
</template>

<style scoped>
.file-icon {
  display: inline-block;
  line-height: 0;
  flex-shrink: 0;
}

.file-icon svg {
  display: block;
  width: 100%;
  height: 100%;
}
</style>
