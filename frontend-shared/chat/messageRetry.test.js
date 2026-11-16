import assert from 'node:assert/strict'
import test from 'node:test'

import { buildUserRetryRequest, canRetryUserMessage } from './messageRetry.js'

test('display-only plan confirmations cannot be retried as new user turns', () => {
  const message = {
    role: 'user',
    content: '1. 项目 A：快速版本',
    display_only: true,
    message_kind: 'plan_confirmation',
    interaction_mode: 'plan',
  }

  assert.equal(canRetryUserMessage(message), false)
  assert.equal(buildUserRetryRequest(message), null)
})

test('retry keeps the interaction mode of the original user turn', () => {
  const request = buildUserRetryRequest({
    role: 'user',
    content: '请先规划实施方案',
    interaction_mode: 'plan',
  })

  assert.deepEqual(request, {
    content: '请先规划实施方案',
    interactionMode: 'plan',
  })
  assert.equal(canRetryUserMessage({ role: 'user', content: '普通消息' }), true)
})
