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
 * - `current`：界面正在展示的会话。用户刷新时看的就是它，哪怕在飞记录丢了
 *   （换了标签页、记录过期、发送后立刻刷新还没写出记录）也必须把它列进来，
 *   否则这一轮永远没人探测、内容也就永远补不回来。
 *
 * 返回 `{ ids, wait, lastActive }`：`wait` 是最可能还在准备中的那个会话，只有它会
 * 进入轮询等待。
 */
export function resumeCandidates({ saved, flight, sessions, current }) {
  const ids = []
  const add = (sid) => {
    if (sid && !ids.includes(sid)) ids.push(sid)
  }
  const currentId = current || ''
  // 展示中的会话排在最前：它是「刷新时用户正在看的那一轮」的首要候选
  if (currentId) add(currentId)
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
      // 会话可能还没建好（列表里没有）：先只留空，交给 resolveResumeTargets 等待
      const recent = (sessions || [])
        .filter(s => s && s.session_id && toMs(s.updated_at) >= flight.at - FLIGHT_MATCH_MS)
        .sort((a, b) => toMs(b.updated_at) - toMs(a.updated_at))
        .slice(0, 3)
      recent.forEach(s => add(s.session_id))
      wait = recent.length ? recent[0].session_id : ''
    }
  }
  // 在飞记录缺失/过期时，展示中的会话就是最该等待的那一个
  if (!wait) wait = currentId
  return {
    ids: ids.slice(0, 5),
    wait,
    lastActive: (saved && saved.lastActive) || '',
  }
}

// 打开会话的调用方需要完整状态，区分运行中的流和已结束但仍可回放的流。
export async function probeStreamState(sessionId, getStatus) {
  if (!sessionId || typeof getStatus !== 'function') return null
  try {
    return await getStatus(sessionId)
  } catch (e) {
    return null
  }
}

/**
 * 打开会话时的探活：**不依赖本地「在飞」记录**，直接问后端这一轮还在不在跑。
 *
 * 为什么需要它：「在飞」记录存在 `sessionStorage`，换浏览器、换标签页、
 * 重新登录后都是空的。此时后台那一轮可能还在跑，但前端不会挂 `/stream/live`，用户看到的
 * 就是「停在半截的静态页面」——而内容其实还在源源不断地产出。刷新场景由
 * `resolveResumeTargets` 兜底，打开/切换会话的场景只回来这里。
 *
 * 返回 true 表示调用方应该挂 `/stream/live`（并把它标成"进行中"）。
 * 查询失败（会话刚被删除、网络抖动）一律视为不活跃：探活不该影响打开会话。
 */
export async function shouldAttachOnOpen(sessionId, getStatus) {
  const status = await probeStreamState(sessionId, getStatus)
  return Boolean(status && status.active)
}

/**
 * 单个会话的流状态（`/stream/status` 的薄包装）。
 *
 * 调用方（`chatSession.js` 的 `probeLiveStream`）需要拿到 `active` 与 `replayable`
 * 两个字段判断「挂实时流」还是「回放过程事件」，因此这里原样返回状态对象；
 * 查询失败返回 `null`（探活失败绝不能影响打开会话）。
 */
export async function probeStreamState(sessionId, getStatus) {
  if (!sessionId || typeof getStatus !== 'function') return null
  try {
    return await getStatus(sessionId)
  } catch (e) {
    return null
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
 * 这一轮的答复是否已经落地：正文 / 思考 / 工具卡片 / 审批载荷 / 异常兜底文案任一非空
 * 即算。
 *
 * 只认 assistant：最后一条是 user 说明模型还没回；最后一条是「空 assistant」
 * （占位或刚落库还没写入内容）同样不算答复。落库的进行中消息常常只有 blocks
 * 没有 content，因此不能只看正文。
 *
 * ``fallback_note`` 也算：那是后端在「这一轮没跑完」时落的中性说明（超时 / 上游断连
 * 等），有它就说明这一轮已经收尾了，再等下去只是空转。
 */
export function hasAssistantReply(message) {
  if (!message || message.role !== 'assistant') return false
  return Boolean(
    message.content
    || message.thinking
    || message.pending_approval
    || message.fallback_note
    || (Array.isArray(message.tool_calls) && message.tool_calls.length > 0)
    || (Array.isArray(message.blocks) && message.blocks.length > 0)
  )
}

/** 从会话列表里按「更新时间落在发送时刻附近」挑出这一轮的会话（置顶旧会话不参与）。 */
function pickRecentSession(sessions, flight) {
  const recent = (sessions || [])
    .filter(s => s && s.session_id && toMs(s.updated_at) >= flight.at - FLIGHT_MATCH_MS)
    .sort((a, b) => toMs(b.updated_at) - toMs(a.updated_at))
  return recent.length ? recent[0].session_id : ''
}

/**
 * 最后一条消息是否属于「本轮」并且是答复。
 *
 * 只看 role 会踩坑：多轮会话刷新在「本轮提问还没落库」的窗口时，历史最后一条仍是
 * 上一轮的 assistant，会被误判成「这一轮已经答完了」从而放弃等待。
 * 用时间戳把它排除：本轮的消息不可能早于发送时刻。
 */
export function turnCompleted(message, flight) {
  if (!hasAssistantReply(message)) return false
  if (!flight || !flight.at) return true
  const ts = toMs(message.timestamp || message.created_at)
  if (!ts) return true
  return ts >= flight.at - 5000
}

/**
 * 等「新会话首轮」的会话出现在列表里。
 *
 * 刷新若落在后端还没建会话的准备期，前端手里只有一个空 sid 的在飞记录，会话列表
 * 里也还没有它——这时既没有候选会话可探测，也不知道该接哪一个。几秒后会话建好、
 * start 事件把 sid 补写进在飞记录，才具备续接条件，所以这里短轮询等它出现，
 * 而不是一次查不到就放弃（那正是「刷新太早就不出流」的成因）。
 */
export async function waitForSessionId(opts) {
  const options = opts || {}
  const flight = options.flight
  const refreshSessions = options.refreshSessions
  if (!flight || flight.sessionId || typeof refreshSessions !== 'function') return ''
  const intervalMs = options.intervalMs || 1500
  const timeoutMs = options.timeoutMs || 20000
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    await sleep(intervalMs)
    let list = []
    try {
      list = (await refreshSessions()) || []
    } catch (e) {
      list = []
    }
    const found = pickRecentSession(list, flight)
    if (found) return found
    // 会话已建好且 start 事件补写了 sid：直接读在飞记录更快
    const fresh = readStreamFlight()
    if (fresh && fresh.sessionId) return fresh.sessionId
  }
  return ''
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
  let plan = resumeCandidates({
    saved: options.saved,
    flight: options.flight,
    sessions: options.sessions,
    // 展示中的会话是「刷新时用户正在看的那一轮」的首要线索，必须传下去；
    // 之前漏传导致在飞记录为空时 ids 直接为空，连一次 /stream/status 都不打。
    current: options.current,
  })
  // 新会话首轮：刷新落在后端还没建会话的准备期，列表里也还没有它——等它出现，
  // 否则这一轮永远没人接，页面就停在「只有提问、没有回答」。
  if (!plan.ids.length && options.flight) {
    const sid = await waitForSessionId({
      flight: options.flight,
      refreshSessions: options.refreshSessions,
      intervalMs: options.sessionIntervalMs,
      timeoutMs: options.sessionTimeoutMs,
    })
    if (sid) {
      plan = { ids: [sid], wait: sid, lastActive: '' }
    }
  }
  if (!plan.ids.length) return { ...plan, active: [], waited: false }

  let active = await probeActive(plan.ids, options.getStatus)
  let waited = false
  if (!active.length && plan.wait && options.getLastMessage) {
    const last = await safeLastMessage(options.getLastMessage, plan.wait)
    // 判据从「最后一条是不是 user」放宽到「这一轮到底有没有答复」：刷新可能落在
    // 用户消息还没落库的窗口（历史里仍停着上一轮的 assistant），只看 user 就会
    // 直接放弃；历史一条消息都没有（新会话首轮）时，只有确实刚发过才值得等。
    const worthWaiting = last || options.flight
    if (worthWaiting && !turnCompleted(last, options.flight)) {
      waited = true
      active = await waitForActive([plan.wait], options.getStatus, {
        timeoutMs: options.timeoutMs,
        intervalMs: options.intervalMs,
        isFinished: async (sid) =>
          turnCompleted(await safeLastMessage(options.getLastMessage, sid), options.flight),
      })
    }
  }
  return { ...plan, active, waited }
}
