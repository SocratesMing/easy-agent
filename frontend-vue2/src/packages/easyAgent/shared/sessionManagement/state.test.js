// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/sessionManagement/state.test.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
import test from 'node:test'
import assert from 'node:assert/strict'
import { resetSessions, archiveChanged, sessionMeta, loadDailySessions, sessionGuard, sessionDeleted } from './state.js'
const deferred = () => { let resolve; return { promise: new Promise(r => { resolve = r }), resolve } }
test('列表中的每条会话都按当前版本保存元数据', async () => {
  resetSessions()
  await loadDailySessions(async () => [{ session_id: 'A', archived: false }, { session_id: 'B', archived: false }], () => {})
  assert.equal(sessionMeta('A').archived, false)
  assert.equal(sessionMeta('B').archived, false)
})
test('归档成功使之前发出的列表响应失效，归档元数据不依赖列表行', async () => {
  resetSessions()
  const request = deferred(); let rows = []
  const result = loadDailySessions(() => request.promise, data => { rows = data })
  archiveChanged('A', true)
  request.resolve([{ session_id: 'A', archived: false }])
  assert.equal(await result, false)
  assert.deepEqual(rows, [])
  assert.equal(sessionMeta('A').archived, true)
})
test('切换用户、退出登录和删除后不能回填历史', async () => {
  resetSessions()
  const valid = sessionGuard('A'), request = deferred(); let applied = false
  const result = loadDailySessions(() => request.promise, () => { applied = true })
  resetSessions()
  request.resolve([{ session_id: 'A' }])
  await result
  assert.equal(applied, false)
  assert.equal(valid(), false)
  const alive = sessionGuard('B')
  sessionDeleted('B')
  assert.equal(alive(), false)
})
test('较新的列表请求优先；超过前 50 条的缺项不表示删除', async () => {
  resetSessions()
  const request = deferred(); let rows
  const old = loadDailySessions(() => request.promise, data => { rows = data })
  await loadDailySessions(async () => [{ session_id: 'new' }], data => { rows = data })
  request.resolve([{ session_id: 'old' }]); await old
  assert.equal(rows[0].session_id, 'new')
  assert.equal(sessionGuard('not-in-list')(), true)
})
