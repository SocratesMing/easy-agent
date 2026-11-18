import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

// 替换 Vite 专用配置；实际 authFetch、响应解码和共享校验保持不变。
const configImport = /import \{ API_BASE_URL \} from ['"]\.\.\/config\.js['"]/g
const authSource = (await readFile(new URL('./auth.js', import.meta.url), 'utf8'))
  .replace(configImport, "const API_BASE_URL = 'https://example.invalid'")
  .replace("'../shared/sessionManagement/state.js'", JSON.stringify(new URL('../shared/sessionManagement/state.js', import.meta.url).href))
const authURL = 'data:text/javascript;base64,' + Buffer.from(authSource).toString('base64')
const source = (await readFile(new URL('./chat.js', import.meta.url), 'utf8'))
  .replace(configImport, "const API_BASE_URL = 'https://example.invalid'")
  .replace(/from '([^']+)'/g, (match, path) => `from '${path === './auth.js' ? authURL : new URL(path, import.meta.url).href}'`)
const { compactContext, getContextStatus } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'))
globalThis.localStorage = { getItem: () => 'token' }
globalThis.window = new EventTarget()
if (!globalThis.CustomEvent) globalThis.CustomEvent = class extends Event { constructor(type) { super(type) } }

test('real fetch decoding preserves status, rejects HTML and incomplete JSON, and accepts committed records', async () => {
  const originalFetch = globalThis.fetch
  try {
    for (const status of [200, 403, 502, 503, 504]) {
      globalThis.fetch = async () => new Response('<!DOCTYPE html><html>系统更新</html>', { status })
      await assert.rejects(compactContext('s', 'model', 'r'), { code: 'INVALID_CONTEXT_RESPONSE', status })
      await assert.rejects(getContextStatus('s'), { code: 'INVALID_CONTEXT_RESPONSE', status })
    }
    globalThis.fetch = async () => new Response('{}', { status: 200 })
    await assert.rejects(getContextStatus('s'), { code: 'INVALID_CONTEXT_RESPONSE' })
    await assert.rejects(compactContext('s', 'model', 'r'), { code: 'INVALID_CONTEXT_RESPONSE' })
    globalThis.fetch = async () => new Response(JSON.stringify({ detail: '会话正在等待审批，请先处理审批' }), { status: 409 })
    await assert.rejects(compactContext('s', 'model', 'r'), { status: 409, message: '会话正在等待审批，请先处理审批' })
    const record = { id: 'r', status: 'compacted', message: '已压缩', anchor_message_count: 2,
      after_tokens: 150, context_length: 1000 }
    globalThis.fetch = async (url, options) => {
      assert.equal(options.headers.Authorization, 'Bearer token')
      assert.deepEqual(JSON.parse(options.body), { model: 'model', request_id: 'r' })
      return new Response(JSON.stringify({ session_id: 's', status: 'compacted', compaction: record }))
    }
    assert.equal((await compactContext('s', 'model', 'r')).compaction.id, 'r')
    globalThis.fetch = async () => new Response(JSON.stringify({ session_id: 's', supported: true, busy: false, reason: '', compactions: [record] }))
    assert.equal((await getContextStatus('s')).compactions[0].after_tokens, 150)
  } finally { globalThis.fetch = originalFetch }
})
