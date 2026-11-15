// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/chat/messageSegments.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
/**
 * 助手消息的分段渲染模型（Vue 2 / Vue 3 共用的纯函数）。
 *
 * 一条助手消息的 blocks（思考 / 工具调用 / 正文，已按 order 排好）切成两类段：
 * - 正文段：原位、按正文样式常显。流式期间无法知道哪段是最终回答（SSE 只有 done），
 *   所以正文一律不挪位置、不降级样式，完成时也就不会跳动；
 * - 过程组：相邻的思考与工具调用合成一组，默认折叠成一行摘要，执行中只显示当前这一步。
 *
 * 为了让长任务的高度不随步骤数增长，工具调用前的短叙述（「我先看一下表格结构。」）不打断分组，
 * 而是作为这一步的说明并入过程组。只有它后面紧跟着思考 / 工具时才这样判定：仍在流式输出的
 * 正文可能就是最终回答，先按正文显示；最终回答后面没有过程块，永远不会被并入。
 * 长正文、带块级结构的正文（表格、代码块、列表、标题、引用、图片）照常显示并打断分组。
 */

import { hasChartEnvelope } from '../visual/envelope.js'

/** 短叙述的长度上限（trim 后的字符数）。超过即视为实质内容，保持正文显示 */
export const NARRATION_MAX_CHARS = 80

// 块级 Markdown：表格行、代码围栏、标题、引用、列表
const BLOCK_MARKDOWN = /^\s*(?:\||```|~~~|#{1,6}\s|>|[-*+]\s|\d+[.)]\s)/m
const IMAGE_MARKDOWN = /!\[[^\]]*\]\(/

/** 有专门 UI、不再渲染普通工具卡的工具 */
const HIDDEN_TOOL_NAMES = new Set(['write_todos', 'ask_user'])

/** 过程块：思考，或没有专门 UI 的工具调用 */
export function isProcessBlock(block) {
  if (!block) return false
  if (block.type === 'thinking') return true
  return block.type === 'tool_call' && !HIDDEN_TOOL_NAMES.has(block.tool_name)
}

/**
 * 工具调用是否在等用户作答：只认 pending_approval（卡片据它显示批准 / 拒绝按钮）。
 * approval_status 是展示态，答完一个中断后其余块可能还停在 pending，见 utils/hitlApproval.js。
 */
export function isPendingApproval(block) {
  return !!block && block.type === 'tool_call' && !!block.pending_approval
}

/** 是否为可并入过程组的短叙述：至多两行、不超过 maxChars、不含块级 Markdown 或图片 */
export function isNarrationText(text, maxChars = NARRATION_MAX_CHARS) {
  const s = String(text == null ? '' : text).trim()
  if (!s || s.length > maxChars) return false
  if (s.split('\n').length > 2) return false
  return !BLOCK_MARKDOWN.test(s) && !IMAGE_MARKDOWN.test(s)
}

/**
 * 切分助手消息。
 * @param {Array} blocks 已按 order 排好的 blocks
 * @returns {Array} `{ kind: 'text', key, block }` 或 `{ kind: 'process', key, items }`。
 *   块都复制一份并带上 origIndex（在 blocks 中的下标）；并入组的叙述带 `narration: true`。
 *   组的 key 取首块下标，流式追加不会变。
 */
export function buildMessageSegments(blocks, { narrationMaxChars = NARRATION_MAX_CHARS } = {}) {
  const list = Array.isArray(blocks) ? blocks : []
  const segments = []
  let group = null
  // 候选叙述：短正文先挂起，下一个有效块是思考 / 工具才并入组，否则（后面是正文或已到末尾）按正文显示
  let candidate = null

  const addToGroup = (item) => {
    if (!group) {
      group = { kind: 'process', key: `g${item.origIndex}`, items: [] }
      segments.push(group)
    }
    group.items.push(item)
  }
  const pushText = (block, index) => {
    group = null
    segments.push({ kind: 'text', key: `c${index}`, block: { ...block, origIndex: index } })
  }
  const flushCandidateAsText = () => {
    if (!candidate) return
    pushText(candidate.block, candidate.index)
    candidate = null
  }

  list.forEach((b, i) => {
    if (isProcessBlock(b)) {
      if (candidate) {
        addToGroup({ ...candidate.block, origIndex: candidate.index, narration: true })
        candidate = null
      }
      addToGroup({ ...b, origIndex: i })
      return
    }
    // 隐藏工具（write_todos / ask_user）与未知类型不展示，也不打断分组
    if (!b || b.type !== 'content') return
    const text = String(b.content == null ? '' : b.content)
    if (!text.trim()) return
    flushCandidateAsText()
    if (isNarrationText(text, narrationMaxChars)) {
      candidate = { block: b, index: i }
      return
    }
    pushText(b, i)
  })
  flushCandidateAsText()
  return segments
}

const TOOL_KINDS = {
  execute: 'execute',
  read_file: 'read',
  read_uploaded_file: 'read',
  write_file: 'write',
  edit_file: 'edit',
  delete: 'delete',
  ls: 'search',
  glob: 'search',
  grep: 'search',
  web_search: 'web',
  access_knowledge_original: 'knowledge',
  task: 'task',
}

/** 按「涉及几个文件」计数的类型：同一个文件读两次算一个 */
const FILE_KINDS = new Set(['read', 'write', 'edit', 'delete'])

const KIND_LABELS = {
  read: (n) => `读取 ${n} 个文件`,
  write: (n) => `写入 ${n} 个文件`,
  edit: (n) => `编辑 ${n} 个文件`,
  delete: (n) => `删除 ${n} 个文件`,
  execute: (n) => `执行 ${n} 条命令`,
  search: (n) => `搜索 ${n} 次`,
  web: (n) => `联网搜索 ${n} 次`,
  knowledge: (n) => `查阅知识库 ${n} 次`,
  task: (n) => `执行 ${n} 个子任务`,
  other: (n) => `调用工具 ${n} 次`,
}

/** 工具类型：execute / read / write / edit / delete / search / web / knowledge / task / other */
export function toolKind(toolName) {
  return TOOL_KINDS[toolName] || 'other'
}

function argsOf(block) {
  const a = block && block.arguments
  return a && typeof a === 'object' && !Array.isArray(a) ? a : {}
}

function filePathOf(block) {
  const a = argsOf(block)
  return String(a.file_path || a.path || a.filename || '')
}

/**
 * 过程组摘要：思考次数与总时长、各类工具数量（按首次出现排序）、失败与待审批数、最近一句叙述。
 */
export function summarizeProcessGroup(items) {
  const summary = {
    thinkingCount: 0,
    thinkingSeconds: 0,
    thinkingTimed: false,
    toolCount: 0,
    failedCount: 0,
    pendingCount: 0,
    kinds: [],
  }
  const byKind = new Map()
  for (const item of Array.isArray(items) ? items : []) {
    if (!item) continue
    if (item.type === 'thinking') {
      summary.thinkingCount += 1
      const seconds = Number(item.duration)
      if (item.duration != null && Number.isFinite(seconds)) {
        summary.thinkingSeconds += seconds
        summary.thinkingTimed = true
      }
      continue
    }
    if (item.type === 'content') {
      continue
    }
    if (item.type !== 'tool_call') continue
    summary.toolCount += 1
    if (item.success === false) summary.failedCount += 1
    if (isPendingApproval(item)) summary.pendingCount += 1
    const kind = toolKind(item.tool_name)
    let entry = byKind.get(kind)
    if (!entry) {
      entry = { kind, calls: 0, files: new Set() }
      byKind.set(kind, entry)
    }
    entry.calls += 1
    entry.files.add(filePathOf(item) || `#${entry.calls}`)
  }
  summary.kinds = [...byKind.values()].map((e) => ({
    kind: e.kind,
    count: FILE_KINDS.has(e.kind) ? e.files.size : e.calls,
  }))
  return summary
}

/** 摘要文字：「已思考 6 秒 · 读取 2 个文件 · 执行 1 条命令」 */
export function processSummaryText(summary) {
  const s = summary || {}
  const parts = []
  if (s.thinkingCount > 0) {
    parts.push(s.thinkingTimed ? `已思考 ${Math.max(1, Math.round(s.thinkingSeconds))} 秒` : '已思考')
  }
  for (const k of s.kinds || []) parts.push((KIND_LABELS[k.kind] || KIND_LABELS.other)(k.count))
  return parts.join(' · ')
}

function firstLine(value) {
  return String(value == null ? '' : value).split('\n')[0].trim()
}

function clip(text, max) {
  return text.length > max ? `${text.slice(0, max)}…` : text
}

function baseName(p) {
  return String(p || '').split('/').filter(Boolean).pop() || ''
}

/** 单个工具调用的动作描述，用于执行中的组标题：「执行 pytest -q」「读取 sales.xlsx」 */
export function toolActionLabel(block) {
  const a = argsOf(block)
  const name = block && block.tool_name ? String(block.tool_name) : ''
  switch (toolKind(name)) {
    case 'execute': return `执行 ${clip(firstLine(a.command), 60) || '命令'}`
    case 'read': return `读取 ${baseName(filePathOf(block)) || '文件'}`
    case 'write': return `写入 ${baseName(filePathOf(block)) || '文件'}`
    case 'edit': return `编辑 ${baseName(filePathOf(block)) || '文件'}`
    case 'delete': return `删除 ${baseName(filePathOf(block)) || '文件'}`
    case 'search': return `搜索 ${clip(firstLine(a.pattern || a.path), 40) || '文件'}`
    case 'web': return `联网搜索 ${clip(firstLine(a.query), 40)}`.trim()
    case 'knowledge': return '查阅知识库'
    case 'task': return `执行子任务 ${clip(firstLine(a.description), 40)}`.trim()
    default: return `调用 ${name || '工具'}`
  }
}

function liveTextOf(block) {
  if (!block) return ''
  if (block.type === 'thinking') return String(block.content || '')
  if (block.type !== 'tool_call') return ''
  if (typeof block.result === 'string' && block.result.trim()) return block.result
  const a = argsOf(block)
  switch (block.tool_name) {
    case 'write_file': return String(a.content || '')
    case 'edit_file': return String(a.new_string || '')
    case 'execute': return a.command ? `$ ${a.command}` : ''
    default: return ''
  }
}

/**
 * 执行中过程组的实时预览：从最后一块往前找第一段有内容的文字（思考内容、写入中的文件、
 * 命令及其输出），取末尾 maxChars 个字符。往前找是为了在两步之间不闪空。
 */
export function processLivePreview(items, maxChars = 360) {
  const list = Array.isArray(items) ? items : []
  for (let i = list.length - 1; i >= 0; i -= 1) {
    const text = liveTextOf(list[i]).trim()
    if (text) return text.length > maxChars ? text.slice(-maxChars) : text
  }
  return ''
}

/** 展开列表里可合并的连续同类调用：只合并「查看」类，命令和写入保持逐条 */
const MERGEABLE_KINDS = new Set(['read', 'search'])

function isSettledTool(block) {
  return block.type === 'tool_call' && block.duration != null &&
    block.success !== false && !isPendingApproval(block)
}

function rowOf(item) {
  const type = item.type === 'content' ? 'narration' : item.type === 'thinking' ? 'thinking' : 'tool'
  return { type, key: `i${item.origIndex}`, item }
}

/**
 * 展开后的行列表：叙述 / 思考 / 工具逐条展示；连续两个以上已完成的读取或搜索合并成一行
 * （「读取 3 个文件」），点开再看每一条。执行中、失败、待审批的调用不参与合并。
 */
export function processGroupRows(items) {
  const rows = []
  let run = null
  const flush = () => {
    if (!run) return
    if (run.items.length === 1) {
      rows.push(rowOf(run.items[0]))
    } else {
      rows.push({
        type: 'run',
        key: `r${run.items[0].origIndex}`,
        kind: run.kind,
        items: run.items,
        label: processSummaryText(summarizeProcessGroup(run.items)),
      })
    }
    run = null
  }
  for (const item of Array.isArray(items) ? items : []) {
    if (!item) continue
    const kind = item.type === 'tool_call' ? toolKind(item.tool_name) : ''
    if (MERGEABLE_KINDS.has(kind) && isSettledTool(item)) {
      if (run && run.kind === kind) {
        run.items.push(item)
        continue
      }
      flush()
      run = { kind, items: [item] }
      continue
    }
    flush()
    rows.push(rowOf(item))
  }
  flush()
  return rows
}

function runningTitle(items) {
  const last = items[items.length - 1]
  const previous = items[items.length - 2]
  let action = '正在处理'
  if (last && last.type === 'thinking' && last.duration == null) action = '正在思考'
  else if (last && last.type === 'tool_call' && last.duration == null) action = `正在${toolActionLabel(last)}`
  // 叙述说的是「接下来要做什么」，只挂在紧随其后的当前工具上，不能沿用到后续工具。
  const caption = last && last.type === 'tool_call' && previous && previous.type === 'content' && previous.narration
    ? String(previous.content || '').trim().replace(/[\s。．.！!？?：:；;，,、…]+$/u, '')
    : ''
  return `${caption ? `${caption} · ` : ''}${action}…`
}

/**
 * 过程组的展示模型。
 * @param {{ items: object[] }} group buildMessageSegments 产出的过程组
 * @param {{ running?: boolean }} options running：消息仍在流式输出，且这是最后一段
 * @returns {{ status: 'running'|'approval'|'done', title: string, summary: object,
 *   thinkingOnly: boolean, steps: number, preview: string, pinned: object[], visuals: object[], rows: object[] }}
 *   pinned 是待审批的工具调用、visuals 是结果里带图表的数据工具调用：组折叠时两者都要露出来
 *   （审批按钮和图都是给用户看的）；rows 见 processGroupRows。
 */
export function describeProcessGroup(group, { running = false } = {}) {
  const items = group && Array.isArray(group.items) ? group.items : []
  const summary = summarizeProcessGroup(items)
  const pinned = items.filter(isPendingApproval)
  const visuals = items.filter((item) => item.type === 'tool_call' && !isPendingApproval(item) && hasChartEnvelope(item.result))
  const status = pinned.length > 0 ? 'approval' : running ? 'running' : 'done'
  let title
  if (status === 'approval') title = `等待审批 · ${toolActionLabel(pinned[0])}`
  else if (status === 'running') title = runningTitle(items)
  else title = processSummaryText(summary) || '已处理'
  return {
    status,
    title,
    summary,
    // 组里只有思考块时，展开直接显示思考内容。只调了 write_todos 的那一步，组里会有叙述却没有
    // 可见的工具，这种组按普通列表展开，叙述不能被当成思考内容渲染。
    thinkingOnly: items.length > 0 && items.every((item) => item.type === 'thinking'),
    steps: summary.toolCount,
    preview: status === 'running' ? processLivePreview(items) : '',
    pinned,
    visuals,
    rows: processGroupRows(items),
  }
}
