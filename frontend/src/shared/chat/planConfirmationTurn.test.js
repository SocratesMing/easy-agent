// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/chat/planConfirmationTurn.test.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
import test from 'node:test'
import assert from 'node:assert/strict'

import { buildPlanContinuationTurn } from './planConfirmationTurn.js'

test('splits pending assistant into card, user confirmation, and continuation', () => {
  const messages = [{
    id: 'a1',
    role: 'assistant',
    loading: true,
    pending_approval: { thread_id: 's1-m1' },
    blocks: [{ type: 'content', content: '先确认' }],
    expert: { id: 'e1', name: '专家' },
  }]
  const turn = buildPlanContinuationTurn(messages, 0, {
    id: 'confirm-1',
    text: '1. 项目 A：快速版本',
  })

  assert.equal(turn.completed.loading, false)
  assert.equal(turn.completed.pending_approval, null)
  assert.deepEqual(turn.confirmationMessage, {
    id: 'confirm-1',
    role: 'user',
    content: '1. 项目 A：快速版本',
    created_at: turn.confirmationMessage.created_at,
    message_kind: 'plan_confirmation',
    display_only: true,
  })
  assert.equal(turn.continuation.role, 'assistant')
  assert.equal(turn.continuation.loading, true)
  assert.deepEqual(turn.replacement.map(item => item.role), [
    'assistant',
    'user',
    'assistant',
  ])
  assert.equal(turn.continuation.expert.id, 'e1')
})
