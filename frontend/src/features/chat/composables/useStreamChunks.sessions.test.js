import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { compileScript, parse } from '@vue/compiler-sfc'
import { createStreamChunkHandler as createMainHandler } from './useStreamChunks.js'
import { createStreamChunkHandler as createSidebarHandler } from '../services/chunkHandler.js'

// 执行宿主真实的会话切换函数，避免 fn() 桩掩盖后台用量被 restore 覆盖的问题。
// 只提取相关函数，不启动页面、网络请求或定时器。
const app = readFileSync(new URL('../../../App.vue', import.meta.url), 'utf8')
const { descriptor } = parse(app)
const { scriptSetupAst } = compileScript(descriptor, { id: 'session-isolation-test' })
const mainFunctions = ['ensureSessionState', 'runInSession'].map(name => {
  const node = scriptSetupAst.find(n => n.type === 'FunctionDeclaration' && n.id.name === name)
  return descriptor.scriptSetup.content.slice(node.start, node.end)
}).join('\n')

const sidebar = readFileSync(new URL('../mixins/chatSession.js', import.meta.url), 'utf8')
const sidebarDescriptor = parse(`<script>${sidebar}</script>`).descriptor
const { scriptAst } = compileScript(sidebarDescriptor, { id: 'sidebar-session-isolation-test' })
const methods = scriptAst.find(n => n.type === 'ExportDefaultDeclaration').declaration.properties
  .find(n => n.key.name === 'methods').value.properties
const sidebarMethods = new Function(`return ({${['ensureSessionState', 'runInSession'].map(name => {
  const node = methods.find(n => n.key.name === name)
  return sidebar.slice(node.start, node.end)
}).join(',')}})`)()

const ref = value => ({ value })
const message = id => ({ id, role: 'assistant', blocks: [], usage: { cached: true } })
const usageEvent = {
  type: 'token_usage', input_tokens: 100, output_tokens: 20, reasoning_tokens: 5,
  context_tokens: 120, elapsed_time: 9, step_count: 3,
}
const expectedUsage = {
  cached: true, input_tokens: 100, output_tokens: 20, reasoning_tokens: 5,
  context_tokens: 120, elapsed_time: 9, step_count: 3,
}

function harness(kind, { background, attachMode = false }) {
  const visibleId = background ? 's2' : 's1'
  const visibleMessages = [message(background ? 'b1' : 'a1')]
  const buffered = {
    messages: [message('a1')], sessionUsage: { context_tokens: 0 },
    sessionDuration: 20, iterationCount: 5, todos: [], assistantMsgId: 'a1',
    streamingAssistantId: 'a1', pendingApproval: null,
  }
  const raw = {
    messages: visibleMessages, sessionUsage: { context_tokens: 0 },
    sessionDuration: 20, iterationCount: 5, sessionStates: { s1: buffered },
    // 模拟切换中：current 已变，但 loaded 仍指向界面数据实际所属会话。
    currentSessionId: 's2', loadedSessionId: visibleId, currentTodos: [],
    streamingAssistantId: null, pendingApproval: null, attachedStreamingSessions: {},
    error: null,
  }
  const ctx = {
    assistantMsgId: 'a1', streamSessionId: 's1', attachMode,
    preStreamDuration: 20, preStreamIterationCount: 5,
  }
  if (kind === 'main') {
    const scope = Object.fromEntries(Object.entries(raw).map(([key, value]) => [key, ref(value)]))
    ctx.state = scope
    ctx.runInSession = new Function(...Object.keys(scope), `${mainFunctions}; return runInSession`)(...Object.values(scope))
    return {
      ctx, onChunk: createMainHandler(scope.messages, ctx).onChunk,
      visible: () => ({ messages: scope.messages.value, duration: scope.sessionDuration.value, iterations: scope.iterationCount.value }),
      target: () => background ? scope.sessionStates.value.s1 : {
        messages: scope.messages.value, sessionUsage: scope.sessionUsage.value,
        sessionDuration: scope.sessionDuration.value, iterationCount: scope.iterationCount.value,
      },
    }
  }
  const vm = Object.assign(raw, sidebarMethods)
  return {
    ctx, onChunk: createSidebarHandler(vm, ctx).onChunk,
    visible: () => ({ messages: vm.messages, duration: vm.sessionDuration, iterations: vm.iterationCount }),
    target: () => background ? vm.sessionStates.s1 : vm,
  }
}

for (const kind of ['main', 'sidebar']) {
  for (const background of [false, true]) {
    for (const attachMode of [false, true]) {
      test(`${kind}: 用量归属 loaded 会话，background=${background}, attachMode=${attachMode}`, () => {
        const h = harness(kind, { background, attachMode })
        const before = structuredClone(h.visible())
        h.onChunk(usageEvent)
        const target = h.target()
        assert.deepEqual(target.messages[0].usage, expectedUsage)
        assert.equal(target.sessionUsage.context_tokens, 120)
        assert.equal(target.sessionUsage.input_tokens, 100)
        assert.equal(target.sessionDuration, attachMode ? 9 : 29)
        assert.equal(target.iterationCount, attachMode ? 3 : 8)
        if (background) {
          assert.deepEqual(h.visible(), before, '后台事件不能修改当前界面')
          assert.equal(h.ctx.stateSwapped, false)
        }
        h.onChunk({ ...usageEvent, elapsed_time: undefined, step_count: 0 })
        assert.equal(h.target().messages[0].usage.elapsed_time, 9, '缺失字段不能清空已有累计值')
        h.onChunk({ type: 'token_usage', elapsed_time: 0 })
        assert.equal(h.target().messages[0].usage.elapsed_time, 0)
        assert.equal(h.target().messages[0].usage.step_count, 0)
      })
    }
  }
}

test('主页面后台状态在异常和异步收尾后都恢复', async () => {
  const h = harness('main', { background: true })
  const before = structuredClone(h.visible())
  assert.throws(() => h.ctx.runInSession('s1', h.ctx, () => { throw new Error('test') }), /test/)
  assert.equal(h.ctx.stateSwapped, false)
  assert.deepEqual(h.visible(), before)
  await assert.rejects(h.ctx.runInSession('s1', h.ctx, async () => {
    assert.equal(h.ctx.stateSwapped, true)
    throw new Error('async test')
  }), /async test/)
  assert.equal(h.ctx.stateSwapped, false)
  assert.deepEqual(h.visible(), before)
})
