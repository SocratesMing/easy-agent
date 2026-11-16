import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { compileScript, parse } from '@vue/compiler-sfc'
import { hasAssistantReply } from '../services/streamResume.js'
import {
  archiveChanged, isSessionDeleted, isStaleSessionError, loadDailySessions,
  resetSessions, sessionDeleted, sessionGuard,
} from '../../../shared/sessionManagement/state.js'

const { descriptor } = parse(readFileSync(new URL('../../../App.vue', import.meta.url), 'utf8'))
const { scriptSetupAst } = compileScript(descriptor, { id: 'session-recovery' })
function functions(names, scope) {
  const source = names.map(name => {
    const node = scriptSetupAst.find(n => n.type === 'FunctionDeclaration' && n.id.name === name)
    return descriptor.scriptSetup.content.slice(node.start, node.end)
  }).join('\n')
  return new Function(...Object.keys(scope), `${source}; return {${names.join(',')}}`)(...Object.values(scope))
}
const ref = value => ({ value })
const deferred = () => {
  let resolve
  return { promise: new Promise(r => { resolve = r }), resolve }
}
const reply = content => ({ messages: [{ role: 'assistant', content }] })

function historyHarness(getChatHistory, streaming = false) {
  const scope = {
    isSessionDeleted, sessionGuard, getChatHistory, historyVersions: {}, hasAssistantReply,
    isSessionStreaming: () => streaming,
    currentSessionId: ref('s1'), loadedSessionId: ref('s1'), messages: ref([]), currentTodos: ref([]),
    syncExpertChipFromMessages() {}, sessionUsage: ref({}), sessionDuration: ref(0),
    iterationCount: ref(0), scrollTrigger: ref(0), sessionStates: ref({}),
    ensureSessionState(sid) { return scope.sessionStates.value[sid] ||= { sessionUsage: {} } },
  }
  return {
    scope,
    ...functions(['awaitingAnswer', 'syncSessionFromServer', 'syncIfServerAnswered'], scope),
  }
}

test('日常列表缺项不妨碍历史回填，也不把后台会话切回前台', async () => {
  resetSessions()
  const h = historyHarness(async () => reply('后台完成'))
  h.scope.currentSessionId.value = 's2'
  h.scope.loadedSessionId.value = 's2'
  await h.syncSessionFromServer('s1')
  assert.equal(h.scope.sessionStates.value.s1.messages[0].content, '后台完成')
  assert.deepEqual(h.scope.messages.value, [])
  assert.equal(h.scope.currentSessionId.value, 's2')
})

for (const change of [resetSessions, () => sessionDeleted('s1')]) {
  test(`历史请求期间${change === resetSessions ? '退出登录' : '删除会话'}，不回填旧响应`, async () => {
    resetSessions()
    const request = deferred()
    const h = historyHarness(() => request.promise)
    const pending = h.syncSessionFromServer('s1')
    change()
    request.resolve(reply('过期'))
    await pending
    assert.deepEqual(h.scope.messages.value, [])
  })
}

test('较新历史请求优先，旧响应不能覆盖', async () => {
  resetSessions()
  const request = deferred()
  let calls = 0
  const h = historyHarness(() => ++calls === 1 ? request.promise : Promise.resolve(reply('新')))
  const old = h.syncSessionFromServer('s1')
  await h.syncSessionFromServer('s1')
  request.resolve(reply('旧'))
  await old
  assert.equal(h.scope.messages.value[0].content, '新')
})

test('挂载后空白的会话可从历史补回答复，预加载结果不重复请求', async () => {
  resetSessions()
  let calls = 0
  const h = historyHarness(async () => { calls++; return reply('已落库') }, true)
  h.scope.messages.value = [{ role: 'user', content: '问题' }]
  assert.equal(await h.syncIfServerAnswered('s1'), true)
  assert.equal(h.scope.messages.value[0].content, '已落库')
  assert.equal(calls, 1)
})

test('历史兜底请求期间发生用户重置，不补回旧答复', async () => {
  resetSessions()
  const request = deferred()
  const h = historyHarness(() => request.promise)
  const pending = h.syncIfServerAnswered('s1')
  resetSessions()
  request.resolve(reply('过期'))
  assert.equal(await pending, false)
  assert.deepEqual(h.scope.messages.value, [])
})

test('较新的历史回填使先前的兜底响应失效', async () => {
  resetSessions()
  const request = deferred()
  const h = historyHarness(() => request.promise)
  const old = h.syncIfServerAnswered('s1')
  await h.syncSessionFromServer('s1', reply('新'))
  request.resolve(reply('旧'))
  assert.equal(await old, false)
  assert.equal(h.scope.messages.value[0].content, '新')
})

async function resumeHarness(current, active, change) {
  resetSessions()
  const currentSessionId = ref(current), attached = [], marked = []
  const scope = {
    sessionGuard, isSessionDeleted, currentSessionId, loadedSessionId: ref(current), sessions: ref([]),
    STREAM_STATE_KEY: 'test', sessionStorage: { getItem: () => null }, readStreamFlight: () => null,
    loadSessions: async () => {}, getStreamStatus: async () => {}, getChatHistory: async () => {},
    resolveResumeTargets: async () => {
      if (change) change(currentSessionId)
      return { ids: active, active, waited: false, lastActive: 's1' }
    },
    syncIfServerAnswered: async () => {}, clearStreamFlight() {}, saveStreamState() {},
    markStreaming: sid => marked.push(sid),
    attachToStreamingSession: (sid, options) => attached.push({ sid, ...options }),
  }
  await functions(['tryAttachLiveStream'], scope).tryAttachLiveStream()
  return { currentSessionId, attached, marked }
}

test('新会话首页保持不变，旧的活跃任务仅后台挂载', async () => {
  const h = await resumeHarness(null, ['s1'])
  assert.equal(h.currentSessionId.value, null)
  assert.deepEqual(h.attached, [{ sid: 's1', switchTo: false, displayed: false }])
})

test('恢复等待期间切换会话后，以用户当前选择为准', async () => {
  const h = await resumeHarness('s1', ['s1', 's2'], current => { current.value = 's2' })
  assert.equal(h.attached.find(a => a.displayed).sid, 's2')
  assert.equal(h.attached.every(a => !a.switchTo), true)
})

test('恢复等待期间退出登录，不恢复旧任务状态', async () => {
  const h = await resumeHarness('s1', ['s1'], resetSessions)
  assert.deepEqual(h.attached, [])
  assert.deepEqual(h.marked, [])
})

test('恢复等待期间删除任务，不再挂载', async () => {
  const h = await resumeHarness('s1', ['s1'], () => sessionDeleted('s1'))
  assert.deepEqual(h.attached, [])
  assert.deepEqual(h.marked, [])
})

test('列表成功更新后恢复审批徽标，归档使旧列表及其徽标水合失效', async () => {
  resetSessions()
  const request = deferred(), hydrated = []
  let calls = 0
  const scope = {
    loadDailySessions, isStaleSessionError, userProfile: ref({ username: 'test' }),
    sessions: ref([]), error: ref(null),
    listSessions: (_username, params) => {
      assert.equal(params.archived, false)
      return ++calls === 1 ? request.promise : Promise.resolve([{ session_id: 's2', pending_approval: true }])
    },
    hydratePendingApprovalFromServer() { hydrated.push(scope.sessions.value[0].session_id) },
  }
  const { loadSessions } = functions(['loadSessions'], scope)
  const old = loadSessions()
  archiveChanged('s1', true)
  await loadSessions()
  request.resolve([{ session_id: 's1', pending_approval: true }])
  await old
  assert.deepEqual(hydrated, ['s2'])
  assert.equal(scope.sessions.value[0].session_id, 's2')
})
