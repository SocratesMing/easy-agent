<template>
  <div class="code-preview-container" ref="containerRef"></div>
</template>

<script>
import * as monaco from 'monaco-editor'

const languageMap = {
  js: 'javascript',
  ts: 'typescript',
  py: 'python',
  java: 'java',
  go: 'go',
  rs: 'rust',
  cpp: 'cpp',
  c: 'c',
  h: 'c',
  hpp: 'cpp',
  cs: 'csharp',
  php: 'php',
  rb: 'ruby',
  swift: 'swift',
  kt: 'kotlin',
  scala: 'scala',
  vue: 'html',
  jsx: 'javascript',
  tsx: 'typescript',
  html: 'html',
  css: 'css',
  scss: 'scss',
  less: 'less',
  json: 'json',
  xml: 'xml',
  yaml: 'yaml',
  yml: 'yaml',
  md: 'markdown',
  txt: 'plaintext',
  sh: 'shell',
  bash: 'shell',
  sql: 'sql',
  graphql: 'graphql',
  dockerfile: 'dockerfile',
  makefile: 'makefile',
}

// 与聊天代码块一致的「亮黑」配色（背景 #0d1117 / 前景 #c9d1d9 / 行号槽 #161b22）
const EDITOR_THEME = 'easy-agent-dark'
monaco.editor.defineTheme(EDITOR_THEME, {
  base: 'vs-dark',
  inherit: true,
  rules: [],
  colors: {
    'editor.background': '#0d1117',
    'editor.foreground': '#c9d1d9',
    'editorGutter.background': '#0d1117',
    'editorLineNumber.foreground': '#6e7681',
    'editorLineNumber.activeForeground': '#c9d1d9',
    'editor.lineHighlightBackground': '#161b22',
    'editor.selectionBackground': '#264f78',
    'editorIndentGuide.background1': '#21262d',
    'editorIndentGuide.activeBackground1': '#30363d',
    'editorWidget.background': '#161b22',
    'editorWidget.border': '#30363d',
  },
})

export default {
  name: 'CodePreview',
  props: {
    content: {
      type: String,
      default: '',
    },
    language: {
      type: String,
      default: 'plaintext',
    },
    readOnly: {
      type: Boolean,
      default: true,
    },
  },
  data() {
    return {
      editor: null,
    }
  },
  methods: {
    getLanguage(ext) {
      const key = ext ? String(ext).toLowerCase() : ''
      return languageMap[key] || 'plaintext'
    },
    initEditor() {
      const el = this.$refs.containerRef
      if (!el) return

      if (this.editor) {
        this.editor.dispose()
      }

      const content = this.content || ''
      const lang = this.getLanguage(this.language)

      this.editor = monaco.editor.create(el, {
        value: content,
        language: lang,
        theme: EDITOR_THEME,
        readOnly: this.readOnly,
        automaticLayout: true,
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        fontSize: 13,
        lineNumbers: 'on',
        renderLineHighlight: 'line',
        wordWrap: 'off',
        wrappingIndent: 'indent',
        contextmenu: false,
        folding: true,
        glyphMargin: false,
        lineDecorationsWidth: 10,
        lineNumbersMinChars: 3,
      })
    },
  },
  mounted() {
    this.$nextTick(() => {
      this.initEditor()
    })
  },
  watch: {
    content(newContent) {
      if (this.editor) {
        const model = this.editor.getModel()
        if (model) {
          model.setValue(newContent || '')
        }
      } else {
        this.$nextTick(() => {
          this.initEditor()
        })
      }
    },
    language(newLang) {
      if (this.editor) {
        const model = this.editor.getModel()
        if (model) {
          monaco.editor.setModelLanguage(model, this.getLanguage(newLang))
        }
      }
    },
  },
  beforeDestroy() {
    if (this.editor) {
      this.editor.dispose()
      this.editor = null
    }
  },
}
</script>

<style scoped>
.code-preview-container {
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: #0d1117;
}
</style>
