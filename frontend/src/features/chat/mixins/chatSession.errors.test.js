import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { effectScope, nextTick, reactive, watch } from 'vue'
import { createAutoDismiss } from '../../../utils/autoDismiss.js'
import { chatBus, chatStore } from '../store/chatStore.js'
import { connectSessionView } from '../../../shared/sessionManagement/viewLifecycle.js'
import { derivePendingApproval } from '../../../utils/hitlApproval.js'
import { setReactive } from '../../../utils/reactiveSet.js'

// 沿用组件测试的源码加载方式，隔离依赖 Vite import.meta.env 的网络模块。
const source = readFileSync(new URL('./chatSession.js', import.meta.url), 'utf8')
const chatSession = new Function('createAutoDismiss', 'connectSessionView', 'chatBus', 'chatStore', 'derivePendingApproval', 'setReactive',
  source.replace(/^import .*$/gm, '').replace('export default', 'return'),
)(createAutoDismiss, connectSessionView, chatBus, chatStore, derivePendingApproval, setReactive)

test('错误提示计时与切换会话恢复审批的监听同时生效', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const scope = effectScope()
  const message = {
    role: 'assistant', error: '模型连接中断',
    pending_approval: { thread_id: 's1-a1', action_requests: [{ tool_call_id: 'call-1' }] },
    blocks: [{ type: 'tool_call', tool_call_id: 'call-1', approval_status: 'pending' }],
  }
  const vm = reactive({ ...chatSession.data(), messages: [message] })
  vm.reconstructPendingApproval = chatSession.methods.reconstructPendingApproval.bind(vm)
  vm.recomputePendingApprovalFlag = () => {}
  vm.onPeerStreamStarted = () => {}
  vm.onPeerSessionRemoved = () => {}
  chatSession.created.call(vm)
  t.after(() => {
    chatSession.beforeUnmount.call(vm)
    scope.stop()
  })
  scope.run(() => {
    for (const [key, handler] of Object.entries(chatSession.watch)) {
      watch(() => vm[key], handler.bind(vm))
    }
  })

  vm.currentSessionId = 's1'
  vm.loadedSessionId = 's1'
  vm.error = '模型连接中断'
  await nextTick()
  assert.deepEqual(vm.pendingApproval, { threadId: 's1-a1', assistantMsgId: 'a1' })
  assert.equal(vm.messages[0].blocks[0].pending_approval, true)
  t.mock.timers.tick(4999)
  assert.equal(vm.error, '模型连接中断')
  t.mock.timers.tick(1)
  await nextTick()
  assert.equal(vm.error, null, '全局提示在 5 秒后清空')
  assert.equal(vm.messages[0].error, '模型连接中断', '消息内错误常驻')

  vm.messages = [{ role: 'assistant', content: '已完成', blocks: [] }]
  vm.currentSessionId = 's2'
  vm.loadedSessionId = 's2'
  await nextTick()
  assert.equal(vm.pendingApproval, null, '切换到已完成的会话不残留旧审批')
})
