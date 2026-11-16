import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { compileScript, parse } from 'vue/compiler-sfc'
import {
  archiveChanged, isSessionDeleted, isStaleSessionError, loadDailySessions,
  resetSessions, sessionDeleted, sessionGuard,
} from '../src/packages/easyAgent/shared/sessionManagement/state.js'
import { hasAssistantReply } from '../src/packages/easyAgent/features/chat/services/streamResume.js'

const root = '../src/packages/easyAgent/'
const page = 'EasyAgentPage.vue'
const mixin = 'features/chat/mixins/chatSession.js'

// 从真实源码提取方法，只替换网络与页面初始化依赖，不复制被测实现。
function methods(file, names, scope) {
  const source = readFileSync(new URL(root + file, import.meta.url), 'utf8')
  const sfc = parse({ source: file.endsWith('.vue') ? source : `<script>${source}</script>`, filename: file })
  const { scriptAst } = compileScript(sfc, { id: 'session-recovery' })
  const nodes = scriptAst.find(n => n.type === 'ExportDefaultDeclaration').declaration.properties
    .find(n => n.key.name === 'methods').value.properties
  const body = names.map(name => {
    const node = nodes.find(n => n.key.name === name)
    return sfc.script.content.slice(node.start, node.end)
  }).join(',')
  return new Function(...Object.keys(scope), `return ({${body}})`)(...Object.values(scope))
}
const deferred = () => {
  let resolve
  return { promise: new Promise(r => { resolve = r }), resolve }
}
const reply = content => ({ messages: [{ role: 'assistant', content }] })

function historyHarness(getChatHistory, streaming = false) {
  const vm = {
    currentSessionId: 's1', loadedSessionId: 's1', messages: [], currentTodos: [],
    sessionUsage: {}, sessionDuration: 0, iterationCount: 0, scrollTrigger: 0, sessionStates: {},
    isSessionStreaming: () => streaming, syncExpertChipFromMessages() {},
    $set: (target, key, value) => { target[key] = value },
    ensureSessionState(sid) { return this.sessionStates[sid] ||= { sessionUsage: {} } },
  }
  return Object.assign(vm, methods(mixin, ['awaitingAnswer', 'syncSessionFromServer', 'syncIfServerAnswered'], {
    isSessionDeleted, sessionGuard, getChatHistory, hasAssistantReply,
  }))
}

test('日常列表缺项不妨碍归档会话历史回填，也不切换前台会话', async () => {
  resetSessions()
  const vm = historyHarness(async () => reply('后台完成'))
  vm.currentSessionId = vm.loadedSessionId = 's2'
  await vm.syncSessionFromServer('s1')
  assert.equal(vm.sessionStates.s1.messages[0].content, '后台完成')
  assert.deepEqual(vm.messages, [])
  assert.equal(vm.currentSessionId, 's2')
})

for (const [label, change] of [['退出登录', resetSessions], ['删除会话', () => sessionDeleted('s1')]]) {
  for (const method of ['syncSessionFromServer', 'syncIfServerAnswered']) {
    test(`${method}: 请求期间${label}，不回填旧响应`, async () => {
      resetSessions()
      const request = deferred()
      const vm = historyHarness(() => request.promise)
      const pending = vm[method]('s1')
      change()
      request.resolve(reply('过期'))
      await pending
      assert.deepEqual(vm.messages, [])
    })
  }
}

for (const method of ['syncSessionFromServer', 'syncIfServerAnswered']) {
  test(`${method}: 较新历史回填优先，旧响应不能覆盖`, async () => {
    resetSessions()
    const request = deferred()
    const vm = historyHarness(() => request.promise)
    const old = vm[method]('s1')
    await vm.syncSessionFromServer('s1', reply('新'))
    request.resolve(reply('旧'))
    await old
    assert.equal(vm.messages[0].content, '新')
  })
}

test('挂载后空白的会话可补回答复，预加载历史不重复请求', async () => {
  resetSessions()
  let calls = 0
  const vm = historyHarness(async () => { calls++; return reply('已落库') }, true)
  vm.messages = [{ role: 'user', content: '本轮问题' }]
  assert.equal(await vm.syncIfServerAnswered('s1'), true)
  assert.equal(vm.messages[0].content, '已落库')
  assert.equal(calls, 1)
})

async function resumeHarness(current, active, change) {
  resetSessions()
  const attached = [], marked = []
  const vm = { currentSessionId: current, loadedSessionId: current, sessions: [],
    loadSessions: async () => {}, syncIfServerAnswered: async () => {}, saveStreamState() {},
    markStreaming: sid => marked.push(sid),
    attachToStreamingSession: (sid, options) => attached.push({ sid, ...options }),
  }
  Object.assign(vm, methods(page, ['tryAttachLiveStream'], {
    sessionGuard, isSessionDeleted, STREAM_STATE_KEY: 'test',
    sessionStorage: { getItem: () => null }, readStreamFlight: () => null,
    clearStreamFlight() {}, getStreamStatus: async () => {}, getChatHistory: async () => {},
    resolveResumeTargets: async () => {
      if (change) change(vm)
      return { ids: active, active, waited: false, lastActive: 's1' }
    },
  }))
  await vm.tryAttachLiveStream()
  return { vm, attached, marked }
}

test('刷新新会话首页时，旧活跃任务只在后台挂载', async () => {
  const h = await resumeHarness(null, ['s1'])
  assert.equal(h.vm.currentSessionId, null)
  assert.deepEqual(h.attached, [{ sid: 's1', switchTo: false, displayed: false }])
})

test('恢复等待期间切换会话，以用户当前选择为准', async () => {
  const h = await resumeHarness('s1', ['s1', 's2'], vm => { vm.currentSessionId = 's2' })
  assert.equal(h.attached.find(a => a.displayed).sid, 's2')
  assert.equal(h.attached.every(a => !a.switchTo), true)
})

for (const [label, change] of [['退出登录', resetSessions], ['删除任务', () => sessionDeleted('s1')]]) {
  test(`恢复等待期间${label}，不再恢复旧任务状态`, async () => {
    const h = await resumeHarness('s1', ['s1'], change)
    assert.deepEqual(h.attached, [])
    assert.deepEqual(h.marked, [])
  })
}

for (const file of [page, mixin]) {
  test(`${file}: 只在有效列表应用后恢复审批徽标并清除加载错误`, async () => {
    resetSessions()
    const request = deferred(), hydrated = []
    let calls = 0
    const vm = { sessions: [], userProfile: { username: 'test' }, error: '加载会话列表失败',
      hydratePendingApprovalFromServer() { hydrated.push(this.sessions[0].session_id) },
    }
    Object.assign(vm, methods(file, ['loadSessions'], {
      loadDailySessions, isStaleSessionError, chatStore: { username: 'test' },
      listSessions: (_username, params) => {
        assert.equal(params.archived, false)
        return ++calls === 1 ? request.promise : Promise.resolve([{ session_id: 's2', pending_approval: true }])
      },
    }))
    const old = vm.loadSessions()
    archiveChanged('s1', true)
    await vm.loadSessions()
    request.resolve([{ session_id: 's1', pending_approval: true }])
    await old
    assert.deepEqual(hydrated, ['s2'])
    assert.equal(vm.sessions[0].session_id, 's2')
    assert.equal(vm.error, null)
  })
}
