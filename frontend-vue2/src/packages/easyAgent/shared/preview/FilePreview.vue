<template>
    <div v-if="visible" class="preview-overlay" :class="{ 'is-inline': inline }" @mousedown="overlayPress = markOverlayPress($event)"
    @click.self="onOverlayClick">
      <div class="preview-dialog">
        <div class="preview-header">
          <h3 class="preview-title">{{ filename }}</h3>
          <div class="header-actions">
            <button v-if="!expertId" class="download-btn" @click="handleDownload" title="下载文件">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                <polyline points="7 10 12 15 17 10"></polyline>
                <line x1="12" y1="15" x2="12" y2="3"></line>
              </svg>
            </button>
            <button class="close-btn" @click="handleClose">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <line x1="18" y1="6" x2="6" y2="18"></line>
                <line x1="6" y1="6" x2="18" y2="18"></line>
              </svg>
            </button>
          </div>
        </div>
        <div class="preview-content">
          <div v-if="loading" class="preview-loading">
            <div class="spinner"></div>
            <span>加载中...</span>
          </div>
          <div v-else-if="error" class="preview-error" :class="{ 'is-pptx': isPptx }">
            <svg v-if="!isPptx" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="10"></circle>
              <line x1="12" y1="8" x2="12" y2="12"></line>
              <line x1="12" y1="16" x2="12.01" y2="16"></line>
            </svg>
            <p>{{ error }}</p>
          </div>
          <div v-else-if="isPdf" class="preview-pdf">
            <iframe :src="`${resolveUrl(previewUrl)}#navpanes=0&view=FitH`" class="pdf-iframe" title="PDF 预览"></iframe>
          </div>
          <div v-else-if="isPdfConverted" class="preview-pptx">
            <iframe v-if="pptxPdfUrl" :src="`${pptxPdfUrl}#navpanes=0&view=FitH`" class="pptx-iframe" title="PPTX 预览"></iframe>
          </div>
          <div v-else-if="isDocx" class="preview-docx">
            <DocxPreview v-if="docxUrl" :file-url="docxUrl" />
          </div>
          <div v-else-if="isExcel" class="preview-excel">
            <ExcelPreview v-if="excelUrl" :file-url="excelUrl" />
          </div>
          <div v-else-if="isImage" class="preview-image">
            <img :src="resolveUrl(previewUrl)" :alt="filename" />
          </div>
          <div v-else-if="isCsv" class="preview-csv">
            <div class="csv-table-wrapper">
              <table class="csv-table">
                <thead>
                  <tr>
                    <th class="row-num">#</th>
                    <th v-for="(header, hi) in csvData.headers" :key="hi">{{ header }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(row, ri) in csvData.rows" :key="ri">
                    <td class="row-num">{{ ri + 1 }}</td>
                    <td v-for="(cell, ci) in row" :key="ci">{{ cell }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <div v-else-if="isMarkdown" class="preview-markdown">
            <div class="markdown-body" v-html="renderedMarkdown"></div>
          </div>
          <div v-else-if="isHtml" class="preview-html">
            <iframe :src="htmlUrl" class="html-iframe" sandbox="allow-same-origin"></iframe>
          </div>
          <div v-else-if="isText" class="preview-text">
            <!-- 文本类按行渲染：行号与内容在同一个 flex 行里，长行软换行只会让这一行变高。
                 以前是「独立行号列 + pre-wrap 正文」，折行占掉视觉行却不产生行号，
                 行号列从第一个折行处开始整体上移，末尾还会出现「行号用完了、正文还在继续」。 -->
            <div v-if="plainTextMode" class="code-lines" :style="{ '--ln-width': gutterWidth }">
              <div v-for="line in visibleLines" :key="line.n" class="code-line">
                <span class="ln">{{ line.n }}</span>
                <!-- 用 span 而不是 code：主题里的 `.easy-agent-root[data-theme="dark"] code` 带 !important 背景，
                     每行一个 code 会变成一条条色带 -->
                <span class="line-text">{{ line.text }}</span>
              </div>
            </div>
            <!-- 代码类整段高亮：hljs 的 token 会跨行（多行注释/字符串），按行切会切断标签；
                 pre 不折行，一个源码行恒等于一个视觉行，独立行号列才对得上 -->
            <div v-else class="code-block">
              <div class="line-numbers">
                <span v-for="n in shownLineCount" :key="n">{{ n }}</span>
              </div>
              <pre v-html="highlightedCode"></pre>
            </div>
            <!-- 提示条单独渲染、不参与行号：以前把「内容过长已截断」拼在正文里，会被当成正文最后两行 -->
            <div v-if="textNotice" class="preview-notice">
              <span>{{ textNotice }}</span>
              <button v-if="hasMoreLines" class="preview-more-btn" @click="loadMoreLines">加载更多</button>
            </div>
          </div>
          <div v-else class="preview-unsupported">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
              <polyline points="14 2 14 8 20 8"></polyline>
              <line x1="12" y1="18" x2="12" y2="12"></line>
              <line x1="9" y1="15" x2="15" y2="15"></line>
            </svg>
            <p>该文件类型暂不支持在线预览</p>
            <button class="download-fallback-btn" @click="handleDownload">下载文件</button>
          </div>
        </div>
      </div>
    </div>
</template>

<script>
import {
  setupMarkedExtensions,
  createMarkdownRenderer,
  renderMarkdown,
  installCodeCopyHandler,
  escapeHtml,
} from '../../markdownSetup.js'
import hljs from '../markdown/hljs.js'
import '../../styles/highlight-github-dark.css'
// .preview-markdown 内的文档样式在 styles/markdown.css（随 markdownSetup.js 引入，不依赖 ::v-deep）
import { overlayClickCloses, markOverlayPress } from '../../utils/overlayClick.js'
// 预览库按需加载：docx/excel/xlsx 合计数 MB，只有真正打开对应文件时才拉取
let xlsxPromise = null
function loadXlsx() {
  if (!xlsxPromise) xlsxPromise = import('xlsx')
  return xlsxPromise
}
import { getStoredToken } from '../../api/auth.js'
import { getExpertMd } from '../../api/expert.js'
import { requestBlob, requestArrayBuffer, requestText, resolveUrl } from '../../utils/request.js'
// 行号几何（按行切分 / 分页 / 列宽）：纯函数，两个前端的 FilePreview 共用
import {
  clampSourceText,
  lineNumberGutterWidth,
  splitSourceLines,
  takeLines,
  TEXT_CHAR_LIMIT,
  TEXT_LINE_PAGE,
} from './textLines.js'

// 注册 KaTeX 数学公式 + emoji 短代码扩展（幂等，仅执行一次）
setupMarkedExtensions()

const imageExts = ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.svg', '.ico']
const textExts = ['.txt', '.json', '.jsonl', '.xml', '.csv', '.js', '.ts', '.vue', '.py', '.java', '.go', '.rs', '.c', '.cpp', '.h', '.hpp', '.sh', '.bat', '.css', '.scss', '.less', '.sql', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf', '.env', '.log', '.md', '.jsx', '.tsx', '.rb', '.php', '.swift', '.kt', '.scala', '.lua', '.pl', '.r', '.dart', '.ex', '.exs', '.erl', '.hs', '.ml', '.jl', '.tf', '.proto', '.graphql', '.makefile', '.cmake', '.dockerfile', '.gitignore', '.properties', '.gradle']

// 安全获取文件扩展名（无扩展名时返回空字符串）
function getExt(name) {
  if (!name) return ''
  const parts = String(name).split('.')
  if (parts.length < 2) return ''
  return parts.pop().toLowerCase()
}

// 代码语言映射：文件扩展名 → highlight.js 语言标识
const codeLangMap = {
  js: 'javascript', jsx: 'javascript', ts: 'typescript', tsx: 'typescript',
  vue: 'xml', html: 'xml', htm: 'xml', xml: 'xml',
  py: 'python', java: 'java', go: 'go', rs: 'rust',
  c: 'c', cpp: 'cpp', h: 'c', hpp: 'cpp',
  sh: 'bash', bat: 'bat',
  css: 'css', scss: 'scss', less: 'less',
  sql: 'sql', json: 'json', yaml: 'yaml', yml: 'yaml',
  toml: 'ini', rb: 'ruby', php: 'php', swift: 'swift',
  kt: 'kotlin', lua: 'lua', pl: 'perl', r: 'r',
  dart: 'dart', tf: 'hcl', proto: 'protobuf', graphql: 'graphql',
}

// markdown 预览：与聊天区共用代码块结构（语言标签 + 复制按钮），
// 高亮走 highlight.js（亮黑主题），未识别语言时按自动识别处理
function highlightWithHljs(code, lang) {
  const raw = (lang || '').toLowerCase()
  const resolved = raw ? codeLangMap[raw] || raw : ''
  let inner = ''
  if (resolved && hljs.getLanguage(resolved)) {
    try {
      inner = hljs.highlight(code, { language: resolved }).value
    } catch (e) {
      console.warn('[FilePreview] 语法高亮失败:', raw, e)
    }
  }
  if (!inner) inner = hljs.highlightAuto(code).value
  const langClass = resolved ? ` class="language-${escapeHtml(resolved)}"` : ''
  return `<pre class="hljs"><code${langClass}>${inner}</code></pre>`
}

const mdRenderer = createMarkdownRenderer({ highlight: highlightWithHljs })

// 代码块复制按钮的全局处理函数（幂等），与聊天区共用
installCodeCopyHandler()

export default {
  components: {
    // 按需加载：docx-preview / exceljs 只在打开对应类型文件时才进内存
    DocxPreview: () => import('./DocxPreview.vue'),
    ExcelPreview: () => import('./ExcelPreview.vue'),
  },
  props: {
    filename: {
      type: String,
      default: ''
    },
    filePath: {
      type: String,
      default: ''
    },
    // 专家提示词 MD（只读虚拟文件）：非空时正文走 `/agent/expert-md` 现取，
    // 不用 filePath；下载按钮同时隐藏（无磁盘文件可下）。
    expertId: {
      type: String,
      default: ''
    },
    sessionId: {
      type: String,
      default: null
    },
    taskId: {
      type: String,
      default: null
    },
    visible: {
      type: Boolean,
      default: false
    },
    // 内嵌模式：预览只占据「父容器」范围，不再铺满视口、也不加全屏黑遮罩。
    // 用于工作区（WorkspacePanel）—— 这样预览文件时聊天区仍然可见，
    // 会话时间区域、任务规划区域不会被盖住。默认 false 保持原有模态行为。
    inline: {
      type: Boolean,
      default: false
    }
  },
  data() {
    return {
      overlayPress: null,
      loading: false,
      error: '',
      textContent: '',
      // 文本预览一次渲染的行数上限：超过就分页（行号始终与文件实际行对应）
      maxLines: TEXT_LINE_PAGE,
      // 文件极大、超出字符兜底上限时为 true（只影响文本预览的提示条）
      textTruncated: false,
      previewUrl: '',
      docxUrl: '',
      excelUrl: '',
      // PPTX：经后端 LibreOffice 转为 PDF 后以 iframe 预览
      pptxPdfUrl: '',
      excelArrayBuffer: null,
      excelFallback: false,
      excelSheets: [],
      excelRetried: false,
      excelKey: 0,
      // Excel 兜底逻辑保留给后续扩展，当前预览由 ExcelPreview 组件承接。
      htmlUrl: '',
    }
  },
  computed: {
    isImage() {
      const ext = getExt(this.filename)
      return ext && imageExts.includes('.' + ext)
    },
    isPdf() {
      return getExt(this.filename) === 'pdf'
    },
    isPptx() {
      return getExt(this.filename) === 'pptx'
    },
    // PPTX 与老版 PPT/DOC 都没有可用的前端渲染器：统一交后端 LibreOffice 转 PDF。
    isPdfConverted() {
      return ['pptx', 'ppt', 'doc'].includes(getExt(this.filename))
    },
    isDocx() {
      return getExt(this.filename) === 'docx'
    },
    isExcel() {
      return ['xlsx', 'xls'].includes(getExt(this.filename))
    },
    isMarkdown() {
      return getExt(this.filename) === 'md'
    },
    isHtml() {
      return ['html', 'htm'].includes(getExt(this.filename))
    },
    isCsv() {
      return getExt(this.filename) === 'csv'
    },
    isText() {
      const ext = getExt(this.filename)
      return ext && textExts.includes('.' + ext)
    },
    // 预览目标由 (visible, filename, filePath) 共同决定，需要覆盖三种场景：
    //   1) 常驻挂载 + visible 由 false→true（素材面板）；
    //   2) 父级用 v-if 控制、挂载时 visible 已为 true（定时任务面板）—— 靠 immediate 兜住；
    //   3) 多标签切换：visible 始终为 true，只有 filePath/filename 变化（工作区预览）。
    // 只监听 visible 会漏掉 2 和 3，表现为预览区一直空白。
    previewTarget() {
      // expertId 参与比对：同为虚拟 MD 的两个专家切换时也能重新取正文
      return `${this.visible}|${this.filePath}|${this.expertId}|${this.filename}`
    },
    isCode() {
      const ext = getExt(this.filename)
      const codeExts = ['js', 'ts', 'vue', 'py', 'java', 'go', 'rs', 'c', 'cpp', 'h', 'hpp', 'sh', 'bat', 'css', 'scss', 'less', 'sql', 'html', 'htm', 'xml', 'json', 'yaml', 'yml', 'toml', 'jsx', 'tsx', 'rb', 'php', 'swift', 'kt', 'lua', 'pl', 'r', 'dart', 'tf', 'proto', 'graphql']
      return codeExts.includes(ext)
    },
    highlightedCode() {
      // 只高亮当前这一页：超大文件整篇交给 hljs 会很慢，且行号只覆盖可见页
      const code = this.visibleText
      if (!code) return ''
      const ext = getExt(this.filename)
      const lang = codeLangMap[ext]

      // 如果是代码文件且有对应语言，使用语法高亮
      if (this.isCode && lang && hljs.getLanguage(lang)) {
        try {
          return hljs.highlight(code, { language: lang }).value
        } catch (e) {
          console.warn('[FilePreview] 语法高亮失败:', ext, e)
        }
      }

      // 不再对 JSON 做 parse → stringify 重排版：行号必须对应文件里的实际行，
      // 重排版（尤其是压缩成一行的 JSON）会让行号整篇错位；.json 走上面的 hljs 高亮即可

      // 普通文本，转义 HTML
      return escapeHtml(code)
    },
    // ---- 文本预览的行号：按行切分、按行分页，行号与正文同源（见 ./textLines.js）----
    sourceLines() {
      return this.isText ? splitSourceLines(this.textContent) : []
    },
    totalLineCount() {
      return this.sourceLines.length
    },
    visibleLines() {
      return takeLines(this.sourceLines, this.maxLines).lines
    },
    shownLineCount() {
      return this.visibleLines.length
    },
    hasMoreLines() {
      return this.shownLineCount < this.totalLineCount
    },
    /** 本页源文本：代码高亮以它为准，不含分页提示条 */
    visibleText() {
      return this.visibleLines.map((line) => line.text).join('\n')
    },
    /** 行号列宽：整列统一，位数不同的行号才不会左右错位 */
    gutterWidth() {
      return lineNumberGutterWidth(this.totalLineCount)
    },
    /** 文本按行渲染；代码保留整段高亮（hljs 的 token 会跨行，不能按行切） */
    plainTextMode() {
      return !this.isCode
    },
    textNotice() {
      const parts = []
      if (this.textTruncated) parts.push(`文件过大，仅载入前 ${TEXT_CHAR_LIMIT / 10000} 万字符`)
      if (this.hasMoreLines) parts.push(`已显示前 ${this.shownLineCount} 行 / 共 ${this.totalLineCount} 行`)
      return parts.join('；')
    },
    csvData() {
      if (!this.textContent) return { headers: [], rows: [] }
      const lines = this.textContent.split('\n').filter(l => l.trim())
      if (lines.length === 0) return { headers: [], rows: [] }

      const parseLine = (line) => {
        const result = []
        let current = ''
        let inQuotes = false
        for (let i = 0; i < line.length; i++) {
          const char = line[i]
          if (char === '"' && line[i + 1] === '"') {
            current += '"'
            i++
          } else if (char === '"') {
            inQuotes = !inQuotes
          } else if (char === ',' && !inQuotes) {
            result.push(current)
            current = ''
          } else {
            current += char
          }
        }
        result.push(current)
        return result
      }

      const headers = parseLine(lines[0])
      const rows = lines.slice(1, 1000).map(parseLine) // 限制最多 1000 行
      return { headers, rows }
    },
    // 预览/下载基础 URL：定时任务工作目录走独立端点，否则走会话文件端点
    previewBaseUrl() {
      return this.taskId
        ? `/agent/scheduled-tasks/${this.taskId}/workspace/file`
        : '/agent/files/preview'
    },
    renderedMarkdown() {
      if (!this.textContent) return ''
      return renderMarkdown(this.textContent, mdRenderer, { sourceCards: false, remoteImages: 'inline', rawHtml: 'allow' })
    }
  },
  watch: {
    previewTarget: {
      immediate: true,
      handler() {
        if (this.visible && this.filename) {
          this.loadPreview()
        }
      }
    },
  },
  methods: {
    resolveUrl, // previewUrl 存未拼前缀的路径（axios 自行解析）；iframe / img 直接加载时才拼前缀
    /** 遮罩点击关闭守卫：拖拽选择文本时（有非空选区）不关闭预览 */
    onOverlayClick(event) {
      overlayClickCloses(event, this.overlayPress, () => this.handleClose())
    },
    markOverlayPress(event) {
      return markOverlayPress(event)
    },

    handleDownload() {
      const token = getStoredToken()
      const params = new URLSearchParams()
      params.set('file_path', this.filePath)
      if (this.sessionId) params.set('session_id', this.sessionId)
      if (token) params.set('token', token)
      params.set('download', 'true')
      const url = resolveUrl(`${this.previewBaseUrl}?${params.toString()}`)
      const link = document.createElement('a')
      link.href = url
      link.download = this.filename
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
    },
    /** 文本预览「加载更多」：每次多渲染一页行（数据已在内存，无需重新请求） */
    loadMoreLines() {
      this.maxLines += TEXT_LINE_PAGE
    },
    async loadPreview() {
      // 每次加载分配递增序号：异步返回后若已有更新的加载，丢弃旧结果，
      // 避免快速切换标签时旧请求覆盖新标签内容。
      const seq = (this._loadSeq = (this._loadSeq || 0) + 1)
      this.loading = true
      this.error = ''
      this.textContent = ''
      this.maxLines = TEXT_LINE_PAGE
      this.textTruncated = false
      if (this.docxUrl) {
        URL.revokeObjectURL(this.docxUrl)
      }
      if (this.excelUrl) {
        URL.revokeObjectURL(this.excelUrl)
      }
      this.docxUrl = ''
      this.excelUrl = ''
      if (this.pptxPdfUrl) {
        URL.revokeObjectURL(this.pptxPdfUrl)
      }
      this.pptxPdfUrl = ''
      if (this.htmlUrl) {
        URL.revokeObjectURL(this.htmlUrl)
        this.htmlUrl = ''
      }

      const ts = new Date().toISOString()
      // 统一打印预览请求日志：文件名、路径、会话ID
      console.log(
        `[${ts}] [FilePreview] 预览请求 | 文件名: ${this.filename} | 路径: ${this.filePath} | 会话: ${this.sessionId || '无'}| 专家: ${this.expertId || '无'}`
      )

      // 专家提示词 MD：只读虚拟文件，正文按 expertId 现取（无磁盘路径）
      if (this.expertId) {
        try {
          const data = await getExpertMd(this.expertId)
          if (seq !== this._loadSeq) return
          this.textContent = (data && data.content) || ''
          if (!this.textContent) this.error = '该专家暂无可读的提示词文件'
        } catch (e) {
          if (seq !== this._loadSeq) return
          this.error = (e && e.message) || '加载专家提示词失败'
        } finally {
          if (seq === this._loadSeq) this.loading = false
        }
        return
      }

      // 校验必要参数
      if (!this.filePath) {
        this.error = '文件路径为空，无法预览'
        console.error(
          `[${ts}] [FilePreview] loadPreview 失败: filePath 为空`,
          { filename: this.filename, sessionId: this.sessionId }
        )
        this.loading = false
        return
      }

      const token = getStoredToken()

      // 构建预览 URL：定时任务工作目录走独立端点，否则走会话文件端点
      const params = new URLSearchParams()
      params.set('file_path', this.filePath)
      if (this.sessionId) params.set('session_id', this.sessionId)
      if (token) params.set('token', token)
      this.previewUrl = `${this.previewBaseUrl}?${params.toString()}`
      console.log(
        `[${ts}] [FilePreview] 加载预览 | 文件: ${this.filename} | 类型: ${getExt(this.filename) || '无扩展名'} | URL: ${this.previewUrl}`
      )

      const ext = getExt(this.filename)

      // 构建 auth headers
      const headers = token ? { 'Authorization': `Bearer ${token}` } : {}

      try {
        if (this.isPdf) {
          console.log('[FilePreview] PDF 预览')
        } else if (this.isPdfConverted) {
          console.log('[FilePreview] Office → PDF 预览 (LibreOffice)')
          // 后端用 LibreOffice 把 pptx/ppt/doc 转成 PDF，返回 PDF 流，由浏览器内置查看器渲染
          const pdfParams = new URLSearchParams(params)
          pdfParams.set('target', 'pdf')
          const pdfUrl = `${this.previewBaseUrl}?${pdfParams.toString()}`
          const blob = await requestBlob({ url: pdfUrl, headers })
          if (seq !== this._loadSeq) return
          this.pptxPdfUrl = URL.createObjectURL(blob)
        } else if (this.isDocx) {
          console.log('[FilePreview] DOCX 预览')
          const arrayBuffer = await requestArrayBuffer({ url: this.previewUrl, headers })
          if (seq !== this._loadSeq) return
          this.docxUrl = URL.createObjectURL(
            new Blob([arrayBuffer], {
              type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            })
          )
        } else if (this.isExcel) {
          console.log('[FilePreview] Excel 预览')
          const arrayBuffer = await requestArrayBuffer({ url: this.previewUrl, headers })
          if (seq !== this._loadSeq) return
          this.excelUrl = URL.createObjectURL(
            new Blob([arrayBuffer], {
              type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            })
          )
        } else if (this.isHtml) {
          console.log('[FilePreview] HTML 预览')
          const htmlText = await requestText({ url: this.previewUrl, headers })
          if (seq !== this._loadSeq) return
          const blob = new Blob([htmlText], { type: 'text/html; charset=utf-8' })
          this.htmlUrl = URL.createObjectURL(blob)
        } else if (this.isMarkdown || this.isCsv) {
          let text = await requestText({ url: this.previewUrl, headers })
          if (seq !== this._loadSeq) return
          // Markdown / CSV 是整篇渲染（不涉及行号），继续按字符兜底
          if (text.length > 50000) {
            text = text.substring(0, 50000) + '\n\n... (内容过长已截断)'
          }
          // 剥掉 UTF-8 BOM（Excel 导出的 CSV 常带），否则首列表头会混入不可见的 \uFEFF
          this.textContent = text.replace(/^\uFEFF/, '')
          console.log('[FilePreview] 文本预览加载完成, 长度:', this.textContent.length)
        } else if (this.isText) {
          // 文本预览不按字符截断：行号必须与文件实际行一一对应，渲染量由 maxLines 分页控制。
          // 只有超大文件才按行边界兜底裁一次（见 ./textLines.js），裁完的末尾仍是完整行。
          const text = await requestText({ url: this.previewUrl, headers })
          if (seq !== this._loadSeq) return
          const clamped = clampSourceText(text)
          this.textTruncated = clamped.truncated
          // 剥掉 UTF-8 BOM，否则首行行号会混入不可见的 \uFEFF
          this.textContent = clamped.text.replace(/^\uFEFF/, '')
          console.log('[FilePreview] 文本预览加载完成, 长度:', this.textContent.length, '行数:', this.totalLineCount)
        } else {
          console.log('[FilePreview] 不支持预览的文件类型:', ext || '未知')
        }
      } catch (e) {
        if (seq !== this._loadSeq) return
        console.error('[FilePreview] 预览加载失败:', e, { filename: this.filename, filePath: this.filePath })
        this.error = ext === 'pptx' ? 'PPTX 预览失败，请下载后查看' : `加载失败: ${e.message}`
      }

      if (seq !== this._loadSeq) return
      this.loading = false
    },
    async handleExcelError(e) {
      console.error('Excel error:', e)
      if (!this.excelRetried && this.excelArrayBuffer) {
        // 复杂 xlsx 可能包含 drawing，先做一次清理重试。
        // 用 SheetJS 做一次 read→write 往返，生成“不含 drawing”的干净工作簿，
        // 清理后的工作簿仍保留边框、合并单元格和多 sheet。
        // 与“无图片时正常显示”体验一致。
        try {
          const XLSX = await loadXlsx()
          const wb = XLSX.read(this.excelArrayBuffer, { type: 'array', cellStyles: true })
          const cleaned = XLSX.write(wb, { type: 'array', bookType: 'xlsx' })
          this.excelRetried = true
          this.excelUrl = cleaned
          this.excelKey++
          return
        } catch (err) {
          console.error('[FilePreview] 去 drawing 重试失败:', err)
        }
      }
      // 仍失败（极少见）：用 SheetJS 把各 sheet 渲染为 HTML 表格兜底
      if (this.excelArrayBuffer) {
        this.buildExcelTables(this.excelArrayBuffer)
      } else {
        this.error = 'Excel 预览加载失败'
      }
    },
    handleClose() {
      // pptx/docx/excel/html 均为 Blob URL，需释放
      if (this.docxUrl) URL.revokeObjectURL(this.docxUrl)
      if (this.excelUrl) URL.revokeObjectURL(this.excelUrl)
      if (this.pptxPdfUrl) URL.revokeObjectURL(this.pptxPdfUrl)
      if (this.htmlUrl) URL.revokeObjectURL(this.htmlUrl)
      this.$emit('close')
    },
  },
}
</script>

<style scoped>
.preview-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.6);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9999;
}

.preview-dialog {
  width: 90%;
  max-width: 1200px;
  height: 85%;
  background: white;
  border-radius: 16px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

/* 内嵌模式（工作区内预览）：限定在「父容器」范围内定位 —— 不铺满视口、
   不加全屏黑遮罩，因此聊天区的会话时间、任务规划等区域在预览时依然可见。 */
.preview-overlay.is-inline {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(15, 23, 42, 0.04);
  z-index: 50;
}

.preview-overlay.is-inline .preview-dialog {
  width: 100%;
  height: 100%;
  max-width: none;
  border-radius: 0;
  box-shadow: none;
}

/* inline（工作区内预览）不显示自带头部：文件名与关闭按钮已由工作区标签栏承担，
   重复展示既冗余又白占一行高度。下载入口移到标签上（见 WorkspacePanel）。 */
.preview-overlay.is-inline .preview-header {
  display: none;
}

.preview-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid #e5e7eb;
  background: #f9fafb;
  flex-shrink: 0;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.download-btn {
  width: 32px;
  height: 32px;
  border: none;
  background: transparent;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  color: #6b7280;
  transition: all 0.2s;
}

.download-btn:hover {
  background: #e0f2fe;
  color: #0284c7;
}

.download-btn svg {
  width: 18px;
  height: 18px;
}

.preview-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: #111827;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.close-btn {
  width: 32px;
  height: 32px;
  border: none;
  background: transparent;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  color: #6b7280;
  transition: all 0.2s;
}

.close-btn:hover {
  background: #f3f4f6;
  color: #111827;
}

.close-btn svg {
  width: 20px;
  height: 20px;
}

.preview-content {
  flex: 1;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.preview-loading,
.preview-error,
.preview-unsupported {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #6b7280;
  gap: 12px;
}

.preview-error {
  color: #ef4444;
}

.preview-error.is-pptx {
  color: #6b7280;
}

.preview-unsupported svg {
  width: 64px;
  height: 64px;
  color: #d1d5db;
}

.download-fallback-btn {
  margin-top: 8px;
  padding: 8px 20px;
  background: #3b82f6;
  color: white;
  border: none;
  border-radius: 8px;
  font-size: 14px;
  cursor: pointer;
  transition: background 0.2s;
}

.download-fallback-btn:hover {
  background: #2563eb;
}

.preview-image {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: #f8fafc;
}

.preview-image img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

.preview-pdf {
  width: 100%;
  height: 100%;
  overflow: hidden;
}

.pdf-iframe {
  width: 100%;
  height: 100%;
  border: none;
  background: #525659;
}

.preview-html {
  width: 100%;
  height: 100%;
  overflow: hidden;
}

.html-iframe {
  width: 100%;
  height: 100%;
  border: none;
  background: #fff;
}

.preview-pptx {
  width: 100%;
  flex: 1;
  min-height: 0;
  overflow: hidden;
  background: #fff;
}

/* PPTX 经后端 LibreOffice 转为 PDF，由浏览器内置查看器渲染 */
.pptx-iframe {
  width: 100%;
  height: 100%;
  border: none;
  background: #fff;
}

.preview-docx {
  width: 100%;
  height: 100%;
  overflow: auto;
}

.preview-docx ::v-deep .docx-preview {
  width: 100%;
  height: 100%;
}

.preview-excel {
  width: 100%;
  height: 100%;
  min-height: 400px;
  overflow: auto;
}

.preview-excel ::v-deep .excel-preview,
.preview-excel ::v-deep .vue-office-excel,
.preview-excel ::v-deep .x-spreadsheet {
  width: 100%;
  min-height: 400px;
}

/* Excel 兜底：SheetJS 生成的 HTML 表格 */
.excel-fallback {
  height: 100%;
  overflow: auto;
  padding: 16px;
  background: #fff;
}

.excel-sheet {
  margin-bottom: 24px;
}

.excel-sheet-name {
  font-size: 14px;
  font-weight: 600;
  color: #374151;
  margin-bottom: 8px;
  padding-bottom: 4px;
  border-bottom: 2px solid #e5e7eb;
}

.excel-table-wrap {
  overflow: auto;
}

.excel-table-wrap table {
  border-collapse: collapse;
  font-size: 13px;
}

.excel-table-wrap td,
.excel-table-wrap th {
  border: 1px solid #d0d7de;
  padding: 4px 10px;
  min-width: 64px;
  max-width: 480px;
  white-space: normal;
  word-break: break-word;
}

.preview-text {
  width: 100%;
  height: 100%;
  overflow: auto;
  background: #0d1117;
}

.preview-text .code-block {
  display: flex;
  font-family: 'Cascadia Code', 'Fira Code', 'JetBrains Mono', 'Consolas', monospace;
  font-size: 13px;
  line-height: 1.6;
  min-height: 100%;
}

.preview-text .line-numbers {
  display: flex;
  flex-direction: column;
  padding: 16px 8px 16px 16px;
  text-align: right;
  color: #6e7681;
  user-select: none;
  border-right: 1px solid #21262d;
  background: #161b22;
  flex-shrink: 0;
}

.preview-text .line-numbers span {
  display: block;
  min-height: 1.6em;
}

/* 文本类（txt / log / ini …）：一行一条记录，行号与内容同高 */
.preview-text .code-lines {
  padding: 16px 0;
  font-family: 'Cascadia Code', 'Fira Code', 'JetBrains Mono', 'Consolas', monospace;
  font-size: 13px;
  line-height: 1.6;
  /* 行号列宽由组件的 --ln-width 统一给出（见 shared/preview/textLines.js），各行右对齐 */
}

.preview-text .code-line {
  display: flex;
  align-items: flex-start;
}

.preview-text .code-line .ln {
  flex: 0 0 auto;
  width: var(--ln-width, 56px);
  box-sizing: border-box;
  padding-right: 8px;
  text-align: right;
  color: #6e7681;
  user-select: none;
  /* 每行的右边框拼起来就是连续的竖线，不必给整列铺背景 */
  border-right: 1px solid #21262d;
}

.preview-text .code-line .line-text {
  flex: 1 1 auto;
  min-width: 0;
  padding: 0 16px;
  color: #c9d1d9;
  font-family: inherit;
  font-size: inherit;
  white-space: pre-wrap;
  word-break: break-all;
}

/* 分页 / 超大文件提示：sticky 在底部，不占行号 */
.preview-text .preview-notice {
  position: sticky;
  bottom: 0;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 16px;
  font-size: 12px;
  color: #8b949e;
  background: #161b22;
  border-top: 1px solid #21262d;
}

.preview-text .preview-more-btn {
  padding: 2px 10px;
  border: 1px solid #30363d;
  border-radius: 4px;
  background: transparent;
  color: #c9d1d9;
  font-size: 12px;
  cursor: pointer;
}

.preview-text .preview-more-btn:hover {
  background: #21262d;
}

.preview-text pre {
  margin: 0;
  padding: 16px;
  color: #c9d1d9;
  /* 代码不折行：一个源码行恒等于一个视觉行，独立行号列才对得上；超宽横向滚动 */
  white-space: pre;
  flex: 1;
  min-width: 0;
  overflow-x: auto;
  background: #0d1117;
}

.preview-csv {
  width: 100%;
  height: 100%;
  overflow: auto;
  background: #ffffff;
}

.csv-table-wrapper {
  padding: 16px;
}

.csv-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.csv-table th,
.csv-table td {
  padding: 6px 12px;
  border: 1px solid #e2e8f0;
  text-align: left;
  white-space: nowrap;
}

.csv-table th {
  background: #f1f5f9;
  font-weight: 600;
  color: #1e293b;
  position: sticky;
  top: 0;
  z-index: 1;
}

.csv-table td {
  color: #334155;
}

.csv-table tbody tr:nth-child(even) {
  background: #f8fafc;
}

.csv-table tbody tr:hover {
  background: #e0f2fe;
}

.csv-table .row-num {
  background: #f1f5f9;
  color: #94a3b8;
  font-size: 11px;
  text-align: right;
  user-select: none;
  position: sticky;
  left: 0;
  z-index: 1;
}

.preview-markdown {
  width: 100%;
  height: 100%;
  overflow: auto;
  padding: 20px;
  background: #ffffff;
}

.spinner {
  width: 32px;
  height: 32px;
  border: 3px solid #e5e7eb;
  border-top-color: #3b82f6;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
