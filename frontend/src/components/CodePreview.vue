<template>
  <div class="code-preview-container" ref="containerRef"></div>
</template>

<script>
import { ref, onMounted, watch, onUnmounted, shallowRef, nextTick } from 'vue'
import * as monaco from 'monaco-editor'
export default {
  props: {
  content: {
    type: String,
    default: ''
  },
  language: {
    type: String,
    default: 'plaintext'
  },
  readOnly: {
    type: Boolean,
    default: true
  }
},
  setup(props, { emit }) {
const containerRef = ref(null)
const editor = shallowRef(null)

const languageMap = {
  'js': 'javascript',
  'ts': 'typescript',
  'py': 'python',
  'java': 'java',
  'go': 'go',
  'rs': 'rust',
  'cpp': 'cpp',
  'c': 'c',
  'h': 'c',
  'hpp': 'cpp',
  'cs': 'csharp',
  'php': 'php',
  'rb': 'ruby',
  'swift': 'swift',
  'kt': 'kotlin',
  'scala': 'scala',
  'vue': 'html',
  'jsx': 'javascript',
  'tsx': 'typescript',
  'html': 'html',
  'css': 'css',
  'scss': 'scss',
  'less': 'less',
  'json': 'json',
  'xml': 'xml',
  'yaml': 'yaml',
  'yml': 'yaml',
  'md': 'markdown',
  'txt': 'plaintext',
  'sh': 'shell',
  'bash': 'shell',
  'sql': 'sql',
  'graphql': 'graphql',
  'dockerfile': 'dockerfile',
  'makefile': 'makefile'
}

function getLanguage(ext) {
  return languageMap[ext?.toLowerCase()] || 'plaintext'
}

function initEditor() {
  if (!containerRef.value) return
  
  if (editor.value) {
    editor.value.dispose()
  }
  
  const content = props.content || ''
  const lang = getLanguage(props.language)
  
  editor.value = monaco.editor.create(containerRef.value, {
    value: content,
    language: lang,
    theme: 'vs-dark',
    readOnly: props.readOnly,
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
    lineNumbersMinChars: 3
  })
}

onMounted(() => {
  nextTick(() => {
    initEditor()
  })
})

watch(() => props.content, (newContent) => {
  if (editor.value) {
    const model = editor.value.getModel()
    if (model) {
      model.setValue(newContent || '')
    }
  } else {
    nextTick(() => {
      initEditor()
    })
  }
})

watch(() => props.language, (newLang) => {
  if (editor.value) {
    const model = editor.value.getModel()
    if (model) {
      monaco.editor.setModelLanguage(model, getLanguage(newLang))
    }
  }
})

onUnmounted(() => {
  if (editor.value) {
    editor.value.dispose()
    editor.value = null
  }
})

    return {
      containerRef,
      editor,
      getLanguage,
      initEditor,
      languageMap,
      monaco,
      nextTick,
      onMounted,
      onUnmounted,
      ref,
      shallowRef,
      watch,
    }
  },
}
</script>

<style scoped>
.code-preview-container {
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: #1e1e1e;
}
</style>
