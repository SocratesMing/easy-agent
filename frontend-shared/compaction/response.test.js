import test from 'node:test'
import assert from 'node:assert/strict'
import { readContextResponse } from './response.js'
import { backendErrorMessage } from '../utils/apiError.js'

const record = { id: 'request', status: 'compacted', message: '已压缩', anchor_message_count: 2,
  after_tokens: 150, context_length: 1000 }
const status = { session_id: 'session', supported: true, busy: false, reason: '', compactions: [record] }
const result = { session_id: 'session', status: 'compacted', compaction: record }

test('valid status and terminal results preserve estimates and request identity', () => {
  assert.deepEqual(readContextResponse(status, 200, 'session'), status)
  assert.deepEqual(readContextResponse(result, 200, 'session', 'request'), result)
  const skipped = { ...result, status: 'skipped', compaction: { ...record, status: 'skipped' } }
  assert.deepEqual(readContextResponse(skipped, 200, 'session', 'request'), skipped)
})

test('HTML, missing fields, incomplete records and mismatched identities are not authoritative', () => {
  for (const value of [null, '<html>更新中</html>', {}, [], { ...status, session_id: 'other' },
    { ...status, busy: undefined }, { ...status, compactions: undefined },
    { ...status, compactions: [{}] }, { ...status, compactions: [{ ...record, status: 'running' }] }]) {
    assert.throws(() => readContextResponse(value, 200, 'session'), { code: 'INVALID_CONTEXT_RESPONSE', status: 200 })
  }
  for (const value of [null, '<html>更新中</html>', {}, { ...result, compaction: undefined },
    { ...result, compaction: { ...record, id: 'other' } }, { ...result, status: 'skipped' },
    { ...result, compaction: { ...record, after_tokens: undefined } }]) {
    assert.throws(() => readContextResponse(value, 200, 'session', 'request'), { code: 'INVALID_CONTEXT_RESPONSE' })
  }
})

test('only structured business errors are shown and HTTP status survives decoding failures', () => {
  for (const code of [409, 422, 502, 503, 504]) {
    assert.throws(() => readContextResponse({ detail: '会话正在等待审批' }, code, 'session'),
      { message: '会话正在等待审批', status: code })
    assert.throws(() => readContextResponse('<html>更新中</html>', code, 'session'),
      { code: 'INVALID_CONTEXT_RESPONSE', status: code })
  }
  const fallback = '请求失败'
  for (const payload of ['upstream failed', '<html>更新中</html>', { detail: '<html>更新中</html>' },
    { detail: 'x'.repeat(501) }, { detail: [{ msg: 'invalid' }] }]) {
    assert.equal(backendErrorMessage(payload, fallback), fallback)
  }
  assert.equal(backendErrorMessage({ detail: { message: '所选模型不可用' } }, fallback), '所选模型不可用')
  const dirty = { ...status, compactions: [{ ...record, message: '<html>更新中</html>' }] }
  assert.equal(readContextResponse(dirty, 200, 'session').compactions[0].message, '压缩结果已更新')
})
