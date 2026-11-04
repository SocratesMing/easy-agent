/**
 * 回归测试：会话 A 生成中新建/切到会话 B，A 的后台事件不能丢。
 *
 * 现象：A 生成中新建会话 B 并发送，切回 A 只剩切走时的快照、没有实时思考过程；
 * A 再发送提示「本会话正在生成回复」（后端其实还在跑）。
 * 根因：saveCurrentSessionState 存的缓存不含 assistantMsgId，runInSession 却用缓存值
 * 覆盖 ctx.assistantMsgId（变成 undefined），A 的后续事件找不到占位消息被全部丢弃。
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import chatSessionMixin from './chatSession.js'

// 用 mixin 自身的 data / methods / computed 拼一个最小 vm（不挂载 Vue）
function makeVm() {
  const store = { streamingSessions: [] }
  const vm = {
    ...chatSessionMixin.data.call({}),
    $set(target, key, value) { target[key] = value },
  }
  Object.defineProperty(vm, 'streamingSessions', { get: () => store.streamingSessions, set: v => { store.streamingSessions = v } })
  Object.defineProperty(vm, 'isStreaming', { get: () => vm.isSessionStreaming(vm.currentSessionId) })
  for (const [name, fn] of Object.entries(chatSessionMixin.methods)) vm[name] = fn.bind(vm)
  return vm
}

function appendContent(vm, ctx, text) {
  vm.runInSession(ctx.streamSessionId, ctx, () => {
    const msg = vm.messages.find(m => m.id === ctx.assistantMsgId)
    if (msg) msg.content += text
  })
}

test('A 生成中新建会话 B：A 的后台事件写入 A 的缓存，切回后可见且仍可停止', () => {
  const vm = makeVm()
  // 会话 A 正在生成
  vm.currentSessionId = 'A'
  vm.loadedSessionId = 'A'
  vm.messages = [{ id: 'u1', role: 'user', content: '任务A' }, { id: 'pA', role: 'assistant', content: '', loading: true, blocks: [] }]
  vm.streamingAssistantId = 'pA'
  vm.markStreaming('A')
  const ctxA = { streamSessionId: 'A', assistantMsgId: 'pA' }
  appendContent(vm, ctxA, '思考1')

  // 新建会话 B（同 handleCreateSession）并开始生成
  vm.saveCurrentSessionState()
  vm.currentSessionId = 'B'
  vm.loadedSessionId = 'B'
  vm.messages = [{ id: 'pB', role: 'assistant', content: '', loading: true, blocks: [] }]
  vm.streamingAssistantId = 'pB'
  vm.markStreaming('B')

  // A 继续在后台收到事件
  appendContent(vm, ctxA, '思考2')
  appendContent(vm, ctxA, '思考3')
  assert.equal(ctxA.assistantMsgId, 'pA', '后台路由不能改掉流自己的写入目标')
  assert.equal(vm.messages[0].content, '', 'A 的事件不能写进当前展示的 B')
  assert.equal(vm.streamingAssistantId, 'pB')

  // 切回 A（同 handleSelectSession）
  vm.saveCurrentSessionState()
  vm.currentSessionId = 'A'
  assert.ok(vm.restoreSessionState('A'))
  assert.equal(vm.messages.find(m => m.id === 'pA').content, '思考1思考2思考3')
  assert.equal(vm.streamingAssistantId, 'pA', '切回后停止按钮要能定位 A 的占位消息')
  assert.equal(vm.isStreaming, true)

  // 展示中继续收事件
  appendContent(vm, ctxA, '完成')
  assert.equal(vm.messages.find(m => m.id === 'pA').content, '思考1思考2思考3完成')
})

test('同一会话第二轮在后台时不会写进上一轮回答', () => {
  const vm = makeVm()
  vm.currentSessionId = 'A'
  vm.loadedSessionId = 'A'
  vm.messages = [{ id: 'old', role: 'assistant', content: '第一轮', blocks: [] }]
  vm.sessionStates.A = { messages: [{ id: 'old', role: 'assistant', content: '第一轮' }, { id: 'new', role: 'assistant', content: '' }], assistantMsgId: 'old', sessionUsage: {}, todos: [] }
  vm.loadedSessionId = 'B'
  vm.messages = []
  const ctx = { streamSessionId: 'A', assistantMsgId: 'new' }
  appendContent(vm, ctx, '第二轮')
  const msgs = vm.sessionStates.A.messages
  assert.equal(msgs[0].content, '第一轮')
  assert.equal(msgs[1].content, '第二轮')
})

test('已结束的会话恢复时不带残留的 streamingAssistantId', () => {
  const vm = makeVm()
  vm.sessionStates.A = { messages: [], sessionUsage: {}, todos: [], streamingAssistantId: 'pA' }
  vm.streamingAssistantId = 'pB'
  vm.restoreSessionState('A')
  assert.equal(vm.streamingAssistantId, null)
})

// 模拟一次 approval_required 事件：与 chunk handler 一样经 runInSession 路由到流所属会话
function approvalArrives(vm, sid, threadId) {
  vm.runInSession(sid, null, () => { vm.pendingApproval = { threadId, assistantMsgId: `p${sid}` } })
}

// 模拟左侧栏切换：保存当前 → 恢复目标 → loadedSessionId watcher 触发的 reconstructPendingApproval
function switchTo(vm, sid) {
  vm.saveCurrentSessionState()
  vm.currentSessionId = sid
  assert.ok(vm.restoreSessionState(sid))
  vm.reconstructPendingApproval()
}

function twoSessionsWaitingForApproval() {
  const vm = makeVm()
  vm.sessionStates.A = { messages: [{ id: 'pA', role: 'assistant', blocks: [] }], sessionUsage: {}, todos: [] }
  vm.sessionStates.B = { messages: [{ id: 'pB', role: 'assistant', blocks: [] }], sessionUsage: {}, todos: [] }
  vm.markStreaming('A')
  vm.markStreaming('B')
  vm.currentSessionId = 'A'
  vm.restoreSessionState('A')
  return vm
}

test('两个会话同时停在审批：各自持有自己的审批，后到的不会覆盖先到的', () => {
  const vm = twoSessionsWaitingForApproval()
  approvalArrives(vm, 'A', 'A-1') // 展示中
  approvalArrives(vm, 'B', 'B-1') // 后台
  assert.equal(vm.pendingApproval.threadId, 'A-1')
  assert.equal(vm.pendingApprovalOf('B').threadId, 'B-1')
  switchTo(vm, 'B')
  assert.equal(vm.pendingApproval.threadId, 'B-1')
  switchTo(vm, 'A')
  assert.equal(vm.pendingApproval.threadId, 'A-1')
})

test('批准 A 后切到 B：B 的批准按钮仍拿得到 B 的审批（此前为 null，点击无反应）', () => {
  const vm = twoSessionsWaitingForApproval()
  approvalArrives(vm, 'A', 'A-1')
  approvalArrives(vm, 'B', 'B-1')
  // 批准 A：恢复流结束只清 A 自己的
  vm.clearPendingApprovalOf('A')
  vm.unmarkStreaming('A')
  switchTo(vm, 'B')
  assert.deepEqual(vm.pendingApproval, { threadId: 'B-1', assistantMsgId: 'pB' })
})

test('A 的恢复流在后台结束时，不清掉当前展示的 B 的审批', () => {
  const vm = twoSessionsWaitingForApproval()
  approvalArrives(vm, 'A', 'A-1')
  approvalArrives(vm, 'B', 'B-1')
  switchTo(vm, 'B') // 用户批准 A 后立刻切到 B，A 的恢复流还在跑
  vm.clearPendingApprovalOf('A')
  assert.equal(vm.pendingApproval.threadId, 'B-1')
  assert.equal(vm.pendingApprovalOf('A'), null)
})
