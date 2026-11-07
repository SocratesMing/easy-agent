/**
 * 流式续接：刷新页面后把页面重新接回「正在跑的那一轮」。
 *
 * 背景（2026-11-07）：新会话首轮发送时前端还没有 session_id（要等后端 start
 * 事件才有），`markStreaming(null)` 是空操作，sessionStorage 里不会留下任何记录。
 * 此时刷新页面，tryAttachLiveStream 读到空就直接返回，既不回放也不挂载 —— 页面
 * 表现为「刷新后不再吐流」。而这一轮其实还在后台跑（后端断开只退订不取消）。
 *
 * 更难的是后端在 Agent/MCP 初始化完成前还没占流锁，/stream/status 仍是
 * inactive，而这个准备窗口常见十几秒到几十秒，正好是最容易刷新的时段。
 *
 * 这里补两件事：
 * 1. markStreamFlight：发送即落一条「本轮在飞」记录（含时间戳，sid 未知时为空），
 *    刷新后据此到会话列表里把会话找回来；
 * 2. waitForActive：状态还是 inactive 时短轮询等它变活跃，而不是一次探测就放弃。
 */

const FLIGHT_KEY = 'easy_agent_stream_flight'
// 记录有效期：超过它说明这轮早已结束（或换了标签页），不再追
const FLIGHT_TTL_MS = 10 * 60 * 1000
// 按会话 updated_at 找回目标会话的容差（消息落库时间与本地时间有秒级偏差）
const FLIGHT_MATCH_MS = 5 * 60 * 1000
// 默认轮询参数：间隔 3 秒、最多等 90 秒（覆盖 Agent/MCP 初始化 + 首个事件之前）
const WAIT_INTERVAL_MS = 3000
const WAIT_TIMEOUT_MS = 90 * 1000

function safeStorage() {
  try {
    return typeof sessionStorage === 'undefined' ? null : sessionStorage
  } catch (e) {
    // 隐私模式 / 禁用 Cookie 时访问会抛错
    return null
  }
}

/** 把后端时间（"2026-11-07 14:47:16" / ISO / 时间戳）统一成毫秒。 */
function toMs(value) {
  if (!value) return 0
  if (typeof value === 'number') return value
  const ms = Date.parse(String(value).trim().replace(' ', 'T'))
  return isNaN(ms) ? 0 : ms
}

/** 本轮已发出、结果还未落地：记一条在飞记录（sid 可能还不知道）。 */
export function markStreamFlight(sessionId) {
  const store = safeStorage()
  if (!store) return
  try {
    store.setItem(FLIGHT_KEY, JSON.stringify({
      sessionId: sessionId || '',
      at: Date.now(),
    }))
  } catch (e) { /* 写不进去不影响主流程 */ }
}

export function clearStreamFlight() {
  const store = safeStorage()
  if (store) {
    try {
      store.removeItem(FLIGHT_KEY)
    } catch (e) { /* ignore */ }
  }
}

/** 读在飞记录；过期返回 null（避免对早已结束的一轮空等）。 */
export function readStreamFlight(now) {
  const store = safeStorage()
  if (!store) return null
  let raw = null
  try {
    raw = store.getItem(FLIGHT_KEY)
  } catch (e) {
    return null
  }
  if (!raw) return null
  let data = null
  try {
    data = JSON.parse(raw)
  } catch (e) {
    return null
  }
  if (!data || !data.at) return null
  if ((typeof now === 'number' ? now : Date.now()) - data.at > FLIGHT_TTL_MS) return null
  return data
}

/**
 * 算出刷新后要探测的会话。
 *
 * - `saved`：上次持久化的流式状态（streamingIds 里都是已知 sid 的会话）
 * - `flight`：发送时落的在飞记录。sid 为空说明是「新会话首轮」，此时按会话列表里
 *   updated_at 落在发送时刻附近的会话找回（列表按 updated_at 倒序，但置顶会话会
 *   排在前面，所以必须按时间过滤，不能直接取第一条）
 *
 * 返回 `{ ids, wait, lastActive }`：`wait` 是最可能还在准备中的那个会话，只有它会
 * 进入轮询等待。
 */
export function resumeCandidates({ saved, flight, sessions }) {
  const ids = []
  const add = (sid) => {
    if (sid && !ids.includes(sid)) ids.push(sid)
  }
  if (saved && saved.streaming) {
    const savedIds = Array.isArray(saved.streamingIds) && saved.streamingIds.length
      ? saved.streamingIds
      : (saved.sessionId ? [saved.sessionId] : [])
    savedIds.forEach(add)
  }
  let wait = ''
  if (flight) {
    if (flight.sessionId) {
      add(flight.sessionId)
      wait = flight.sessionId
    } else {
      const recent = (sessions || [])
        .filter(s => s && s.session_id && toMs(s.updated_at) >= flight.at - FLIGHT_MATCH_MS)
        .slice(0, 3)
      recent.forEach(s => add(s.session_id))
      wait = recent.length ? recent[0].session_id : ''
    }
  }
  return {
    ids: ids.slice(0, 5),
    wait,
    lastActive: (saved && saved.lastActive) || '',
  }
}

/** 逐个问后端「这一轮还在跑吗」，返回确实活跃的会话。 */
export async function probeActive(ids, getStatus) {
  const active = []
  for (const sid of ids) {
    try {
      const status = await getStatus(sid)
      if (status && status.active && !active.includes(sid)) active.push(sid)
    } catch (e) {
      // 会话可能已被删除：忽略
    }
  }
  return active
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms))
}

/**
 * 等这一轮真正开始（后端占上流锁、能挂载了为止）。
 *
 * 结束条件：变活跃 / 超时 / 这一轮已经答复完（`isFinished` 返回 true，说明刷新时
 * 恰好赶在最后一刻，答复已落库，交给调用方回填历史即可）。
 */
export async function waitForActive(ids, getStatus, opts) {
  const options = opts || {}
  const intervalMs = options.intervalMs || WAIT_INTERVAL_MS
  const timeoutMs = options.timeoutMs || WAIT_TIMEOUT_MS
  const isFinished = options.isFinished
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    await sleep(intervalMs)
    const active = await probeActive(ids, getStatus)
    if (active.length) return active
    if (isFinished) {
      try {
        if (await isFinished(ids[0])) return []
      } catch (e) { /* 判定失败按「还没结束」继续等 */ }
    }
  }
  return []
}

async function safeLastMessage(getLastMessage, sid) {
  try {
    return await getLastMessage(sid)
  } catch (e) {
    return null
  }
}

/**
 * 页面刷新后的续接决策：要探测哪些会话、哪些确实还活着、要不要等。
 *
 * 返回 `{ ids, wait, lastActive, active, waited }`：
 * - `active` 是确认可挂载的会话（可能为空）
 * - `waited` 为真表示已经等过一轮但没等到，调用方应清在飞记录并回填历史
 */
export async function resolveResumeTargets(opts) {
  const options = opts || {}
  const plan = resumeCandidates({
    saved: options.saved,
    flight: options.flight,
    sessions: options.sessions,
  })
  if (!plan.ids.length) return { ...plan, active: [], waited: false }

  let active = await probeActive(plan.ids, options.getStatus)
  let waited = false
  if (!active.length && plan.wait && options.getLastMessage) {
    const last = await safeLastMessage(options.getLastMessage, plan.wait)
    if (last && last.role === 'user') {
      waited = true
      active = await waitForActive([plan.wait], options.getStatus, {
        timeoutMs: options.timeoutMs,
        intervalMs: options.intervalMs,
        isFinished: async (sid) => {
          const m = await safeLastMessage(options.getLastMessage, sid)
          return !!m && m.role !== 'user'
        },
      })
    }
  }
  return { ...plan, active, waited }
}
