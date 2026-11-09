<script>
// 文件类型图标：模块自带的 SVG 字符串（assets/icons/vscode-icons.js，原为 `~icons/vscode-icons/*`）
import * as vscodeIcons from '../../assets/icons/vscode-icons.js'
const IconPdf = vscodeIcons.fileTypePdf2
const IconWord = vscodeIcons.fileTypeWord
const IconExcel = vscodeIcons.fileTypeExcel
const IconPowerpoint = vscodeIcons.fileTypePowerpoint
const IconText = vscodeIcons.fileTypeText
const IconMarkdown = vscodeIcons.fileTypeMarkdown
const IconImage = vscodeIcons.fileTypeImage
const IconPython = vscodeIcons.fileTypePython
const IconJs = vscodeIcons.fileTypeJsOfficial
const IconTs = vscodeIcons.fileTypeTypescriptOfficial
const IconVue = vscodeIcons.fileTypeVue
const IconHtml = vscodeIcons.fileTypeHtml
const IconCss = vscodeIcons.fileTypeCss
const IconJson = vscodeIcons.fileTypeJson
const IconJava = vscodeIcons.fileTypeJava
const IconGo = vscodeIcons.fileTypeGo
const IconRust = vscodeIcons.fileTypeRust
const IconC = vscodeIcons.fileTypeC
const IconCpp = vscodeIcons.fileTypeCpp
const IconSql = vscodeIcons.fileTypeSql
const IconZip = vscodeIcons.fileTypeZip
const IconDefault = vscodeIcons.defaultFile
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
