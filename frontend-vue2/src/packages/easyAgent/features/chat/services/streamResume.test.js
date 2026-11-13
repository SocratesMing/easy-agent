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
  hasAssistantReply,
  markStreamFlight,
  probeActive,
  readStreamFlight,
  resolveResumeTargets,
  resumeCandidates,
  shouldAttachOnOpen,
  turnCompleted,
  waitForActive,
} from './streamResume.js'

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

test('候选会话：在飞记录丢失时，展示中的会话仍要探测并作为等待目标', () => {
  // 换标签页 / 发送后立刻刷新（记录还没写）/ 记录过期：只剩「界面正在看的会话」这条线索
  const plan = resumeCandidates({ saved: null, flight: null, sessions: [], current: 'cur-1' })
  assert.deepEqual(plan.ids, ['cur-1'])
  assert.equal(plan.wait, 'cur-1')
})

test('候选会话：展示中的会话排最前且不重复', () => {
  const plan = resumeCandidates({
    saved: { streaming: true, streamingIds: ['s-1', 's-2'], lastActive: 's-2' },
    flight: { sessionId: 's-1', at: Date.now() },
    sessions: [],
    current: 's-1',
  })
  assert.deepEqual(plan.ids, ['s-1', 's-2'])
  assert.equal(plan.wait, 's-1')
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
    flight: { sessionId: 's-1', at: Date.now() - 30000 },
    sessions: [],
    // 回复时间晚于发送时刻：确认是本轮的答复
    getStatus: async () => {
      polls += 1
      return { active: false }
    },
    getLastMessage: async () => ({
      role: 'assistant',
      content: '做好了',
      timestamp: new Date().toISOString(),
    }),
    intervalMs: 5,
    timeoutMs: 500,
  })
  assert.deepEqual(plan.active, [])
  assert.equal(plan.waited, false)
  assert.equal(polls, 1)
})

test('续接决策：本轮提问还没落库（历史停在上一轮答复）仍在等待，不误判为已完成', async () => {
  installStorage()
  let polls = 0
  const sentAt = Date.now()
  const plan = await resolveResumeTargets({
    saved: null,
    flight: { sessionId: 's-1', at: sentAt },
    sessions: [],
    // 发送后 3 个轮询周期后端才占上流锁
    getStatus: async () => {
      polls += 1
      return { active: polls > 3 }
    },
    // 上一次轮询得到的最后一条是上一轮的答复（时间早于本次发送）
    getLastMessage: async () => ({
      role: 'assistant',
      content: '上一轮的答复',
      timestamp: new Date(sentAt - 60000).toISOString(),
    }),
    intervalMs: 5,
    timeoutMs: 1000,
  })
  assert.deepEqual(plan.active, ['s-1'])
  assert.equal(plan.waited, true)
  assert.ok(polls > 3)
})

test('续接决策：新会话首轮的会话还没建好 -> 等列表刷新把它找回来再接', async () => {
  installStorage()
  markStreamFlight(null)
  let refreshCalls = 0
  const plan = await resolveResumeTargets({
    saved: null,
    flight: readStreamFlight(),
    sessions: [],
    getStatus: async () => ({ active: true }),
    getLastMessage: async () => null,
    // 前两次列表里还没有这个会话（后端还在准备），第三次才出现
    refreshSessions: async () => {
      refreshCalls += 1
      return refreshCalls >= 3
        ? [{ session_id: 'new-1', updated_at: new Date().toISOString() }]
        : []
    },
    sessionIntervalMs: 5,
    sessionTimeoutMs: 500,
    intervalMs: 5,
    timeoutMs: 500,
  })
  assert.deepEqual(plan.active, ['new-1'])
  assert.ok(refreshCalls >= 3)
})

test('续接决策：展示中的会话（在飞记录缺失）也参与探测', async () => {
  installStorage()
  const probed = []
  const plan = await resolveResumeTargets({
    saved: null,
    flight: null,
    sessions: [],
    current: 'cur-1',
    getStatus: async (sid) => {
      probed.push(sid)
      return { active: false }
    },
    getLastMessage: async () => ({ role: 'assistant', content: '上一轮答复' }),
    intervalMs: 5,
    timeoutMs: 50,
  })
  assert.deepEqual(probed, ['cur-1'])
  assert.deepEqual(plan.active, [])
})

test('续接决策：等待期间这轮答复完成 -> 提前结束等待，标记 waited', async () => {
  installStorage()
  const sentAt = Date.now()
  let historyCalls = 0
  const start = Date.now()
  const plan = await resolveResumeTargets({
    saved: null,
    flight: { sessionId: 's-1', at: sentAt },
    sessions: [],
    getStatus: async () => ({ active: false }),
    // 第 1 次是等待前的判定（还没答复）；之后模拟这一轮跑完、答复落库
    getLastMessage: async () => {
      historyCalls += 1
      return historyCalls === 1
        ? { role: 'user', timestamp: new Date(sentAt).toISOString() }
        : { role: 'assistant', content: '答复', timestamp: new Date().toISOString() }
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

test('异常兜底文案算「这一轮已收尾」：有 fallback_note 就不再等', async () => {
  const sentAt = Date.now()
  // 普通异常 / 超时都会在落库消息上带 fallback_note：这一轮已经结束（虽然没跑完），
  // 再盯着等答复只是空转。
  assert.equal(hasAssistantReply({ role: 'assistant', fallback_note: '异常' }), true)
  assert.equal(
    turnCompleted(
      { role: 'assistant', fallback_note: '异常', timestamp: new Date(sentAt + 100).toISOString() },
      { sessionId: 's-1', at: sentAt }
    ),
    true
  )
  // 空 assistant 占位仍算「还没答复」
  assert.equal(hasAssistantReply({ role: 'assistant' }), false)
  // 上一轮遗留的兜底文案不会被当成本轮答复（时间戳早于发送时刻）
  assert.equal(
    turnCompleted(
      { role: 'assistant', fallback_note: '异常', timestamp: new Date(sentAt - 600000).toISOString() },
      { sessionId: 's-1', at: sentAt }
    ),
    false
  )
})

test('打开会话时的探活：只认后端 active，失败一律当不活跃', async () => {
  // 这条判据不读 sessionStorage：换浏览器 / 换标签页 / 重新登录后本地在飞记录是空的，
  // 但后台那一轮可能还在跑。只问后端，不问本地。
  const asked = []
  const active = await shouldAttachOnOpen('s-1', async (sid) => {
    asked.push(sid)
    return { session_id: sid, active: true }
  })
  assert.equal(active, true)
  assert.deepEqual(asked, ['s-1'])

  // inactive / 空响应 → 不挂
  assert.equal(await shouldAttachOnOpen('s-1', async () => ({ active: false })), false)
  assert.equal(await shouldAttachOnOpen('s-1', async () => null), false)
  // 会话刚被删除、网络抖动：探活失败不能影响打开会话本身
  assert.equal(
    await shouldAttachOnOpen('s-1', async () => {
      throw new Error('404')
    }),
    false
  )
  // 没有会话 id / 没有查询函数：直接不挂，也不发请求
  let called = false
  assert.equal(
    await shouldAttachOnOpen('', async () => {
      called = true
      return { active: true }
    }),
    false
  )
  assert.equal(called, false)
  assert.equal(await shouldAttachOnOpen('s-1', null), false)
})


test('probeStreamState 保留 inactive/replayable 状态供打开会话回放，并吞掉探测失败', async () => {
  const { probeStreamState } = await import('./streamResume.js')
  const status = { active: false, replayable: true }
  assert.equal(await probeStreamState('s1', async sid => { assert.equal(sid, 's1'); return status }), status)
  assert.equal(await probeStreamState('s1', async () => { throw new Error('offline') }), null)
  assert.equal(await probeStreamState('', () => { throw new Error('must not call') }), null)
  assert.equal(await probeStreamState('s1', null), null)
})
