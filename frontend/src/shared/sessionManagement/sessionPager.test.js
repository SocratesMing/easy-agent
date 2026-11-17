// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/sessionManagement/sessionPager.test.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { resetSessions } from './state.js'

// 只替换网络与共享 store，被测实现仍是 sessionPager.js 源码
const stateURL = new URL('./state.js', import.meta.url).href
const source = (await readFile(new URL('./sessionPager.js', import.meta.url), 'utf8'))
  .replace(/import .* from '..\/..\/api\/chat.js'/, 'const { listSessions } = globalThis.__pagerApi')
  .replace(/import .* from '..\/..\/features\/chat\/store\/chatStore.js'/, 'const { chatStore } = globalThis.__pagerStore')
  .replace("from './state.js'", `from '${stateURL}'`)

const page = (count, prefix) => Array.from({ length: count }, (_, i) => ({ session_id: `${prefix}${i}`, archived: false }))
let moduleId = 0

async function setup(pages = []) {
  resetSessions()
  const calls = []
  const store = { sessions: [], sessionsHasMore: false, sessionsLoading: false }
  globalThis.__pagerApi = {
    listSessions: async (username, params) => {
      calls.push([username, params])
      const next = pages.length ? pages.shift() : []
      if (next instanceof Error) throw next
      return next
    },
  }
  globalThis.__pagerStore = { chatStore: store }
  const mod = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}#${++moduleId}`)
  return { mod, calls, store }
}

const requestLog = calls => calls.map(([username, p]) => [username, p.limit, p.offset, p.archived])

test('首屏只取一页，滚动到底才续取下一页', async () => {
  const { mod, calls, store } = await setup([page(50, 'a'), page(30, 'b')])
  assert.equal(await mod.reloadSessionList('u'), true)
  assert.equal(store.sessions.length, 50)
  assert.equal(store.sessionsHasMore, true)
  assert.equal(store.sessionsLoading, false)

  assert.equal(await mod.loadMoreSessionList('u'), true)
  assert.equal(store.sessions.length, 80)
  assert.equal(store.sessionsHasMore, false)
  assert.deepEqual(requestLog(calls), [['u', 50, 0, false], ['u', 50, 50, false]])

  // 到底后不再发请求
  assert.equal(await mod.loadMoreSessionList('u'), false)
  assert.equal(calls.length, 2)
})

test('刷新按已展开的条数重取，不把列表缩回首屏一页', async () => {
  const { mod, calls, store } = await setup([page(50, 'a'), page(50, 'b'), page(100, 'c')])
  await mod.reloadSessionList('u')
  await mod.loadMoreSessionList('u')
  assert.equal(store.sessions.length, 100)

  assert.equal(await mod.reloadSessionList('u'), true)
  assert.equal(store.sessions.length, 100)
  assert.equal(store.sessionsHasMore, true)
  assert.deepEqual(requestLog(calls).at(-1), ['u', 100, 0, false])
})

test('续取追加时按 session_id 去重', async () => {
  const second = page(50, 'b')
  second[0] = { session_id: 'a49', archived: false }
  const { mod, store } = await setup([page(50, 'a'), second])
  await mod.reloadSessionList('u')
  await mod.loadMoreSessionList('u')
  const ids = store.sessions.map(s => s.session_id)
  assert.equal(ids.length, 99)
  assert.equal(new Set(ids).size, 99)
})

test('退出登录后分页从第一页重新开始', async () => {
  const { mod, calls, store } = await setup([page(50, 'a'), page(50, 'b'), page(50, 'c')])
  await mod.reloadSessionList('u')
  await mod.loadMoreSessionList('u')
  assert.equal(store.sessions.length, 100)
  resetSessions()
  assert.equal(store.sessionsHasMore, false)

  await mod.reloadSessionList('u')
  assert.deepEqual(requestLog(calls).at(-1), ['u', 50, 0, false])
})

test('请求失败时保留原有列表并允许重试', async () => {
  const { mod, store } = await setup([page(50, 'a'), new Error('离线')])
  await mod.reloadSessionList('u')
  await assert.rejects(() => mod.loadMoreSessionList('u'), /离线/)
  assert.equal(store.sessions.length, 50)
  assert.equal(store.sessionsLoading, false)
  assert.equal(store.sessionsHasMore, true)
})
