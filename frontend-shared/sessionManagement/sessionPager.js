// 会话列表分页：首屏只取一页，滚动到底再续取。
// 一次性把全部会话拉回来（每页 100 连发多次）在会话多的账号上会拖慢首屏。
import { listSessions } from '../../api/chat.js'
import { chatStore } from '../../features/chat/store/chatStore.js'
import { loadDailySessions, subscribeSessions } from './state.js'

// 一页条数：后端单页上限 100，用 50 既能让首屏够长，单次请求也不重
export const SESSION_PAGE_SIZE = 50
const SESSION_PAGE_MAX = 100

let loaded = 0 // 已取到的原始行数，下一页的 offset（不能用已展示条数：归档/删除会筛掉行）
let more = true
let busy = false

function mark(loading, hasMore) {
  busy = loading
  if (hasMore !== undefined) more = hasMore
  chatStore.sessionsLoading = busy
  chatStore.sessionsHasMore = more
}

// 重置后还没有列表：先不给「还有更早的会话」，等首屏请求回来再按结果置位
export function resetSessionPaging() { loaded = 0; mark(false, false) }

// 退出登录/切换用户后从第一页重新开始
subscribeSessions(event => { if (event.type === 'reset') resetSessionPaging() })

async function fetchPage(username, limit, offset) {
  const rows = await listSessions(username || null, { archived: false, limit, offset })
  return Array.isArray(rows) ? rows : rows?.sessions || []
}

/**
 * 首屏与刷新：整体替换列表。
 * 已滚动加载过 N 页时按已展开的条数重取（后端单页上限 100 封顶），避免刷新后列表缩回一页。
 */
export async function reloadSessionList(username, after) {
  if (busy) return false
  const limit = Math.min(SESSION_PAGE_MAX, Math.max(SESSION_PAGE_SIZE, loaded))
  mark(true)
  let raw = []
  try {
    const ok = await loadDailySessions(
      () => fetchPage(username, limit, 0).then(rows => (raw = rows)),
      rows => { chatStore.sessions = rows },
    )
    if (!ok) return false
    loaded = raw.length
    mark(false, raw.length >= limit)
    after?.()
    return true
  } finally {
    mark(false)
  }
}

/** 滚动到底：续取下一页并追加（按 session_id 去重，翻页期间排序上移不会重复渲染）。 */
export async function loadMoreSessionList(username, after) {
  if (busy || !more) return false
  mark(true)
  let raw = []
  try {
    const ok = await loadDailySessions(
      () => fetchPage(username, SESSION_PAGE_SIZE, loaded).then(rows => (raw = rows)),
      rows => {
        const seen = new Set(chatStore.sessions.map(s => s && s.session_id))
        chatStore.sessions = chatStore.sessions.concat(rows.filter(s => s && !seen.has(s.session_id)))
      },
    )
    if (!ok) return false
    loaded += raw.length
    mark(false, raw.length >= SESSION_PAGE_SIZE)
    after?.()
    return true
  } finally {
    mark(false)
  }
}
