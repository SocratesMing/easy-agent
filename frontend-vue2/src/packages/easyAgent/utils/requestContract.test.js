/**
 * 请求层合同测试：用本机 HTTP 服务走真实的 axios（Node http 适配器）与原生 fetch，
 * 覆盖 JSON / 原始响应 / 文本 / 二进制 / SSE、地址前缀、鉴权头、401 续期与失效、取消。
 */
import assert from 'node:assert/strict'
import http from 'node:http'
import test from 'node:test'

const store = new Map()
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
}

const axios = (await import('axios')).default
const { configureEasyAgent, resetEasyAgentConfig } = await import('../runtime/config.js')
const { onRuntimeEvent } = await import('../runtime/events.js')
const req = await import('./request.js')
const { readSSE } = await import('../api/sse.js')
const { runSignIn } = await import('../api/auth.js')

// ── 合同 mock 服务 ────────────────────────────────────────────────
const seen = []
let validToken = 'good'
let kicked = false
const server = http.createServer((request, response) => {
  const url = new URL(request.url, 'http://x')
  let body = ''
  request.on('data', (c) => { body += c })
  request.on('end', () => {
    seen.push({ method: request.method, path: url.pathname + url.search, headers: request.headers, body })
    const auth = request.headers.authorization || ''
    const json = (status, data, headers = {}) => {
      response.writeHead(status, { 'Content-Type': 'application/json', ...headers })
      response.end(JSON.stringify(data))
    }
    if (url.pathname.endsWith('/agent/auth/login-passwordless')) {
      validToken = `tok-${seen.length}`
      return json(200, { access_token: validToken, username: JSON.parse(body).username, context_length: 4096 })
    }
    if (auth !== `Bearer ${validToken}`) {
      return json(401, { detail: kicked ? '您的账号在其他设备登录，您已被迫下线，请重新登录' : '登录已过期或未登录，请重新登录' })
    }
    if (url.pathname.endsWith('/agent/json')) return json(200, { data: { nested: true }, ok: 1 }, { 'X-Trace': 'abc' })
    if (url.pathname.endsWith('/agent/text')) {
      response.writeHead(200, { 'Content-Type': 'application/json' })
      return response.end('{"not":"parsed"}')
    }
    if (url.pathname.endsWith('/agent/bytes')) {
      response.writeHead(200, { 'Content-Type': 'application/octet-stream', 'Content-Disposition': "attachment; filename*=UTF-8''%E4%BA%BA%E5%91%98.xlsx" })
      return response.end(Buffer.from([1, 2, 3, 4]))
    }
    if (url.pathname.endsWith('/agent/upload')) {
      return json(200, { contentType: request.headers['content-type'], size: body.length })
    }
    if (url.pathname.endsWith('/agent/sse')) {
      response.writeHead(200, { 'Content-Type': 'text/event-stream' })
      response.write('data: {"type":"start"}\n\n')
      setTimeout(() => {
        response.write('data: {"type":"content","content":"hi"}\n\ndata: {"type":"done"}')
        response.end()
      }, 20)
      return undefined
    }
    if (url.pathname.endsWith('/agent/slow')) return setTimeout(() => json(200, { late: true }), 200)
    if (url.pathname.endsWith('/api/knowledge/v1/status')) return json(200, { enabled: true })
    if (url.pathname.endsWith('/agent/bad')) return json(422, { detail: { code: 'LIMIT', message: '超限', retryable: true } })
    return json(404, { detail: 'not found' })
  })
})
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
const origin = `http://127.0.0.1:${server.address().port}`
test.after(() => server.close())

function setup(options = {}) {
  store.clear()
  seen.length = 0
  kicked = false
  validToken = 'good'
  resetEasyAgentConfig()
  configureEasyAgent({ api: { baseURL: origin, ...(options.api || {}) }, auth: options.auth || {} })
  store.set('mini_agent_token', options.token === undefined ? 'good' : options.token)
}

test('request 返回业务数据本身（不猜 data 字段），requestRaw 返回 status / headers', async () => {
  setup()
  const data = await req.default({ url: '/agent/json' })
  assert.deepEqual(data, { data: { nested: true }, ok: 1 })
  const raw = await req.requestRaw({ url: '/agent/json' })
  assert.equal(raw.status, 200)
  assert.equal(raw.headers['x-trace'], 'abc')
  assert.deepEqual(raw.data, { data: { nested: true }, ok: 1 })
})

test('requestText 不做 JSON 解析，requestArrayBuffer 返回原始字节且可读 Content-Disposition', async () => {
  setup()
  assert.equal(await req.requestText({ url: '/agent/text' }), '{"not":"parsed"}')
  const bytes = await req.requestArrayBuffer({ url: '/agent/bytes' })
  assert.deepEqual([...new Uint8Array(bytes)], [1, 2, 3, 4])
  const raw = await req.requestRaw({ url: '/agent/bytes', responseType: 'arraybuffer' })
  const { filenameFromContentDisposition } = await import('../features/personnel/model.js')
  assert.equal(filenameFromContentDisposition(raw.headers['content-disposition']), '人员.xlsx')
})

test('地址前缀在调用时读取：import 之后才配置 baseURL 同样生效，绝对地址不加前缀', async () => {
  setup()
  configureEasyAgent({ api: { baseURL: `${origin}/` } })
  assert.equal(req.resolveUrl('/agent/x'), `${origin}/agent/x`)
  assert.equal(req.resolveUrl('https://other/agent/x'), 'https://other/agent/x')
  configureEasyAgent({ api: { resolveUrl: (p) => `${origin}${p.replace('/api/', '/agent/')}` } })
  assert.equal(req.resolveUrl('/api/knowledge/v1/status'), `${origin}/agent/knowledge/v1/status`)
})

test('不带宿主 axios.defaults 上的 Authorization / baseURL；附加头生效且不能覆盖鉴权', async () => {
  setup({ token: '', api: { headers: () => ({ 'X-Gateway': 'g1', Authorization: 'Bearer from-adapter' }) } })
  axios.defaults.headers.common.Authorization = 'Bearer HOST-USER-TOKEN'
  try {
    await assert.rejects(req.default({ url: '/agent/json' }))
    assert.equal(seen[0].headers.authorization, undefined)
    assert.equal(seen[0].headers['x-gateway'], 'g1')
    store.set('mini_agent_token', 'good')
    await req.default({ url: '/agent/json' })
    assert.equal(seen[1].headers.authorization, 'Bearer good')
  } finally {
    delete axios.defaults.headers.common.Authorization
  }
})

test('FormData 上传不手写 multipart boundary', async () => {
  setup()
  const form = new FormData()
  form.append('file', new Blob(['hello']), 'a.txt')
  const data = await req.default({ url: '/agent/upload', method: 'post', data: form })
  assert.match(data.contentType, /^multipart\/form-data; boundary=/)
})

test('并发 401 只续期一次，各自换新 token 重发；续期结果同时用于 axios 与 fetch', async () => {
  let refreshCalls = 0
  setup({
    token: 'old',
    auth: {
      refresh: async () => {
        refreshCalls += 1
        await new Promise((r) => setTimeout(r, 20))
        store.set('mini_agent_token', 'good')
        return true
      },
    },
  })
  const [a, b, c] = await Promise.all([
    req.default({ url: '/agent/json' }),
    req.default({ url: '/api/knowledge/v1/status' }),
    req.fetchWithAuth(`${origin}/agent/json`),
  ])
  assert.equal(a.ok, 1)
  assert.equal(b.enabled, true)
  assert.equal(c.status, 200)
  assert.equal(refreshCalls, 1)
})

test('续期后重发仍 401 时最多重试一次就失败，不无限重试', async () => {
  let refreshCalls = 0
  setup({ token: 'old', auth: { refresh: async () => { refreshCalls += 1; store.set('mini_agent_token', `still-bad-${refreshCalls}`); return true } } })
  await assert.rejects(req.default({ url: '/agent/json' }))
  assert.equal(refreshCalls, 1)
  assert.equal(seen.length, 2)
})

test('被其他设备顶下线：不续期，直接失效并记录 kicked', async () => {
  let refreshCalls = 0
  const events = []
  const off = onRuntimeEvent('auth-expired', (e) => events.push(e))
  setup({ token: 'old', auth: { refresh: async () => { refreshCalls += 1; return true }, onExpired: (e) => events.push({ ...e, from: 'adapter' }) } })
  kicked = true
  try {
    await assert.rejects(req.default({ url: '/agent/json' }))
    const res = await req.fetchWithAuth(`${origin}/agent/json`)
    assert.equal(res.status, 401)
    assert.equal(refreshCalls, 0)
    assert.equal(events[0].reason, 'kicked')
    assert.equal(events[1].from, 'adapter')
    assert.equal(store.get('auth_kicked'), '1')
    assert.equal(store.has('mini_agent_token'), false)
  } finally {
    off()
  }
})

test('登录类接口（含 /api/auth 旧前缀）401 不续期，避免续期等待自己', async () => {
  let refreshCalls = 0
  setup({ token: 'old', auth: { refresh: async () => { refreshCalls += 1; return true } } })
  await assert.rejects(req.default({ url: '/agent/auth/profile' }))
  store.set('mini_agent_token', 'old')
  await assert.rejects(req.default({ url: '/api/auth/profile' }))
  assert.equal(refreshCalls, 0)
})

test('续期期间请求被取消：不会在新 token 下继续发出', async () => {
  let release
  setup({ token: 'old', auth: { refresh: () => new Promise((r) => { release = () => { store.set('mini_agent_token', 'good'); r(true) } }) } })
  const controller = new AbortController()
  const pending = req.default({ url: '/agent/json', signal: controller.signal })
  await new Promise((r) => setTimeout(r, 30))
  controller.abort()
  release()
  await assert.rejects(pending, (e) => e.code === 'ERR_CANCELED' || e.name === 'CanceledError')
  assert.equal(seen.filter((s) => s.headers.authorization === 'Bearer good').length, 0)
})

test('fetchWithAuth 返回未读取的原生 Response，SSE 按事件逐条解析；取消后停止读取', async () => {
  setup()
  const response = await req.handleStreamResponse(await req.fetchWithAuth(req.resolveUrl('/agent/sse'), { headers: req.streamHeaders() }))
  assert.equal(response.bodyUsed, false)
  const events = []
  await readSSE(response, (e) => events.push(e.type))
  assert.deepEqual(events, ['start', 'content', 'done'])

  const controller = new AbortController()
  const pending = req.fetchWithAuth(req.resolveUrl('/agent/slow'), { signal: controller.signal })
  controller.abort()
  await assert.rejects(pending, (e) => e.name === 'AbortError')
})

test('handleStreamResponse 保留结构化错误：message / code / retryable / status', async () => {
  setup()
  await assert.rejects(
    req.handleStreamResponse(await req.fetchWithAuth(req.resolveUrl('/agent/bad'))),
    (e) => e.message === '超限' && e.code === 'LIMIT' && e.retryable === true && e.status === 422
  )
})

test('配置了 auth.signIn 时 401 续期即重新执行 signIn（并发合并），失败按失效通知', async () => {
  let signIns = 0
  setup({
    token: 'old',
    auth: {
      signIn: async ({ passwordlessLogin }) => {
        signIns += 1
        await passwordlessLogin('host_user', 'E1')
        return true
      },
    },
  })
  const [a, b] = await Promise.all([req.default({ url: '/agent/json' }), req.default({ url: '/agent/json' })])
  assert.equal(a.ok, 1)
  assert.equal(b.ok, 1)
  assert.equal(signIns, 1)
  assert.equal(store.get('mini_agent_username'), 'host_user')

  const events = []
  const off = onRuntimeEvent('auth-expired', (e) => events.push(e.reason))
  configureEasyAgent({ auth: { signIn: async () => false } })
  store.set('mini_agent_token', 'old')
  try {
    await assert.rejects(req.default({ url: '/agent/json' }))
    assert.deepEqual(events, ['expired'])
    assert.equal(store.has('mini_agent_token'), false)
  } finally {
    off()
  }
})

test('runSignIn：返回 profile 时透传；声称成功却没写 token 时报错', async () => {
  setup({ token: '' })
  configureEasyAgent({ auth: { signIn: async ({ setToken }) => { setToken('good', 'u1'); return { profile: { username: '张三' } } } } })
  const result = await runSignIn()
  assert.deepEqual(result.profile, { username: '张三' })
  assert.equal(store.get('mini_agent_token'), 'good')
  store.clear()
  configureEasyAgent({ auth: { signIn: async () => true } })
  await assert.rejects(runSignIn(), /没有写入 Agent token/)
})

test('notifyUnauthorized：能续期时后台续期，不能续期时直接失效', async () => {
  let refreshCalls = 0
  setup({ token: 'old', auth: { refresh: async () => { refreshCalls += 1; return true } } })
  req.notifyUnauthorized('/api/knowledge/v1/bases/x/documents', '登录已过期')
  await new Promise((r) => setTimeout(r, 0))
  assert.equal(refreshCalls, 1)
  configureEasyAgent({ auth: { refresh: null } })
  req.notifyUnauthorized('/api/knowledge/v1/bases/x/documents', '登录已过期')
  assert.equal(store.has('mini_agent_token'), false)
})

test('存储键按运行时配置读写（SDK / 宿主可与独立应用分开存放）', async () => {
  setup()
  configureEasyAgent({ auth: { tokenKey: 'host_agent_token', usernameKey: 'host_agent_user' } })
  req.storeAuth('t1', 'u1')
  assert.equal(store.get('host_agent_token'), 't1')
  assert.equal(req.getStoredUsername(), 'u1')
  req.clearAuth()
  assert.equal(store.has('host_agent_token'), false)
  assert.equal(store.get('mini_agent_token'), 'good')
})
