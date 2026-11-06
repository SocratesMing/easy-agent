// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/promptEnhancement/controller.test.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
import test from 'node:test'
import assert from 'node:assert/strict'
import { createEnhancementController, enhancementError } from './controller.js'

function harness() {
  let text = '原始\n    代码'
  let scope = 'main'
  let state
  let controller
  const requests = []
  const read = () => ({ text, body: text, scope, blocked: false, restore: body => body })
  controller = createEnhancementController({
    read, write: value => { text = value; controller.invalidate() }, publish: value => { state = value },
    request: (body, signal) => new Promise((resolve, reject) => requests.push({ body, signal, resolve, reject })),
  })
  return { controller, requests, get text() { return text }, get state() { return state },
    edit(value) { text = value; controller.invalidate() },
    scope(value) { scope = value; controller.invalidate() },
    silentScope(value) { scope = value },
  }
}

test('successful replacement preserves multiline input without a success notice', async () => {
  const h = harness()
  const pending = h.controller.enhance()
  assert.equal(h.requests[0].body, '原始\n    代码')
  assert.equal(h.state.pending, true)
  h.requests[0].resolve({ enhanced_prompt: '增强\n    内容' })
  await pending
  assert.equal(h.text, '增强\n    内容')
  assert.deepEqual(h.state, { pending: false, notice: '' })
})

test('edit then revert in one tick discards old success and error', async () => {
  for (const fail of [false, true]) {
    const h = harness()
    const pending = h.controller.enhance()
    h.edit('新文本'); h.edit('原始\n    代码')
    assert.equal(h.requests[0].signal.aborted, true)
    if (fail) h.requests[0].reject(new Error('failure'))
    else h.requests[0].resolve({ enhanced_prompt: '过期' })
    await pending
    assert.equal(h.text, '原始\n    代码')
    assert.deepEqual(h.state, { pending: false, notice: '' })
  }
})

test('stale callback cannot finish a newer request or overwrite its draft', async () => {
  const h = harness()
  const first = h.controller.enhance()
  h.edit('第二稿')
  const second = h.controller.enhance()
  h.requests[0].resolve({ enhanced_prompt: '旧结果' })
  await first
  assert.equal(h.state.pending, true)
  h.requests[1].resolve({ enhanced_prompt: '新结果' })
  await second
  assert.equal(h.text, '新结果')
})

test('selection/scope/dispose invalidates request; result also checks scope', async () => {
  for (const invalidate of [h => h.scope('sidebar'), h => h.controller.dispose(), h => h.silentScope('account-b')]) {
    const h = harness()
    const pending = h.controller.enhance()
    invalidate(h)
    h.requests[0].resolve({ enhanced_prompt: '过期' })
    await pending
    assert.equal(h.text, '原始\n    代码')
    assert.equal(h.state.notice, '')
  }
  const h = harness()
  const pending = h.controller.enhance()
  h.requests[0].resolve({ enhanced_prompt: '增强' }); await pending
  h.edit('继续编辑')
  assert.equal(h.text, '继续编辑')
})

test('instances own pending state; duplicate click sends only one call', async () => {
  const first = harness(), second = harness()
  const a = first.controller.enhance(), b = second.controller.enhance()
  await first.controller.enhance()
  assert.equal(first.requests.length, 1)
  first.controller.dispose()
  first.requests[0].resolve({ enhanced_prompt: '旧' })
  second.requests[0].resolve({ enhanced_prompt: '独立结果' })
  await Promise.all([a, b])
  assert.equal(second.text, '独立结果')
})

test('failures keep draft and render safe status messages, including 422 arrays', async () => {
  const h = harness()
  const pending = h.controller.enhance()
  h.requests[0].reject({ response: { status: 422, data: { detail: [{ msg: 'raw' }] } } })
  await pending
  assert.equal(h.text, '原始\n    代码')
  assert.equal(h.state.notice, '请输入有效提示词，或缩短输入内容')
  assert.match(enhancementError({ code: 'ECONNABORTED' }), /超时/)
  assert.match(enhancementError({ status: 503 }), /管理员/)
})
