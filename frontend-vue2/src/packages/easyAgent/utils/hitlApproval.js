import Vue from 'vue'

/**
 * HITL 审批的纯函数（DA-02）：approval_required 载荷 → 工具卡片标记 / 独立审批面板 / 恢复决策。
 *
 * 后端给每条待审批操作带 interrupt_id + action_index：并行子代理各自触发审批时，图里会同时
 * 挂着多个中断。前端按中断作答——点哪张卡片（或面板里哪一项）的按钮，就只回答它所属的中断，
 * 其余中断保持挂起，恢复流结束后后端会再次下发 approval_required。
 * 子代理内部的操作在主消息里没有对应的工具卡片，渲染在消息级的独立审批面板里。
 * 旧载荷（没有 interrupt_id，改造前落库的待审批记录）沿用「一次决策作用于整批」的旧行为。
 */

import { buildInteractionResumeDecisions, isInteractionAction } from '../shared/chat/planInteraction.js'

function isToolBlock (b) {
  return !!b && b.type === 'tool_call'
}

function pendingActions (message) {
  const pending = message && message.pending_approval
  return pending && Array.isArray(pending.action_requests) ? pending.action_requests.filter(Boolean) : []
}

/** 单条消息是否仍待审批：还有待审批卡片，或还有挂不上卡片的操作等着在消息级面板作答。 */
function messageHasPendingApproval (message) {
  const pending = message && message.pending_approval
  // 历史接口可能给数组形态（改造前的落库记录）
  if (Array.isArray(pending)) return pending.length > 0
  const blocks = (message && message.blocks) || []
  const hasPendingBlock = blocks.some(b => !!b && b.type === 'tool_call' && b.approval_status === 'pending')
  // 显式 null = 前端已作答（handleToolApproval 清空载荷），此时块上残留的 pending 不算数，
  // 否则就会出现「徽标一直亮、卡片却没有按钮」的死状态。
  if (pending === null) return false
  // 旧数据没有消息级载荷：只在这条消息自己身上按块状态兜底（不跨消息扫）
  if (!pending || !pending.thread_id) return hasPendingBlock
  if (hasPendingBlock) return true
  // 卡片都作答完了：主消息里没有对应卡片的操作（子代理内部审批）仍需作答
  return pendingActions(message).some(ar => {
    if (!ar || !ar.tool_call_id) return true
    const blk = blocks.find(b => !!b && b.type === 'tool_call' &&
      (b.tool_call_id === ar.tool_call_id || b.id === ar.tool_call_id))
    return !blk || blk.approval_status === 'pending'
  })
}

/**
 * 会话是否仍待审批 —— 会话列表「待审批」徽标的唯一判据（2026-11-07 DA-24）。
 *
 * 只认**最后一条 assistant 消息的消息级 pending_approval**，原因有两个：
 * 1. 块上的 approval_status 是展示态：答完一个中断后其余中断的块可能停在 pending，
 *    而按钮（看 pending_approval）已经收起 → 用块状态判徽标会出现「有徽标、没按钮」，
 *    且只有整页刷新重建历史才能消掉；
 * 2. 逐条扫全库历史时，任何一条旧消息的残留都会让徽标永久亮着。
 * 末尾出现用户消息说明已发起新一轮对话，上一条的审批不再阻塞会话。
 */
export function derivePendingApproval (messages) {
  const list = messages || []
  for (let i = list.length - 1; i >= 0; i--) {
    const msg = list[i]
    if (!msg) continue
    if (msg.role === 'user') return false
    if (msg.role !== 'assistant') continue
    return messageHasPendingApproval(msg)
  }
  return false
}

export function hasInterruptIds (actionRequests) {
  return (actionRequests || []).some(ar => ar && ar.interrupt_id)
}

/**
 * 恢复流里收到的 approval_required 是「新的待审批」，还是刚作答中断的重放？
 *
 * 不能只比 thread_id：一次运行（含其全部恢复流）的审批句柄是同一个，嵌套审批、
 * 并行子代理剩下的中断都带着同一个 thread_id。按 thread_id 判会把新审批当成重放，
 * 于是本地审批态被清空，而按钮仍由历史回填显示 —— 表现为「按钮点了没反应，刷新才行」。
 *
 * 判据改为中断身份：thread_id 变了，或出现本次**没有作答过**的 interrupt_id。
 * 旧载荷（没有 interrupt_id）只能退回 thread_id 比较。
 */
export function isNewApprovalEvent (data, answered = {}) {
  const threadId = data && data.thread_id
  if (!threadId) return false
  if (threadId !== answered.threadId) return true
  const answeredInterruptId = answered.interruptId || null
  const ids = (data.action_requests || [])
    .map(ar => ar && ar.interrupt_id)
    .filter(Boolean)
  if (!ids.length) return !answeredInterruptId
  return ids.some(id => id !== answeredInterruptId)
}

/**
 * 为一条待审批操作找到对应的工具卡片下标（找不到返回 -1）。
 * 新载荷只认 tool_call_id；对不上的是子代理内部操作，交给独立面板。
 * 旧载荷兜底按工具名找「尚未执行完」的最后一张卡片，避免标到早已执行过的同名调用上。
 */
export function findApprovalBlockIndex (blocks, ar, taken = new Set()) {
  const list = blocks || []
  if (!ar) return -1
  if (ar.tool_call_id) {
    const idx = list.findIndex((b, i) => !taken.has(i) && isToolBlock(b) &&
      (b.tool_call_id === ar.tool_call_id || b.id === ar.tool_call_id))
    if (idx !== -1) return idx
  }
  if (ar.interrupt_id || !ar.tool_name) return -1
  for (let i = list.length - 1; i >= 0; i--) {
    const b = list[i]
    if (!taken.has(i) && isToolBlock(b) && b.tool_name === ar.tool_name && b.duration == null) return i
  }
  return -1
}

/**
 * 把 approval_required 的操作挂到工具卡片上（原地修改 blocks），返回挂不上的操作。
 */
export function applyApprovalRequests (blocks, actionRequests) {
  const unmatched = []
  const taken = new Set()
  for (const ar of actionRequests || []) {
    const idx = findApprovalBlockIndex(blocks, ar, taken)
    if (idx === -1) {
      unmatched.push(ar)
      continue
    }
    taken.add(idx)
    // 换成新对象再 Vue.set：已观察过的工具块上新增 pending_approval / approval_status
    // 不会触发更新，卡片要等历史回填才展开。
    const blk = { ...blocks[idx], pending_approval: true, approval_status: 'pending' }
    if (ar.tool_call_id) {
      blk.tool_call_id = ar.tool_call_id
      blk.id = ar.tool_call_id
    }
    if (ar.file_paths && ar.file_paths.length > 0) blk.file_paths = ar.file_paths
    // 后端下发的可选项：删除类只有 approve/reject，卡片据此隐藏「编辑参数」
    if (Array.isArray(ar.allowed_decisions)) blk.allowed_decisions = ar.allowed_decisions
    if (ar.interrupt_id) blk.hitl_interrupt_id = ar.interrupt_id
    Vue.set(blocks, idx, blk)
  }
  return unmatched
}

/**
 * 消息仍待审批、但没有挂在任何待审批工具卡片上的操作（子代理内部的审批），供独立面板渲染。
 * 保证每条待审批操作都有可点的按钮。
 */
export function unmatchedApprovalRequests (message) {
  const actions = pendingActions(message)
  if (!actions.length) return []
  const blocks = (message && message.blocks) || []
  const taken = new Set()
  return actions.filter(ar => {
    if (isInteractionAction(ar)) return false
    const idx = findApprovalBlockIndex(blocks, ar, taken)
    if (idx === -1) return true
    const blk = blocks[idx]
    if (blk.pending_approval || blk.approval_status === 'pending') {
      taken.add(idx)
      return false
    }
    return true
  })
}

/**
 * 一条消息里「一个中断挂了多项操作」的分组：`[{ interruptId, actions, count }]`（DA-28）。
 *
 * 为什么必须整批处理：HITL 中间件是 batched 模式，把**同一条 AIMessage 里**命中审批的
 * 全部工具调用合并成一次中断下发（日志「N 个操作待审批」，action_index 0..N-1）；而 resume
 * 契约要求 decisions 数量与 action_requests 相等、且没有「先不决定」这类决策，所以
 * 「只批其中一项、其余继续挂起」在协议层表达不出来 —— 点任意一张卡片，整批必然一起作答。
 * 既然如此就不能让每张卡片各挂一个「批准」：用户以为只批了一项，实际批了一整批。
 * 展示层改为「多操作中断只有一个审批入口 + 操作清单」，按钮写明「批准全部 N 项」。
 */
export function batchedApprovalGroups (message) {
  const groups = new Map()
  for (const ar of pendingActions(message)) {
    if (!ar || isInteractionAction(ar) || !ar.interrupt_id) continue
    if (!groups.has(ar.interrupt_id)) groups.set(ar.interrupt_id, [])
    groups.get(ar.interrupt_id).push(ar)
  }
  return [...groups.entries()]
    .filter(([, list]) => list.length > 1)
    .map(([interruptId, list]) => ({
      interruptId,
      actions: list.slice().sort((a, b) => (a.action_index || 0) - (b.action_index || 0)),
      count: list.length,
    }))
}

/** 审批面板里一行操作的展示信息：`{ name, detail }`，detail 取路径 / 命令 / 参数摘要。 */
export function approvalActionSummary (action) {
  const args = (action && action.arguments) || {}
  const name = (action && (action.name || action.tool_name)) || '工具'
  const filePaths = (action && action.file_paths) || []
  let detail = ''
  if (typeof args.file_path === 'string') detail = args.file_path
  else if (Array.isArray(args.file_paths) && args.file_paths.length) detail = args.file_paths.join('、')
  else if (typeof args.path === 'string') detail = args.path
  else if (typeof args.command === 'string') detail = args.command
  else if (filePaths.length) detail = filePaths.join('、')
  else if (Object.keys(args).length) {
    try { detail = JSON.stringify(args) } catch (e) { detail = '' }
  }
  return { name, detail: detail.length > 140 ? `${detail.slice(0, 137)}...` : detail }
}

/** 工具卡片上的按钮回答哪个中断：卡片自身记录的，否则按 tool_call_id 到载荷里查。 */
export function approvalTargetForBlock (message, block) {
  const id = block && (block.tool_call_id || block.id)
  const ar = id ? pendingActions(message).find(a => a.tool_call_id === id) : null
  return {
    interruptId: (block && block.hitl_interrupt_id) || (ar && ar.interrupt_id) || null,
    // 编辑参数需要知道改的是同批里的哪一条（action_index 由后端下发）
    actionIndex: ar && ar.action_index != null ? ar.action_index : null,
    actionName: (ar && (ar.name || ar.tool_name)) || (block && block.tool_name) || ''
  }
}

/**
 * 构造 /agent/chat/resume 的 decisions。
 * 新载荷：只回答 interruptId（缺省为第一个中断）的那一批操作，每条带 interrupt_id / action_index。
 * 旧载荷：单个决策（后端把它作用于整批），与改造前一致。
 */
export function buildResumeDecisions (actionRequests, decision, opts = {}) {
  // 拒绝说明由后端按工具生成（DA-15）；调用方明确给了说明才带上
  const base = decision === 'approve'
    ? { type: 'approve' }
    : (opts.rejectMessage ? { type: 'reject', message: opts.rejectMessage } : { type: 'reject' })
  // 「改完参数继续执行」：只改被点的那一条，同批其余操作按批准处理
  const edited = decision === 'edit' ? (opts.editedAction || null) : null
  const editIndex = decision === 'edit' && opts.targetActionIndex != null
    ? Number(opts.targetActionIndex)
    : -1
  const actionDecision = (ar) => {
    const idx = ar && ar.action_index != null ? Number(ar.action_index) : 0
    if (edited && idx === editIndex) {
      return {
        type: 'edit',
        edited_action: {
          name: edited.name || (ar && (ar.name || ar.tool_name)) || '',
          args: edited.args || {},
        },
      }
    }
    return decision === 'edit' ? { type: 'approve' } : base
  }
  const actions = (actionRequests || []).filter(Boolean)
  if (!hasInterruptIds(actions)) {
    return { decisions: [edited ? { type: 'edit', edited_action: edited } : base], interruptId: null }
  }
  const interruptId = opts.interruptId || actions.find(ar => ar.interrupt_id).interrupt_id
  const batch = actions
    .filter(ar => ar.interrupt_id === interruptId)
    .sort((a, b) => (a.action_index || 0) - (b.action_index || 0))
  const decisions = batch.length
    ? batch.map(ar => ({ ...actionDecision(ar), interrupt_id: interruptId, action_index: ar.action_index || 0 }))
    // 中断已不在当前载荷里：仍按 ID 发送，由后端判定（过期则 400），绝不改投别的中断
    : [{ ...(edited ? { type: 'edit', edited_action: edited } : base), interrupt_id: interruptId }]
  return { decisions, interruptId }
}

/**
 * 统一构造审批 / 规划确认的 resume 载荷。
 * ``respond`` 只回答 ask_user；同批其他工具转成 reject，避免误执行。
 */
export function buildApprovalResumePlan (actionRequests, decision, target = {}) {
  if (decision === 'respond') {
    return buildInteractionResumeDecisions(actionRequests, {
      interruptId: target.interruptId,
      answers: target.response ? target.response.answers : {},
      note: target.response ? target.response.note : '',
      responses: target.responses || null,
    })
  }
  return buildResumeDecisions(actionRequests, decision, {
    interruptId: target.interruptId,
    targetActionIndex: target.actionIndex,
    editedAction: target.editedArgs
      ? { name: target.actionName || '', args: target.editedArgs }
      : null,
  })
}

/**
 * 用户作答后更新卡片：清掉所有「待审批」按钮（恢复流期间不允许再点），
 * 只给本次作答的中断记上「已批准 / 已拒绝」，其余仍为 pending，等后端再次下发审批。
 */
export function markApprovalDecision (blocks, decision, actionRequests, interruptId) {
  const status = decision === 'approve' || decision === 'respond'
    ? 'approved'
    : (decision === 'edit' ? 'edited' : 'rejected')
  const targetIds = interruptId
    ? new Set((actionRequests || [])
      .filter(ar => ar && ar.interrupt_id === interruptId && ar.tool_call_id)
      .map(ar => ar.tool_call_id))
    : null
  for (const blk of blocks || []) {
    if (!blk || !blk.pending_approval) continue
    blk.pending_approval = false
    if (!targetIds || targetIds.has(blk.tool_call_id) || blk.hitl_interrupt_id === interruptId) {
      blk.approval_status = status
    }
  }
}
