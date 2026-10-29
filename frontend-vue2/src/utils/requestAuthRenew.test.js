import assert from 'node:assert/strict'
import test from 'node:test'

const store = new Map()
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
}
globalThis.window = { dispatchEvent: () => {} }
globalThis.CustomEvent = class { constructor(type) { this.type = type } }

const { default: request, configureRequest, fetchWithAuth } = await import('./request.js')

const TOKEN_KEY = 'mini_agent_token'

/** axios 自定义 adapter：按 token 决定 200 / 401，并记录每次请求用的 token */
function tokenAdapter(validToken, seen) {
  return (config) => {
    const auth = config.headers.Authorization || config.headers.get?.('Authorization') || ''
    seen.push(auth)
    if (auth === `Bearer ${validToken}`) {
      return Promise.resolve({ data: { ok: true }, status: 200, statusText: 'OK', headers: {}, config })
    }
    return Promise.reject(Object.assign(new Error('401'), { config, response: { status: 401, data: { detail: 'token 已失效' } } }))
  }
}

function setup({ refreshAuth, onAuthExpired = () => {} }) {
  store.clear()
  store.set(TOKEN_KEY, 'old')
  configureRequest({ refreshAuth, onAuthExpired })
}

test('401 后换新 token 并把原请求重发一次', async () => {
  let refreshCalls = 0
  setup({ refreshAuth: async () => { refreshCalls++; store.set(TOKEN_KEY, 'new'); return true } })
  const seen = []
  const data = await request({ url: '/agent/sessions', adapter: tokenAdapter('new', seen) })
  assert.deepEqual(data, { ok: true })
  assert.equal(refreshCalls, 1)
  assert.deepEqual(seen, ['Bearer old', 'Bearer new'])
})

test('token 已被别的请求换过时直接重发，不再换取', async () => {
  let refreshCalls = 0
  setup({ refreshAuth: async () => { refreshCalls++; return true } })
  const seen = []
  const adapter = (config) => {
    const res = tokenAdapter('new', seen)(config)
    store.set(TOKEN_KEY, 'new')
    return res
  }
  assert.deepEqual(await request({ url: '/agent/sessions', adapter }), { ok: true })
  assert.equal(refreshCalls, 0)
})

test('换取失败时请求失败，且不再派发失效事件（refreshAuth 已通知宿主）', async () => {
  let expired = 0
  setup({ refreshAuth: async () => false, onAuthExpired: () => { expired++ } })
  await assert.rejects(request({ url: '/agent/sessions', adapter: tokenAdapter('new', []) }))
  assert.equal(expired, 0)
})

test('登录类接口 401 不续期，走原失效流程', async () => {
  let refreshCalls = 0
  let expired = 0
  setup({ refreshAuth: async () => { refreshCalls++; return true }, onAuthExpired: () => { expired++ } })
  await assert.rejects(request({ url: '/agent/auth/me', adapter: tokenAdapter('new', []) }))
  assert.equal(refreshCalls, 0)
  assert.equal(expired, 1)
  assert.equal(store.get(TOKEN_KEY), undefined)
})

test('未配置 refreshAuth（主站）时保持原行为：清理登录态并派发失效事件', async () => {
  let expired = 0
  setup({ refreshAuth: null, onAuthExpired: () => { expired++ } })
  await assert.rejects(request({ url: '/agent/sessions', adapter: tokenAdapter('new', []) }))
  assert.equal(expired, 1)
  assert.equal(store.get(TOKEN_KEY), undefined)
})

test('fetchWithAuth：并发 401 只换取一次，各自用新 token 重发', async () => {
  let refreshCalls = 0
  let pending = null
  setup({
    refreshAuth: () => {
      if (!pending) {
        refreshCalls++
        pending = new Promise((r) => setTimeout(r, 10)).then(() => { store.set(TOKEN_KEY, 'new'); pending = null; return true })
      }
      return pending
    },
  })
  const seen = []
  globalThis.fetch = async (url, init) => {
    seen.push(init.headers.Authorization)
    const ok = init.headers.Authorization === 'Bearer new'
    return { status: ok ? 200 : 401, ok, clone: () => ({ json: async () => ({}) }) }
  }
  const [a, b] = await Promise.all([
    fetchWithAuth('/agent/chat/stream', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }),
    fetchWithAuth('/agent/chat/cancel', { method: 'POST' }),
  ])
  assert.equal(a.status, 200)
  assert.equal(b.status, 200)
  assert.equal(refreshCalls, 1)
  assert.deepEqual(seen.filter((h) => h === 'Bearer new').length, 2)
})
