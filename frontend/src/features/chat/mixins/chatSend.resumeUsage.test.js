import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { compileScript, parse } from '@vue/compiler-sfc'
import { createStreamChunkHandler } from '../services/chunkHandler.js'
import { resumeUsageBase } from '../services/streamUsage.js'
import { buildResumeDecisions, markApprovalDecision } from '../../../utils/hitlApproval.js'
import { setReactive } from '../../../utils/reactiveSet.js'

// 执行真实审批入口和事件处理器，只替换网络请求与收尾回填。
const source = readFileSync(new URL('./chatSend.js', import.meta.url), 'utf8')
const { descriptor } = parse(`<script>${source}</script>`)
const { scriptAst } = compileScript(descriptor, { id: 'sidebar-resume-usage' })
const methods = scriptAst.find(n => n.type === 'ExportDefaultDeclaration').declaration.properties
  .find(n => n.key.name === 'methods').value.properties
const method = methods.find(n => n.key.name === 'handleToolApproval')
const usage = (step_count, elapsed_time) => ({ step_count, elapsed_time, input_tokens: 100, output_tokens: 20 })

for (const fromHistory of [false, true]) {
  test(`侧边栏真实审批入口不重复累计耗时和步数，fromHistory=${fromHistory}`, async () => {
    const vm = {
      messages: [{ id: 'a1', role: 'assistant', blocks: [] }],
      currentSessionId: 's1', loadedSessionId: 's1', sessionStates: {},
      sessionUsage: {}, sessionDuration: 20, iterationCount: 5,
      runInSession: (_sid, _ctx, fn) => fn(),
      createStreamChunkHandler(ctx) { return createStreamChunkHandler(this, ctx) },
      recomputePendingApprovalFlag() {}, markStreaming() {}, unmarkStreaming() {},
      saveCurrentSessionState() {}, loadSessions: async () => {}, syncSessionFromServer: async () => {},
    }
    if (fromHistory) {
      vm.sessionDuration = 29.4
      vm.iterationCount = 8
      vm.messages[0].usage = usage(3, 9.4)
    } else {
      const { onChunk } = vm.createStreamChunkHandler({
        streamSessionId: 's1', assistantMsgId: 'a1', preStreamDuration: 20, preStreamIterationCount: 5,
      })
      onChunk({ type: 'token_usage', ...usage(3, 9.4) })
    }
    vm.pendingApproval = { threadId: 's1-a1', assistantMsgId: 'a1' }
    vm.messages[0].pending_approval = { thread_id: 's1-a1', action_requests: [] }
    const resumeStream = async (_sid, _thread, _decisions, onChunk) => {
      onChunk({ type: 'token_usage', ...usage(4, 12.4) })
      onChunk({ type: 'token_usage', ...usage(5, 16.4) })
      onChunk({ type: 'done', usage: usage(5, 17.4), elapsed_time: 17.4 })
    }
    const handleToolApproval = new Function(
      'resumeUsageBase', 'buildResumeDecisions', 'markApprovalDecision', 'setReactive', 'resumeStream',
      `return ({${source.slice(method.start, method.end)}}).handleToolApproval`,
    )(resumeUsageBase, buildResumeDecisions, markApprovalDecision, setReactive, resumeStream)
    await handleToolApproval.call(vm, 'approve')
    assert.equal(vm.sessionDuration, 37.4)
    assert.equal(vm.iterationCount, 10)
    assert.equal(vm.messages[0].usage.elapsed_time, 17.4)
    assert.equal(vm.messages[0].usage.step_count, 5)
    assert.equal(vm.messages[0].error, undefined)
  })
}
