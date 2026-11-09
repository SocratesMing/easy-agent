import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { stateFor } from './state.js'

// 注入传输层，直接验证两版前端共用的完整请求/恢复生命周期。
const source = (await readFile(new URL('./mixin.js', import.meta.url), 'utf8'))
  .replace("import { compactContext, getContextStatus } from '../../api/chat.js'",
    'const { compactContext, getContextStatus } = globalThis.__compactionTestApi')
  .replace("from '../sessionManagement/state.js'", `from '${new URL('../sessionManagement/state.js', import.meta.url).href}'`)
  .replace("from './state.js'", `from '${new URL('./state.js', import.meta.url).href}'`)

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
