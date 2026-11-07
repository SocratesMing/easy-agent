/**
 * 流式续接单测：刷新后能否把页面接回「正在跑的那一轮」。
 *
 * 回归背景（2026-11-07）：会话进行中刷新页面，前端不再吐流。日志里连
 * /agent/chat/stream/status 都没有——说明不是「重连失败」而是「从未尝试重连」：
 * 新会话首轮发送时还没有 session_id，markStreaming(null) 是空操作，
 * sessionStorage 里没有记录，tryAttachLiveStream 直接 return。后端在 Agent/MCP
 * 初始化期间也还没占流锁（/stream/status = inactive），一次探测就放弃同样接不回。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  clearStreamFlight,
  markStreamFlight,
  probeActive,
  readStreamFlight,
  resolveResumeTargets,
  resumeCandidates,
  waitForActive,
} from '../src/packages/easyAgent/features/chat/services/streamResume.js'

// Node 没有 sessionStorage：模块内部是懒取（safeStorage），这里补一个最小实现
function installStorage() {
  const map = new Map()
  globalThis.sessionStorage = {
    getItem: k => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: k => map.delete(k),
  }
  return map
}

const MIN = 60 * 1000

test('在飞记录：发送即可写入，sid 未定时留空，过期自动失效', () => {
  installStorage()
  markStreamFlight(null)
  const flight = readStreamFlight()
  assert.ok(flight, '刚写入的记录应可读到')
  assert.equal(flight.sessionId, '')
  assert.ok(Date.now() - flight.at < 5000)

  // 10 分钟后视为早已结束，不再追
  assert.equal(readStreamFlight(Date.now() + 11 * MIN), null)

  markStreamFlight('s-1')
  clearStreamFlight()
  assert.equal(readStreamFlight(), null)
})

test('候选会话：优先用持久化的活跃会话，其次补上在飞记录里的 sid', () => {
  installStorage()
  const plan = resumeCandidates({
    saved: { streaming: true, streamingIds: ['s-1'], lastActive: 's-1' },
    flight: { sessionId: 's-2', at: Date.now() },
    sessions: [],
  })
  assert.deepEqual(plan.ids, ['s-1', 's-2'])
  assert.equal(plan.wait, 's-2')
  assert.equal(plan.lastActive, 's-1')
})

test('候选会话：新会话首轮（sid 未知）按 updated_at 到列表里找回，置顶旧会话不参与', () => {
  const at = Date.now()
  const plan = resumeCandidates({
    saved: null,
    flight: { sessionId: '', at },
    sessions: [
      // 置顶会话排在最前，但更新时间很早：不能当成目标
      { session_id: 'pinned-old', updated_at: new Date(at - 3 * 60 * MIN).toISOString() },
      { session_id: 'just-sent', updated_at: new Date(at - 2000).toISOString() },
      { session_id: 'older', updated_at: new Date(at - 60 * MIN).toISOString() },
    ],
  })
  assert.deepEqual(plan.ids, ['just-sent'])
  assert.equal(plan.wait, 'just-sent')
})

test('候选会话：没有任何线索时返回空，不再空打接口', () => {
  const plan = resumeCandidates({ saved: null, flight: null, sessions: [] })
  assert.deepEqual(plan.ids, [])
  assert.equal(plan.wait, '')
})

test('探测活跃：单个会话查询失败不影响其它会话', async () => {
  const active = await probeActive(['bad', 's-1'], async (sid) => {
    if (sid === 'bad') throw new Error('404')
    return { active: true }
  })
  assert.deepEqual(active, ['s-1'])
})

test('续接决策：后端已占流锁时直接挂载，不进入等待', async () => {
  installStorage()
  let calls = 0
  const plan = await resolveResumeTargets({
    saved: { streaming: true, streamingIds: ['s-1'], lastActive: 's-1' },
    flight: { sessionId: 's-1', at: Date.now() },
    sessions: [],
    getStatus: async () => {
      calls += 1
      return { active: true }
    },
    getLastMessage: async () => ({ role: 'user' }),
  })
  assert.deepEqual(plan.active, ['s-1'])
  assert.equal(plan.waited, false)
  assert.equal(calls, 1)
})

test('续接决策：准备期 inactive + 最后一轮是用户消息 -> 轮询等到活跃（本次故障场景）', async () => {
  installStorage()
  let polls = 0
  const plan = await resolveResumeTargets({
    saved: { streaming: true, streamingIds: ['s-1'], lastActive: 's-1' },
    flight: { sessionId: 's-1', at: Date.now() },
    sessions: [],
    // 前两次仍 inactive（Agent/MCP 还在建），第三次才开始活跃
    getStatus: async () => {
      polls += 1
      return { active: polls > 2 }
    },
    getLastMessage: async () => ({ role: 'user' }),
    intervalMs: 5,
    timeoutMs: 500,
  })
  assert.deepEqual(plan.active, ['s-1'])
  assert.equal(plan.waited, true)
  assert.ok(polls > 2)
})

test('续接决策：最后一轮已答复（assistant）时不等待，直接回填历史', async () => {
  installStorage()
  let polls = 0
  const plan = await resolveResumeTargets({
    saved: { streaming: true, streamingIds: ['s-1'], lastActive: 's-1' },
    flight: { sessionId: 's-1', at: Date.now() },
    sessions: [],
    getStatus: async () => {
      polls += 1
      return { active: false }
    },
    getLastMessage: async () => ({ role: 'assistant' }),
    intervalMs: 5,
    timeoutMs: 500,
  })
  assert.deepEqual(plan.active, [])
  assert.equal(plan.waited, false)
  assert.equal(polls, 1)
})

test('续接决策：等待期间这轮答复完成 -> 提前结束等待，标记 waited', async () => {
  installStorage()
  let historyCalls = 0
  const start = Date.now()
  const plan = await resolveResumeTargets({
    saved: null,
    flight: { sessionId: 's-1', at: Date.now() },
    sessions: [],
    getStatus: async () => ({ active: false }),
    // 第 1 次是等待前的判定（还没答复）；之后模拟这一轮跑完、答复落库
    getLastMessage: async () => {
      historyCalls += 1
      return historyCalls === 1 ? { role: 'user' } : { role: 'assistant' }
    },
    intervalMs: 5,
    timeoutMs: 5000,
  })
  assert.deepEqual(plan.active, [])
  assert.equal(plan.waited, true)
  assert.ok(historyCalls >= 2)
  // 提前结束：不该等满 5 秒
  assert.ok(Date.now() - start < 1000)
})

test('等待超时：始终 inactive 时按超时返回空，不阻塞页面', async () => {
  const start = Date.now()
  const active = await waitForActive(['s-1'], async () => ({ active: false }), {
    intervalMs: 5,
    timeoutMs: 40,
  })
  assert.deepEqual(active, [])
  assert.ok(Date.now() - start >= 40)
})
