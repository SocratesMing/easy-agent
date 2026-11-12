import assert from 'node:assert/strict'
import test from 'node:test'
import { readFile } from 'node:fs/promises'

// Keep the real authFetch; stub Vite's build-time config for Node's test runner.
const configImport = /import \{ API_BASE_URL \} from ['"]\.\.\/config\.js['"]/g
const authSource = (await readFile(new URL('./auth.js', import.meta.url), 'utf8'))
  .replace(configImport, "const API_BASE_URL = ''")
  .replace("'../shared/sessionManagement/state.js'", JSON.stringify(new URL('../shared/sessionManagement/state.js', import.meta.url).href))
const authURL = 'data:text/javascript;base64,' + Buffer.from(authSource).toString('base64')
const { storeAuth, getStoredToken } = await import(authURL)
const source = (await readFile(new URL('./usage.js', import.meta.url), 'utf8'))
  .replace(configImport, "const API_BASE_URL = ''")
  .replace("'./auth.js'", JSON.stringify(authURL))
  .replace("'../shared/utils/tokenUsage.js'", JSON.stringify(new URL('../shared/utils/tokenUsage.js', import.meta.url).href))
const { getTokenUsage } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'))
const store = new Map()
globalThis.localStorage = { getItem: key => store.get(key) || null, setItem: (key, value) => store.set(key, value), removeItem: key => store.delete(key) }
globalThis.window = new EventTarget()
if (!globalThis.CustomEvent) globalThis.CustomEvent = class extends Event { constructor(type) { super(type) } }

test('Vue3 usage sends auth, only the allowed days, no-store and cancellation', async () => {
  const originalFetch = globalThis.fetch
  try {
    storeAuth('token', 'Alice')
    const controller = new AbortController()
    globalThis.fetch = async (url, options) => {
      assert.equal(url, '/agent/usage?days=30')
      assert.equal(options.headers.Authorization, 'Bearer token')
      assert.equal(options.cache, 'no-store')
      assert.equal(options.signal, controller.signal)
      return new Response('{"days":30}')
    }
    assert.deepEqual(await getTokenUsage(30, controller.signal), { days: 30 })
    await assert.rejects(getTokenUsage('30'), RangeError)
    globalThis.fetch = (url, options) => new Promise((resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
    })
    const pending = getTokenUsage(7, controller.signal)
    controller.abort()
    await assert.rejects(pending, { name: 'AbortError' })
  } finally { globalThis.fetch = originalFetch }
})

test('Vue3 reports upstream errors, preserves login on 503 and uses authFetch expiry on 401', async () => {
  const originalFetch = globalThis.fetch
  try {
    storeAuth('token', 'Alice')
    globalThis.fetch = async () => new Response('{"detail":"Token统计暂不可用，请重试"}', { status: 503 })
    await assert.rejects(getTokenUsage(), { message: 'Token统计暂不可用，请重试', status: 503 })
    assert.equal(getStoredToken(), 'token')
    globalThis.fetch = async () => new Response('{}', { status: 401 })
    await assert.rejects(getTokenUsage(), { status: 401 })
    assert.equal(getStoredToken(), null)
  } finally { globalThis.fetch = originalFetch }
})
