// markdown 渲染单点：实现见 frontend-shared/markdown/index.js（同步到 ./shared/markdown/）。
// 本文件只负责引入 KaTeX 样式并把共享内核的 API 原样转出，
// 既有 `from '../markdownSetup.js'` 的引用路径与导出名保持不变。
import 'katex/dist/katex.min.css'

export * from './shared/markdown/index.js'
