import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

// Node has no Vite import.meta.env. Stub only the build-time config import;
// exercise the real authFetch and enhancement transport unchanged.
const configImport = /import \{ API_BASE_URL \} from ['"]\.\.\/config\.js['"]/g
const authSource = (await readFile(new URL('./auth.js', import.meta.url), 'utf8')).replace(configImport, "const API_BASE_URL = 'https://example.invalid'")
  .replace("'../shared/sessionManagement/state.js'", JSON.stringify(new URL('../shared/sessionManagement/state.js', import.meta.url).href))
const authURL = 'data:text/javascript;base64,' + Buffer.from(authSource).toString('base64')
const { storeAuth } = await import(authURL)
const source = (await readFile(new URL('./promptEnhancement.js', import.meta.url), 'utf8'))
  .replace(configImport, "const API_BASE_URL = 'https://example.invalid'")
  .replace("'./auth.js'", JSON.stringify(authURL))
const { enhancePrompt } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'))

const values = new Map()
globalThis.localStorage = { getItem: key => values.get(key) || null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) }
globalThis.window = new EventTarget()
if (!globalThis.CustomEvent) globalThis.CustomEvent = class extends Event { constructor(type) { super(type) } }
storeAuth('token', 'alice')

test('Vue3 transport sends only prompt with existing auth and forwards cancellation', async () => {
  const original = globalThis.fetch
  try {
    globalThis.fetch = async (url, options) => {
      assert.ok(url.endsWith('/agent/prompts/enhance'))
      assert.equal(options.headers.Authorization, 'Bearer token')
      assert.deepEqual(JSON.parse(options.body), { prompt: '原文\n    代码' })
      return new Response(JSON.stringify({ enhanced_prompt: '增强' }), { status: 200 })
    }
    assert.deepEqual(await enhancePrompt('原文\n    代码'), { enhanced_prompt: '增强' })
    const controller = new AbortController()
    globalThis.fetch = (url, options) => new Promise((resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
    })
    const pending = enhancePrompt('原文', controller.signal)
    controller.abort()
    await assert.rejects(pending, { name: 'AbortError' })
  } finally { globalThis.fetch = original }
})

test('Vue3 status errors and timeout remain distinguishable; upstream 503 preserves login', async () => {
  const originalFetch = globalThis.fetch, originalTimer = globalThis.setTimeout
  try {
    globalThis.fetch = async () => new Response(JSON.stringify({ detail: [{ msg: 'validation' }] }), { status: 422 })
    await assert.rejects(enhancePrompt('原文'), { status: 422 })
    globalThis.fetch = async () => new Response('{}', { status: 503 })
    await assert.rejects(enhancePrompt('原文'), { status: 503 })
    assert.equal(values.get('mini_agent_token'), 'token')
    let timeout
    globalThis.setTimeout = (fn, ms) => { assert.equal(ms, 35000); timeout = fn; return 0 }
    globalThis.fetch = (url, options) => new Promise((resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
    })
    const pending = enhancePrompt('原文')
    timeout()
    await assert.rejects(pending, { name: 'TimeoutError' })
  } finally { globalThis.fetch = originalFetch; globalThis.setTimeout = originalTimer }
})
