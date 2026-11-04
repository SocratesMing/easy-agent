// markdown 渲染单点：实现见 frontend-shared/markdown/index.js（同步到 ./shared/markdown/）。
// 本文件只负责引入 KaTeX / markdown 样式并把共享内核的 API 原样转出，
// 既有 `from '../markdownSetup.js'` 的引用路径与导出名保持不变。
import 'katex/dist/katex.min.css'
// 渲染产物（来源卡片、文件预览文档）的样式；普通选择器限定在 .easy-agent-root 内，不依赖 ::v-deep
import './styles/markdown.css'

export * from './shared/markdown/index.js'
