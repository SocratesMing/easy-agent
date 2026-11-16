import assert from 'node:assert/strict'
import http from 'node:http'
import test from 'node:test'
import { getTokenUsage } from './usage.js'
import { configureEasyAgent, resetEasyAgentConfig } from '../runtime/config.js'
import { onRuntimeEvent } from '../runtime/events.js'

const store = new Map()
globalThis.localStorage = {
  getItem: key => store.get(key) || null,
  setItem: (key, value) => store.set(key, value),
  removeItem: key => store.delete(key),
}
let failure = false, slow = false
const seen = []
const server = http.createServer((request, response) => {
  seen.push({ url: request.url, headers: request.headers })
  const send = () => {
    response.writeHead(failure ? 503 : 200, { 'Content-Type': 'application/json' })
    response.end(JSON.stringify(failure ? { detail: 'Token统计暂不可用，请重试' } : { days: Number(new URL(request.url, 'http://local').searchParams.get('days')) }))
  }
  if (slow) setTimeout(send, 100)
  else send()
})
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
test.after(() => { server.closeAllConnections(); server.close(); resetEasyAgentConfig() })

test('Vue2 usage reads late host URL, credential keys and adapter headers at runtime', async () => {
  // Importing the API above must not freeze the standalone runtime settings.
  configureEasyAgent({ mode: 'embedded', api: {
    resolveUrl: path => `http://127.0.0.1:${server.address().port}/host-gateway${path}`,
    withCredentials: true,
    headers: () => ({ 'X-Gateway': 'host', Authorization: 'Bearer host-token' }),
  }, auth: { tokenKey: 'custom-agent-token', usernameKey: 'custom-agent-user' } })
  store.set('custom-agent-token', 'agent-token')
  store.set('custom-agent-user', 'Alice')
  let activity = 0
  const stop = onRuntimeEvent('user-activity', () => { activity += 1 })
  try {
    assert.deepEqual(await getTokenUsage(30), { days: 30 })
    assert.equal(seen[0].url, '/host-gateway/agent/usage?days=30')
    assert.equal(seen[0].headers.authorization, 'Bearer agent-token')
    assert.equal(seen[0].headers['x-gateway'], 'host')
    assert.equal(activity, 1)
    assert.throws(() => getTokenUsage(1), RangeError)
    assert.equal(seen.length, 1)
    failure = true
    await assert.rejects(getTokenUsage(7), { message: 'Token统计暂不可用，请重试' })
    assert.equal(store.get('custom-agent-token'), 'agent-token')
    failure = false
    slow = true
    const controller = new AbortController()
    const pending = getTokenUsage(7, controller.signal)
    controller.abort()
    await assert.rejects(pending, error => error.code === 'ERR_CANCELED')
  } finally { stop() }
})
