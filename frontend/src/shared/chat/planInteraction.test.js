// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/chat/planInteraction.test.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
import test from 'node:test'
import assert from 'node:assert/strict'

import {
  buildInteractionBatchResponses,
  buildInteractionBatches,
  buildInteractionResumeDecisions,
  buildConfirmationMessage,
  interactionCommandItems,
  missingRequiredQuestionIds,
  normalizeInteractionMode,
  normalizeInteractionRequest,
  parseInteractionResponse,
  pendingInteractionActions,
} from './planInteraction.js'

test('normalizes a multi-question interaction and drops malformed choices', () => {
  const request = normalizeInteractionRequest({
    title: '确认三个项目',
    description: '以下选择会影响后续实现。',
    questions: [
      {
        id: 'project-a',
        title: '项目 A 的方案',
        options: [
          { id: 'fast', label: '快速版本', recommended: true },
          { id: 'full', label: '完整版本', description: '覆盖更多边界' },
        ],
      },
      {
        id: 'project-b',
        title: '项目 B 的范围',
        multiple: true,
        options: [
          { id: 'web', label: 'Web' },
          { id: 'mobile', label: '移动端' },
          { id: 'broken' },
        ],
      },
      { id: 'ignored', title: '只有一个选项', options: [{ id: 'x', label: 'X' }] },
    ],
  })

  assert.equal(request.title, '确认三个项目')
  assert.equal(request.questions.length, 2)
  assert.equal(request.questions[0].options[0].recommended, true)
  assert.equal(request.questions[1].multiple, true)
  assert.equal(request.questions[1].options.length, 2)
})

test('required validation supports single and multi select', () => {
  const request = normalizeInteractionRequest({
    questions: [
      {
        id: 'one',
        title: '单选',
        options: [{ id: 'a', label: 'A' }, { id: 'b', label: 'B' }],
      },
      {
        id: 'many',
        title: '多选',
        multiple: true,
        options: [{ id: 'x', label: 'X' }, { id: 'y', label: 'Y' }],
      },
    ],
  })
  assert.deepEqual(missingRequiredQuestionIds(request, {}), ['one', 'many'])
  assert.deepEqual(missingRequiredQuestionIds(request, { one: 'a', many: ['x'] }), [])
})

test('respond decision answers ask_user and rejects accidental side effects', () => {
  const actions = [
    {
      interrupt_id: 'interrupt-1',
      action_index: 0,
      tool_name: 'ask_user',
      arguments: { questions: [] },
    },
    {
      interrupt_id: 'interrupt-1',
      action_index: 1,
      tool_name: 'delete',
      arguments: { path: 'report.pdf' },
    },
    {
      interrupt_id: 'interrupt-2',
      action_index: 0,
      tool_name: 'ask_user',
      arguments: { questions: [] },
    },
  ]
  const plan = buildInteractionResumeDecisions(actions, {
    interruptId: 'interrupt-1',
    answers: { project: 'fast' },
    note: '先做核心流程',
  })

  assert.equal(plan.interruptId, 'interrupt-1')
  assert.equal(plan.decisions.length, 2)
  assert.equal(plan.decisions[0].type, 'respond')
  assert.equal(plan.decisions[1].type, 'reject')
  assert.deepEqual(
    JSON.parse(plan.decisions[0].message),
    { answers: { project: 'fast' }, note: '先做核心流程' },
  )
})

test('multiple ask_user actions in one interrupt keep independent answers', () => {
  const actions = [
    {
      tool_name: 'ask_user',
      interrupt_id: 'interrupt-batch',
      action_index: 0,
      arguments: {
        title: '项目 A',
        questions: [{
          id: 'project-a',
          title: '项目 A 方案',
          options: [{ id: 'fast', label: '快速' }, { id: 'full', label: '完整' }],
        }],
      },
    },
    {
      tool_name: 'ask_user',
      interrupt_id: 'interrupt-batch',
      action_index: 1,
      arguments: {
        title: '项目 B',
        questions: [{
          id: 'project-b',
          title: '项目 B 范围',
          multiple: true,
          options: [{ id: 'web', label: 'Web' }, { id: 'mobile', label: '移动端' }],
        }],
      },
    },
  ]
  const [batch] = buildInteractionBatches(actions)
  const answers = Object.fromEntries(batch.entries.map(entry => [
    entry.questionId,
    entry.question.multiple ? [entry.question.options[0].id] : entry.question.options[0].id,
  ]))
  const responses = buildInteractionBatchResponses(batch, answers, '先做核心流程')

  assert.equal(batch.actions.length, 2)
  assert.deepEqual(
    responses.map(item => item.response.answers),
    [
      { 'project-a': 'fast' },
      { 'project-b': ['web'] },
    ],
  )
  assert.equal(responses[1].response.note, '先做核心流程')

  const plan = buildInteractionResumeDecisions(actions, {
    interruptId: 'interrupt-batch',
    responses,
  })
  assert.deepEqual(
    plan.decisions.map(item => JSON.parse(item.message)),
    [
      { answers: { 'project-a': 'fast' } },
      { answers: { 'project-b': ['web'] }, note: '先做核心流程' },
    ],
  )
})

test('parses persisted tool result and pending action list', () => {
  assert.deepEqual(
    parseInteractionResponse('{"answers":{"project-a":"fast"},"note":"继续"}'),
    { answers: { 'project-a': 'fast' }, note: '继续' },
  )
  assert.deepEqual(
    pendingInteractionActions({
      pending_approval: {
        action_requests: [
          { tool_name: 'ask_user', arguments: {} },
          { tool_name: 'delete', arguments: {} },
        ],
      },
    }).map(item => item.tool_name),
    ['ask_user'],
  )
})

test('invalid interaction modes fall back to normal', () => {
  assert.equal(normalizeInteractionMode('plan'), 'plan')
  assert.equal(normalizeInteractionMode('BRAINSTORM'), 'brainstorm')
  assert.equal(normalizeInteractionMode('unknown'), 'normal')
})

test('slash commands expose the dedicated plan mode only', () => {
  assert.deepEqual(
    interactionCommandItems('').map(item => item.dir_name),
    ['plan'],
  )
  assert.deepEqual(
    interactionCommandItems('brain'),
    [],
  )
})

test('confirmation text lists every selected choice as a user input', () => {
  const request = normalizeInteractionRequest({
    questions: [
      {
        id: 'a',
        title: '项目 A',
        options: [{ id: 'fast', label: '快速版本' }, { id: 'full', label: '完整版本' }],
      },
      {
        id: 'b',
        title: '项目 B',
        multiple: true,
        options: [{ id: 'web', label: 'Web' }, { id: 'mobile', label: '移动端' }],
      },
    ],
  })
  assert.equal(
    buildConfirmationMessage(request, { a: 'fast', b: ['web', 'mobile'] }, '先做核心流程'),
    '1. 项目 A：快速版本\n2. 项目 B：Web、移动端\n补充说明：先做核心流程',
  )
})
