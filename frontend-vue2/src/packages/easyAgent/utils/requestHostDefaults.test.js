/**
 * 宿主在模块加载之前就改了全局 axios.defaults（Authorization / baseURL / withCredentials）：
 * axios.create() 会把当时的 defaults 拷进实例，请求层必须显式覆盖，不能把宿主凭证或前缀带给 Agent 后端。
 * 单独一个文件：必须在 import request.js 之前设置 defaults。
 */
import assert from 'node:assert/strict'
import http from 'node:http'
import test from 'node:test'
import axios from 'axios'

const store = new Map()
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
}

axios.defaults.headers.common.Authorization = 'Bearer HOST-USER-TOKEN'
axios.defaults.baseURL = 'http://127.0.0.1:1/host-gateway'
axios.defaults.withCredentials = true

const { configureEasyAgent } = await import('../runtime/config.js')
const { default: request } = await import('./request.js')

const seen = []
const server = http.createServer((req, res) => {
  seen.push({ path: req.url, authorization: req.headers.authorization })
  res.writeHead(200, { 'Content-Type': 'application/json' })
  res.end('{"ok":true}')
})
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
const origin = `http://127.0.0.1:${server.address().port}`
test.after(() => server.close())

test('无 Agent token 时不带宿主 Authorization，也不走宿主 baseURL', async () => {
  configureEasyAgent({ api: { baseURL: origin } })
  assert.deepEqual(await request({ url: '/agent/sessions' }), { ok: true })
  assert.equal(seen[0].path, '/agent/sessions')
  assert.equal(seen[0].authorization, undefined)
})

test('有 Agent token 时只带 Agent token', async () => {
  store.set('mini_agent_token', 'agent-token')
  await request({ url: '/agent/sessions' })
  assert.equal(seen[1].authorization, 'Bearer agent-token')
})
