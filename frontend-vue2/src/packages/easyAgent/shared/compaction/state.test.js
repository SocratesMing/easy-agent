// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/compaction/state.test.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
import test from 'node:test'
import assert from 'node:assert/strict'
import { stateFor, mergeStatus, commandItems, recordsAt, estimatedUsage } from './state.js'

test('compact command filtering works without skills and exposes disabled reasons', () => {
  for (const query of ['', 'com', 'COM', '压缩']) assert.equal(commandItems(query, '', false)[0].command, true)
  assert.deepEqual(commandItems('unknown-skill', '', false), [])
  assert.equal(commandItems('', '请先处理审批', false)[0].disabled, true)
  assert.match(commandItems('', '忙碌', true)[0].name, /正在压缩/)
})

test('server records replace pending results while operations stay bound to their session', () => {
  const pending = { id: 'one', status: 'running' }
  stateFor('first').records.push(pending)
  stateFor('first').running = true
  const result = { ...pending, status: 'compacted' }
  mergeStatus('first', { compactions: [result], supported: true, busy: false, reason: '' })
  assert.deepEqual(stateFor('first').records, [result])
  assert.equal(stateFor('first').running, true)
  assert.deepEqual(stateFor('second').records, [])
})

test('stable turn anchors remain after the original reply when later turns arrive', () => {
  const record = { id: 'one', anchor_turn_id: 't1', anchor_message_count: 2 }
  const messages = [{ role: 'user', turn_id: 't1' }, { role: 'assistant' }, { role: 'assistant' }, { role: 'user', turn_id: 't2' }]
  assert.deepEqual(recordsAt([record], messages, 2), [record])
  assert.deepEqual(recordsAt([record], messages, 3), [])
  assert.deepEqual(recordsAt([{ ...record, anchor_turn_id: 'legacy' }], messages, 1).length, 1)
})

test('occupancy estimate preserves provider usage and yields to the next real turn', () => {
  const usage = { input_tokens: 900, output_tokens: 50, context_tokens: 900 }
  const records = [{ status: 'compacted', after_tokens: 100, context_length: 1000, anchor_message_count: 2 }]
  const estimate = estimatedUsage(records, [{}, {}], usage, false)
  assert.equal(estimate.context_tokens, 100)
  assert.equal(estimate.input_tokens, 900)
  assert.equal(estimate.output_tokens, 50)
  assert.equal(estimate.context_estimated, true)
  assert.equal(estimatedUsage(records, [{}, {}], usage, true).context_tokens, 100)
  assert.equal(estimatedUsage(records, [{}, {}, {}], usage, false), usage)
})

test('skipped compaction refreshes the estimate instead of restoring pre-compaction usage', () => {
  const usage = { input_tokens: 49069, context_tokens: 49069 }
  const records = [
    { status: 'compacted', after_tokens: 11833, anchor_message_count: 4 },
    { status: 'skipped', after_tokens: 11900, context_length: 65536, anchor_message_count: 6 },
  ]
  const estimate = estimatedUsage(records, Array(6).fill({}), usage, false)
  assert.equal(estimate.context_tokens, 11900)
  assert.equal(estimate.context_estimated, true)
  assert.equal(estimate.input_tokens, 49069)
})

test('a local reply with zero usage preserves compaction while a model reply restores reported usage', () => {
  const usage = { input_tokens: 49069, output_tokens: 83, context_tokens: 49069 }
  const records = [{ status: 'compacted', after_tokens: 11833, context_length: 65536, anchor_message_count: 4 }]
  const messages = [...Array(4).fill({}),
    { role: 'user', content: '你好' },
    { role: 'assistant', content: '请提出相关问题', usage: { input_tokens: 0, output_tokens: 0, context_tokens: 0 } },
  ]
  const estimate = estimatedUsage(records, messages, usage, false)
  assert.equal(estimate.context_tokens, 11850)
  assert.equal(estimate.context_estimated, true)
  assert.equal(estimate.input_tokens, 49069)
  messages[5].usage = { input_tokens: 13000, output_tokens: 20, context_tokens: 13000 }
  assert.equal(estimatedUsage(records, messages, usage, false), usage)
})

test('waiting for the first usage keeps the compressed estimate until this reply reports tokens', () => {
  const usage = { input_tokens: 49069, output_tokens: 83, context_tokens: 49069 }
  const records = [{ status: 'compacted', after_tokens: 11833, context_length: 65536, anchor_message_count: 4 }]
  const messages = [...Array(4).fill({}),
    { role: 'user', content: '霓虹' },
    { role: 'assistant', content: '', loading: true },
  ]
  const waiting = estimatedUsage(records, messages, usage, true)
  assert.equal(waiting.context_tokens, 11843)
  assert.equal(waiting.context_estimated, true)
  assert.equal(waiting.input_tokens, 49069)
  // 流式内容先到，用量尚未到达时也不能恢复压缩前占用。
  messages[5].content = '正在分析'
  assert.ok(estimatedUsage(records, messages, usage, true).context_tokens < 12000)
  messages[5].usage = { input_tokens: 12500, output_tokens: 4, context_tokens: 12500 }
  const current = { ...usage, context_tokens: 12500 }
  assert.equal(estimatedUsage(records, messages, current, true), current)
  messages[5].loading = false
  assert.equal(estimatedUsage(records, messages, current, false), current)
})
