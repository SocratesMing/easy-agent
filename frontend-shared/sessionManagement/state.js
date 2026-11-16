// List membership is a view, never proof of session existence.
let epoch = 0
let revision = 0
let listVersion = 0
const metadata = new Map()
const deleted = new Set()
const listeners = new Set()
export function subscribeSessions(fn) { listeners.add(fn); return () => listeners.delete(fn) }
function emit(event) { listeners.forEach(fn => fn(event)) }
export function resetSessions() {
  epoch++; revision++; listVersion++
  metadata.clear(); deleted.clear()
  emit({ type: 'reset' })
}
export function sessionGuard(id) {
  const started = epoch
  return () => started === epoch && !deleted.has(id)
}
export function isSessionDeleted(id) { return deleted.has(id) }
export function sessionMeta(id) { return metadata.get(id) }
export function rememberSession(data, expectedRevision = revision) {
  if (!data?.session_id || deleted.has(data.session_id)) return
  const previous = metadata.get(data.session_id) || {}
  const archived = expectedRevision === revision ? data.archived : previous.archived
  metadata.set(data.session_id, { session_id: data.session_id, title: data.title || previous.title, archived })
}
export function archiveChanged(id, archived) {
  revision++; listVersion++
  rememberSession({ session_id: id, archived })
  emit({ type: 'archive', sessionId: id, archived })
}
export function sessionDeleted(id) {
  revision++; listVersion++; deleted.add(id); metadata.delete(id)
}
export async function loadDailySessions(load, apply) {
  const version = ++listVersion, started = epoch, changed = revision
  try {
    const rows = await load()
    if (version !== listVersion || started !== epoch || changed !== revision) return false
    const data = Array.isArray(rows) ? rows : rows.sessions || []
    data.forEach(row => rememberSession(row))
    apply(data.filter(s => !s.archived && !deleted.has(s.session_id)))
    return true
  } catch (error) {
    if (version !== listVersion || started !== epoch || changed !== revision) return false
    throw error
  }
}
export function staleSessionError() { return Object.assign(new Error('会话请求已过期'), { name: 'StaleSessionResponse' }) }
export function isStaleSessionError(error) { return error?.name === 'StaleSessionResponse' }

export async function initialSessionId({ linked, stored, rows, read }) {
  const valid = sessionGuard()
  const candidate = linked || (stored !== '__new__' ? stored : null)
  if (candidate) {
    try { await read(candidate); if (valid()) return candidate } catch { /* inaccessible */ }
  }
  if (!valid() || stored === '__new__') return null
  return rows[0]?.session_id || null
}
export function sessionRevision() { return revision }
