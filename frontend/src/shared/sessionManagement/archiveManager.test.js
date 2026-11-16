// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/sessionManagement/archiveManager.test.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { resetSessions, sessionMeta } from './state.js'
const autoDismissURL = new URL(import.meta.url.includes('/frontend-shared/') ? '../../frontend/src/utils/autoDismiss.js' : '../../utils/autoDismiss.js', import.meta.url)
const source = (await readFile(new URL('./archiveManager.js', import.meta.url), 'utf8'))
  .replace(/import .* from '..\/..\/api\/chat.js'/, 'const { listSessions, countSessions, setSessionArchived, deleteSession } = globalThis.__archiveApi')
  .replace(/import .* from '..\/..\/features\/chat\/store\/chatStore.js'/, 'const { chatStore, chatBus } = globalThis.__archiveStore')
  .replace("from './state.js'", `from '${new URL('./state.js', import.meta.url).href}'`)
  .replace("from '../../utils/autoDismiss.js'", `from '${autoDismissURL.href}'`)
let moduleId = 0
async function setup(overrides = {}) {
  resetSessions()
  const writes = [], events = [], store = { sessions: [], pendingApprovalSessions: ['A'], streamingSessions: ['A'] }
  globalThis.__archiveApi = {
    listSessions: async () => [], countSessions: async () => ({ total_sessions: 0 }),
    setSessionArchived: async (id, archived) => { writes.push([id, archived]) },
    deleteSession: async id => { writes.push([id, 'delete']) }, ...overrides,
  }
  globalThis.__archiveStore = { chatStore: store, chatBus: { emit: (event, payload) => events.push([event, payload]) } }
  const { default: mixin } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}#${++moduleId}`)
  const vm = { ...mixin.data(), open: true, $emit() {}, $nextTick: async () => {}, $refs: { confirm: { show: async () => true } } }
  for (const [name, fn] of Object.entries(mixin.methods)) vm[name] = fn.bind(vm)
  Object.defineProperty(vm, 'pages', { get: () => mixin.computed.pages.call(vm) })
  mixin.created.call(vm)
  return { vm, writes, events, store }
}
const deferred = () => { let resolve; return { promise: new Promise(r => { resolve = r }), resolve } }
test('取消删除不发请求，确认绑定的 ID 不随行对象变化', async () => {
  const { vm, writes } = await setup()
  vm.confirmAction = async () => false
  await vm.operate({ session_id: 'A' }, true)
  assert.equal(writes.length, 0)
  const row = { session_id: 'A' }, answer = deferred()
  vm.confirmAction = () => answer.promise
  const operation = vm.operate(row, true)
  row.session_id = 'B'; answer.resolve(true); await operation
  assert.deepEqual(writes, [['A', 'delete']])
  vm.disposeArchive()
})
test('归档保留流式与审批，重复点击仅写一次，刷新失败仍反馈写成功', async () => {
  const write = deferred()
  const { vm, store, writes } = await setup({ setSessionArchived: async id => { writes.push(id); await write.promise }, listSessions: async () => { throw new Error('离线') } })
  const operation = vm.archive({ session_id: 'A', title: '任务' })
  await Promise.resolve(); await Promise.resolve()
  await vm.archive({ session_id: 'A' })
  write.resolve(); await operation
  assert.deepEqual(writes, ['A'])
  assert.equal(sessionMeta('A').archived, true)
  assert.deepEqual(store.streamingSessions, ['A'])
  assert.deepEqual(store.pendingApprovalSessions, ['A'])
  assert.match(vm.notice, /已归档.*刷新失败/)
  vm.disposeArchive()
})
test('搜索较旧的响应、关闭弹层与用户切换后均不回填', async () => {
  const old = deferred()
  let count = 0
  const { vm } = await setup({ listSessions: () => ++count === 1 ? old.promise : Promise.resolve([{ session_id: 'new' }]), countSessions: async () => ({ total_sessions: 1 }) })
  const previous = vm.reload()
  vm.query = 'new'; await vm.reload()
  old.resolve([{ session_id: 'old' }]); await previous
  assert.equal(vm.rows[0].session_id, 'new')
  vm.open = false; vm.invalidate(); await vm.reload()
  assert.equal(vm.rows[0].session_id, 'new')
  resetSessions(); assert.deepEqual(vm.rows, [])
  vm.disposeArchive()
})
test('删除当前页最后一条后回到有效页，取消归档不关闭弹层', async () => {
  const { vm } = await setup()
  vm.page = 3
  await vm.operate({ session_id: 'A' })
  assert.equal(vm.page, 1)
  assert.equal(vm.open, true)
  assert.equal(vm.notice, '已取消归档')
  vm.disposeArchive()
})
test('退出登录后旧删除请求的失败不再展示', async () => {
  let fail
  const started = deferred()
  const pending = new Promise((resolve, reject) => { fail = reject })
  const { vm } = await setup({ deleteSession: () => { started.resolve(); return pending } })
  const operation = vm.operate({ session_id: 'A' }, true)
  await started.promise
  resetSessions()
  fail(new Error('旧用户的网络错误'))
  await operation
  assert.equal(vm.notice, '')
  vm.disposeArchive()
})
test('侧栏归档、取消归档和删除的成功提示在三秒后消失', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const { vm } = await setup()
  vm.open = false // 侧栏的归档组件常驻，关闭设置不会销毁它
  for (const [operation, message] of [
    [() => vm.archive({ session_id: 'A' }), '已归档'],
    [() => vm.operate({ session_id: 'A' }), '已取消归档'],
    [() => vm.operate({ session_id: 'A' }, true), '已删除'],
  ]) {
    await operation()
    assert.equal(vm.notice, message)
    t.mock.timers.tick(2999)
    assert.equal(vm.notice, message)
    t.mock.timers.tick(1)
    assert.equal(vm.notice, '')
  }
  vm.disposeArchive()
})
test('连续归档产生相同提示时从最后一次操作重新计时', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const { vm } = await setup()
  await vm.archive({ session_id: 'A' })
  t.mock.timers.tick(2000)
  await vm.archive({ session_id: 'B' })
  t.mock.timers.tick(1000)
  assert.equal(vm.notice, '已归档')
  t.mock.timers.tick(1999)
  assert.equal(vm.notice, '已归档')
  t.mock.timers.tick(1)
  assert.equal(vm.notice, '')
  vm.disposeArchive()
})
test('操作失败提示也会自动消失', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const { vm } = await setup({ setSessionArchived: async () => { throw new Error('网络不可用') } })
  await vm.archive({ session_id: 'A' })
  assert.equal(vm.notice, '网络不可用')
  t.mock.timers.tick(3000)
  assert.equal(vm.notice, '')
  vm.disposeArchive()
})
test('组件销毁清理提示且迟到的写入结果不再显示提示', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const started = deferred(), write = deferred()
  const { vm } = await setup({ setSessionArchived: async () => { started.resolve(); await write.promise } })
  const operation = vm.archive({ session_id: 'A' })
  await started.promise
  vm.disposeArchive()
  write.resolve(); await operation
  assert.equal(vm.notice, '')
  t.mock.timers.tick(3000)
  assert.equal(vm.notice, '')
})
