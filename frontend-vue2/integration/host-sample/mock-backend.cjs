#!/usr/bin/env node
/**
 * 合同级 mock 后端（Node ≥ 14，无依赖）：托管宿主样例的 dist（/portal/），并按前端用到的接口合同
 * 返回最小数据，用于在真实浏览器里冒烟嵌入页面。不是 Easy Agent 后端的替代品。
 *
 *   node mock-backend.cjs --dist <宿主>/dist [--port 8091] [--base /portal/]
 *
 * 调试开关：
 *   POST /__mock/expire   之后所有已发 token 返回 401「登录已过期」（验证续期）
 *   POST /__mock/kick     之后所有已发 token 返回 401「其他设备登录」（验证不自动重登）
 *   GET  /__mock/log      请求日志（方法、路径、Authorization、是否带宿主 token / 宿主前缀）
 *   POST /__mock/reset    清空状态
 * 聊天消息里包含「慢」或 "slow" 时，流式回复持续约 8 秒（验证离开 / 返回页面时的重新挂载）。
 */
'use strict'

const fs = require('fs')
const http = require('http')
const path = require('path')
const { URL } = require('url')

const args = process.argv.slice(2)
const opt = (name, fallback) => {
  const i = args.indexOf(name)
  return i === -1 ? fallback : args[i + 1]
}
const DIST = path.resolve(opt('--dist', 'dist'))
const PORT = Number(opt('--port', 8091))
// 静态资源的部署基础路径（宿主样例为 /portal/；外网独立应用的 dist 用 --base /）
const BASE = opt('--base', '/portal/')
const isApi = (p) => /^\/(agent|api|__mock)(\/|$)/.test(p)

const MIME = {
  '.html': 'text/html; charset=utf-8', '.js': 'application/javascript', '.css': 'text/css',
  '.json': 'application/json', '.svg': 'image/svg+xml', '.woff2': 'font/woff2', '.woff': 'font/woff',
  '.ttf': 'font/ttf', '.png': 'image/png', '.ico': 'image/x-icon', '.map': 'application/json',
}

let state
function reset() {
  state = {
    tokens: {}, // token -> { username, status: 'ok' | 'expired' | 'kicked' }
    seq: 0,
    sessions: [],
    histories: {},
    streams: {}, // sessionId -> { events: [], done: boolean, listeners: Set }
    log: [],
  }
}
reset()

function send(res, status, body, headers) {
  const data = typeof body === 'string' ? body : JSON.stringify(body)
  res.writeHead(status, Object.assign({ 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-cache' }, headers || {}))
  res.end(data)
}

function readBody(req) {
  return new Promise((resolve) => {
    let raw = ''
    req.on('data', (c) => { raw += c })
    req.on('end', () => {
      try { resolve(raw ? JSON.parse(raw) : {}) } catch (_) { resolve({ raw }) }
    })
  })
}

function issueToken(username) {
  state.seq += 1
  const token = `mock-token-${state.seq}`
  state.tokens[token] = { username, status: 'ok' }
  return token
}

function auth(req, url) {
  const header = req.headers.authorization || ''
  const token = header.startsWith('Bearer ') ? header.slice(7) : url.searchParams.get('token')
  const entry = token && state.tokens[token]
  if (!entry || entry.status === 'expired') return { error: '登录已过期或未登录，请重新登录' }
  if (entry.status === 'kicked') return { error: '您的账号在其他设备登录，您已被迫下线，请重新登录' }
  return { user: entry.username }
}

function sseWrite(res, event) {
  res.write(`data: ${JSON.stringify(event)}\n\n`)
}

function startStream(sessionId, message) {
  const slow = /慢|slow/i.test(message)
  const stream = { events: [], done: false, listeners: new Set() }
  state.streams[sessionId] = stream
  const reply = slow
    ? ['这是一个', '较慢的', '流式回复，', '离开页面', '再回来', '应当能', '继续看到', '后续内容。', '**完成**']
    : ['你好，', '这是 mock 后端的', '**流式**回复。\n\n', '- 列表项\n', '\n```js\nconsole.log(1)\n```\n', '\n公式：\n\n$$E=mc^2$$\n']
  const plan = [{ type: 'start', session_id: sessionId }, { type: 'thinking_start', step: 1 }, { type: 'thinking', content: '思考中……', step: 1 }, { type: 'thinking_end', duration: 0.2, step: 1 }]
  for (const part of reply) plan.push({ type: 'content', content: part, step: 1 })
  plan.push({ type: 'token_usage', input_tokens: 12, output_tokens: 34, reasoning_tokens: 0, context_tokens: 46 })
  plan.push({ type: 'done', session_id: sessionId, message_id: `m-${Date.now()}`, finish_reason: 'stop' })
  const delay = slow ? 900 : 60
  let i = 0
  const tick = () => {
    if (i >= plan.length) {
      stream.done = true
      const text = reply.join('')
      // /__mock/reset 之后旧流才结束：会话已不存在，丢弃即可
      if (state.histories[sessionId]) state.histories[sessionId].push({ id: `a-${Date.now()}`, role: 'assistant', content: text, blocks: [{ type: 'content', content: text, order: 1, step: 1 }], created_at: new Date().toISOString() })
      for (const res of stream.listeners) res.end()
      stream.listeners.clear()
      return
    }
    const event = plan[i]
    i += 1
    stream.events.push(event)
    for (const res of stream.listeners) sseWrite(res, event)
    setTimeout(tick, delay)
  }
  setTimeout(tick, delay)
  return stream
}

function attach(res, stream, replay) {
  res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive' })
  if (replay) for (const event of stream.events) sseWrite(res, event)
  if (stream.done) return res.end()
  stream.listeners.add(res)
  res.on('close', () => stream.listeners.delete(res))
  return undefined
}

async function api(req, res, url) {
  const p = url.pathname
  const method = req.method
  const entry = {
    at: new Date().toISOString(), method, path: p + url.search,
    authorization: req.headers.authorization || '',
    hostToken: /HOST-USER-TOKEN/.test(req.headers.authorization || ''),
    hostPrefix: p.startsWith('/host-gateway'),
  }
  if (!p.startsWith('/__mock/')) state.log.push(entry)

  if (p === '/__mock/log') return send(res, 200, state.log)
  if (p === '/__mock/reset') { reset(); return send(res, 200, { ok: true }) }
  if (p === '/__mock/expire' || p === '/__mock/kick') {
    for (const t of Object.keys(state.tokens)) state.tokens[t].status = p.endsWith('kick') ? 'kicked' : 'expired'
    return send(res, 200, { ok: true })
  }

  if (p === '/agent/auth/login-passwordless' || p === '/agent/auth/login') {
    const body = await readBody(req)
    const username = body.username || 'anonymous'
    return send(res, 200, { access_token: issueToken(username), username, context_length: 131072 })
  }
  if (p === '/api/personnel/login-policy') return send(res, 200, { register_enabled: true })

  const who = auth(req, url)
  if (who.error) {
    entry.status = 401
    return send(res, 401, { detail: who.error })
  }
  const user = who.user

  if (p === '/agent/auth/profile') return send(res, 200, { username: user, organization_id: 'mock-org', email: `${user}@example.com` })
  if (p === '/agent/auth/config') return send(res, 200, { context_length: 131072, preset_questions: ['介绍一下你自己'], app_welcome_title: 'Mock 后端欢迎语' })
  if (p === '/agent/auth/logout') return send(res, 200, { ok: true })
  if (p === '/agent/settings/models') return send(res, 200, { models: [{ name: 'mock-model', display_name: 'Mock 模型' }], active_model: 'mock-model' })
  if (p === '/api/knowledge/v1/status') return send(res, 200, { enabled: true })
  if (p === '/api/knowledge/v1/capabilities') return send(res, 200, { upload: true })
  if (p.startsWith('/api/knowledge/v1/bases') && method === 'GET') return send(res, 200, { items: [], total: 0 })
  if (p.startsWith('/api/knowledge/v1/operations')) return send(res, 200, { items: [], total: 0 })

  if (p === '/agent/sessions' && method === 'GET') return send(res, 200, state.sessions)
  if (p === '/agent/sessions' && method === 'POST') {
    const body = await readBody(req)
    const session = { session_id: `s-${Date.now()}-${state.sessions.length}`, title: body.title || '新会话', created_at: new Date().toISOString(), updated_at: new Date().toISOString() }
    state.sessions.unshift(session)
    state.histories[session.session_id] = []
    return send(res, 200, session)
  }
  let m = /^\/agent\/sessions\/([^/]+)$/.exec(p)
  if (m && method === 'GET') return send(res, 200, { messages: state.histories[m[1]] || [], todos: [], usage: null })
  if (m && method === 'DELETE') {
    state.sessions = state.sessions.filter((s) => s.session_id !== m[1])
    return send(res, 200, { ok: true })
  }

  if (p === '/agent/chat/stream' && method === 'POST') {
    const body = await readBody(req)
    let sid = body.session_id
    // 与真实后端一致：新对话首条消息不带 session_id，由后端建会话并在 start 事件里返回
    if (!sid) {
      sid = `s-${Date.now()}-${state.sessions.length}`
      state.sessions.unshift({ session_id: sid, title: String(body.message || '新会话').slice(0, 20), created_at: new Date().toISOString(), updated_at: new Date().toISOString() })
    }
    if (!state.histories[sid]) state.histories[sid] = []
    state.histories[sid].push({ id: `u-${Date.now()}`, role: 'user', content: body.message, created_at: new Date().toISOString() })
    return attach(res, startStream(sid, body.message || ''), true)
  }
  if (p === '/agent/chat/stream/status') {
    const s = state.streams[url.searchParams.get('session_id')]
    return send(res, 200, { active: !!(s && !s.done) })
  }
  if (p === '/agent/chat/stream/live') {
    const s = state.streams[url.searchParams.get('session_id')]
    if (!s) return send(res, 404, { detail: '没有进行中的流' })
    return attach(res, s, true)
  }
  if (p === '/agent/chat/cancel') return send(res, 200, { ok: true })

  if (/\/files$/.test(p) || p.startsWith('/agent/files/session/')) return send(res, 200, [])
  if (p === '/agent/files/workspace/tree') return send(res, 200, { tree: [], items: [] })
  if (p === '/agent/connectors') return send(res, 200, { connectors: [] })
  if (p === '/agent/connectors/session') return send(res, 200, { session_id: url.searchParams.get('session_id'), connectors: null })
  if (p === '/agent/connectors/status') return send(res, 200, { statuses: [] })
  if (p === '/agent/connectors/mcp/raw') return send(res, 200, { path: 'mcp.json', content: '{\n  "mcpServers": {}\n}\n' })
  if (p.startsWith('/agent/skill-center/')) return send(res, 200, [])
  if (p === '/agent/list' || p === '/agent/public-list' || p === '/agent/session-experts') return send(res, 200, [])
  if (p.startsWith('/agent/scheduled-tasks')) return send(res, 200, [])

  entry.unhandled = true
  return send(res, 200, {})
}

function serveStatic(req, res, url) {
  let rel = decodeURIComponent(url.pathname.slice(BASE.length))
  let file = path.join(DIST, rel)
  if (!file.startsWith(DIST) || !rel || !fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(DIST, 'index.html')
  const type = MIME[path.extname(file)] || 'application/octet-stream'
  res.writeHead(200, { 'Content-Type': type })
  fs.createReadStream(file).pipe(res)
}

http.createServer((req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`)
  if (isApi(url.pathname)) return api(req, res, url).catch((error) => send(res, 500, { detail: String(error && error.message) }))
  if (url.pathname.startsWith(BASE)) return serveStatic(req, res, url)
  res.writeHead(302, { Location: BASE })
  return res.end()
}).listen(PORT, () => {
  console.log(`[mock] http://localhost:${PORT}${BASE}  (dist: ${DIST})`)
})
