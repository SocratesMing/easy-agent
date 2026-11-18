import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { stateFor } from './state.js'
import { readContextResponse } from './response.js'

// 注入传输层，直接验证两版前端共用的完整请求/恢复生命周期。
const source = (await readFile(new URL('./mixin.js', import.meta.url), 'utf8'))
  .replace("import { compactContext, getContextStatus } from '../../api/chat.js'",
    'const { compactContext, getContextStatus } = globalThis.__compactionTestApi')
  .replace("from '../sessionManagement/state.js'", `from '${new URL('../sessionManagement/state.js', import.meta.url).href}'`)
  .replace("from './state.js'", `from '${new URL('./state.js', import.meta.url).href}'`)
  .replace("from '../utils/apiError.js'", `from '${new URL('../utils/apiError.js', import.meta.url).href}'`)

async function setup(sid, compact, status) {
  const storage = new Map()
  globalThis.sessionStorage = {
    getItem: key => storage.get(key) || null,
    setItem: (key, value) => storage.set(key, value),
    removeItem: key => storage.delete(key),
  }
  globalThis.__compactionTestApi = { compactContext: compact, getContextStatus: status }
  const { default: mixin } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}#${sid}`)
  const vm = { currentSessionId: sid, messages: [{ role: 'user', turn_id: 'turn' }],
    selectedModel: 'deepseek', compactReason: '' }
  for (const [name, fn] of Object.entries(mixin.methods)) vm[name] = fn.bind(vm)
  return { vm, storage, state: stateFor(sid) }
}
const idle = () => ({ supported: true, busy: false, reason: '', compactions: [] })

for (const transport of ['fetch', 'axios']) {
  test(`${transport} rejection keeps backend reason, clears pending and permits a new request`, async () => {
    const ids = []
    const { vm, storage, state } = await setup(`reject-${transport}`, async (sid, model, id) => {
      ids.push(id)
      const error = new Error('会话正在等待审批，请先处理审批')
      if (transport === 'fetch') error.status = 409
      else error.response = { status: 409 }
      throw error
    }, idle)
    await vm.runCompact()
    assert.equal(state.records[0].status, 'failed')
    assert.equal(state.records[0].message, '会话正在等待审批，请先处理审批')
    assert.equal(storage.size, 0)
    assert.equal(state.busy, false)
    await vm.runCompact()
    assert.equal(ids.length, 2)
    assert.notEqual(ids[0], ids[1])
  })
}

test('disconnect with failed query stays pending, then recovers a committed result', async () => {
  let queryingFails = true
  let committed
  const { vm, storage, state } = await setup('disconnected', async (sid, model, id) => {
    committed = { id, status: 'compacted', after_tokens: 120 }
    throw new Error('网络中断')
  }, () => {
    if (queryingFails) throw new Error('offline')
    return { ...idle(), compactions: [committed] }
  })
  await vm.runCompact()
  assert.equal(state.records[0].status, 'unknown')
  assert.equal(state.busy, true)
  assert.equal(storage.size, 1)
  queryingFails = false
  await vm.refreshCompaction(vm.currentSessionId)
  assert.equal(state.records[0].status, 'compacted')
  assert.equal(state.records[0].after_tokens, 120)
  assert.equal(storage.size, 0)
  assert.equal(state.busy, false)
})

test('authoritative idle without a result clears pending and stops repeated polling', async () => {
  let sid
  const { vm, storage, state } = await setup('not-committed', async () => {
    throw new Error('connection lost')
  }, idle)
  sid = vm.currentSessionId
  await vm.runCompact()
  assert.equal(state.records[0].status, 'failed')
  assert.match(state.records[0].message, /未提交/)
  assert.equal(storage.has(`compact:${sid}`), false)
  assert.equal(state.busy, false)
  assert.equal(state.queryFailed, false)
})

test('归档退出展示后，压缩断线保留请求 ID，迟到结果仅写原会话', async () => {
  const { archiveChanged } = await import('../sessionManagement/state.js')
  let finish, calls = 0
  const { vm, storage, state } = await setup('archive-compact', async (sid, model, id) => {
    calls++
    await new Promise(resolve => { finish = resolve })
    throw new Error('disconnect')
  }, async () => { throw new Error('offline') })
  const operation = vm.runCompact()
  const request = storage.get('compact:archive-compact')
  archiveChanged('archive-compact', true)
  vm.currentSessionId = null; vm.messages = []; vm.sessionUsage = { input_tokens: 0 }
  finish(); await operation
  assert.equal(storage.get('compact:archive-compact'), request)
  assert.equal(calls, 1)
  assert.equal(state.busy, true)
  assert.equal(vm.sessionUsage.input_tokens, 0)
  assert.deepEqual(vm.messages, [])
})

for (const httpStatus of [200, 403, 502, 503, 504]) {
  test(`HTML ${httpStatus} followed by malformed queries retains the original request and recovers once`, async () => {
    const sid = `html-${httpStatus}`
    let id, calls = 0, resultReady = false
    const html = '<!DOCTYPE html><html><title>系统更新</title><body>请稍后再试</body></html>'
    const record = () => ({ id, status: 'compacted', message: '上下文已压缩', anchor_message_count: 1,
      after_tokens: 150, context_length: 1000 })
    const { vm, state, storage } = await setup(sid, async (session, model, requestId) => {
      calls++; id = requestId
      return readContextResponse(html, httpStatus, session, requestId)
    }, async session => readContextResponse(resultReady
      ? { ...idle(), session_id: session, compactions: [record()] } : html, 200, session))
    await vm.runCompact()
    assert.equal(state.records[0].status, 'unknown')
    assert.equal(vm.compactionNoticeText(state.records[0]), 'compact · 正在确认压缩结果...')
    assert.doesNotMatch(state.records[0].message, /<!DOCTYPE|<html|系统更新/)
    assert.equal(state.busy, true)
    assert.equal(state.queryFailed, true)
    assert.equal(JSON.parse(storage.get(`compact:${sid}`)).id, id)
    // 刷新后仅凭保存的请求 ID 恢复；无效 GET 不能清除请求或误报未提交。
    state.records = []; state.busy = false
    await vm.refreshCompaction(sid)
    assert.equal(state.records[0].status, 'unknown')
    assert.equal(state.busy, true)
    assert.ok(storage.has(`compact:${sid}`))
    resultReady = true
    await vm.refreshCompaction(sid)
    assert.equal(vm.compactionNoticeText(state.records[0]), 'compact · 已压缩')
    assert.equal(state.records.length, 1)
    assert.equal(state.records[0].id, id)
    assert.equal(state.busy, false)
    assert.equal(storage.size, 0)
    assert.equal(calls, 1)
  })
}
