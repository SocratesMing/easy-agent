import test from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import { configureEasyAgent } from '../runtime/config.js'
import request from '../utils/request.js'
import { compactContext, getContextStatus } from './chat.js'

globalThis.localStorage = { getItem: () => null, removeItem() {} }
let reply, seen
const server = http.createServer((req, res) => {
  let body = ''
  req.on('data', chunk => { body += chunk })
  req.on('end', () => {
    seen = { path: req.url, body: body ? JSON.parse(body) : null }
    res.writeHead(reply.status, { 'Content-Type': typeof reply.data === 'string' ? 'text/html' : 'application/json' })
    res.end(typeof reply.data === 'string' ? reply.data : JSON.stringify(reply.data))
  })
})
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
configureEasyAgent({ api: { baseURL: `http://127.0.0.1:${server.address().port}` } })
test.after(() => { server.closeAllConnections(); server.close() })

test('real Axios filters gateway bodies and validates both context endpoints', async () => {
  for (const status of [200, 403, 502, 503, 504]) {
    reply = { status, data: '<!DOCTYPE html><html>系统更新</html>' }
    await assert.rejects(compactContext('s', 'model', 'r'), { code: 'INVALID_CONTEXT_RESPONSE', status })
    assert.deepEqual(seen.body, { model: 'model', request_id: 'r' })
    await assert.rejects(getContextStatus('s'), { code: 'INVALID_CONTEXT_RESPONSE', status })
    assert.equal(seen.path, '/agent/sessions/s/context')
    if (status !== 200) await assert.rejects(request({ url: '/agent/test' }, '请求失败'), { message: '请求失败' })
  }
  reply = { status: 200, data: {} }
  await assert.rejects(getContextStatus('s'), { code: 'INVALID_CONTEXT_RESPONSE' })
  await assert.rejects(compactContext('s', 'model', 'r'), { code: 'INVALID_CONTEXT_RESPONSE' })
  reply = { status: 409, data: { detail: '会话正在等待审批，请先处理审批' } }
  await assert.rejects(compactContext('s', 'model', 'r'), { status: 409, message: reply.data.detail })
  await assert.rejects(request({ url: '/agent/test' }), { message: reply.data.detail })
  const record = { id: 'r', status: 'compacted', message: '已压缩', anchor_message_count: 2,
    after_tokens: 150, context_length: 1000 }
  reply = { status: 200, data: { session_id: 's', status: 'compacted', compaction: record } }
  assert.equal((await compactContext('s', 'model', 'r')).compaction.id, 'r')
  reply = { status: 200, data: { session_id: 's', supported: true, busy: false, reason: '', compactions: [record] } }
  assert.equal((await getContextStatus('s')).compactions[0].after_tokens, 150)
})
