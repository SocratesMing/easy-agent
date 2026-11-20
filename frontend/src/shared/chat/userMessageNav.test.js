// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/chat/userMessageNav.test.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
import test from 'node:test'
import assert from 'node:assert/strict'
import {
  buildUserMessageNavItems,
  findActiveUserNavIndex,
  getTickLevel
} from './userMessageNav.js'

test('按顺序提取用户消息及其紧邻的助手回复摘要', () => {
  const messages = [
    { id: 'u1', role: 'user', content: '第一个问题   带换行\n和空格' },
    { id: 'a1', role: 'assistant', content: '第一轮回答内容' },
    { id: 'u2', role: 'user', content: '', files: [{ name: 'report.pdf' }] },
    {
      id: 'a2',
      role: 'assistant',
      content: '',
      blocks: [{ type: 'thinking', content: '思考中' }, { type: 'content', content: '来自 blocks 的正文' }]
    },
    { id: 'u3', role: 'user', content: '第三个问题（尚无回复）' }
  ]

  const items = buildUserMessageNavItems(messages)
  assert.equal(items.length, 3)
  assert.deepEqual(items[0], {
    id: 'u1',
    messageIndex: 0,
    turnNumber: 1,
    prompt: '第一个问题 带换行 和空格',
    reply: '第一轮回答内容'
  })
  assert.deepEqual(items[1], {
    id: 'u2',
    messageIndex: 2,
    turnNumber: 2,
    prompt: '[附件] report.pdf',
    reply: '来自 blocks 的正文'
  })
  assert.deepEqual(items[2], {
    id: 'u3',
    messageIndex: 4,
    turnNumber: 3,
    prompt: '第三个问题（尚无回复）',
    reply: ''
  })
})

test('根据滚动位置和贴底状态计算当前激活的用户消息索引', () => {
  const items = [
    { messageIndex: 0 },
    { messageIndex: 2 },
    { messageIndex: 4 }
  ]
  const container = { getBoundingClientRect: () => ({ top: 100, height: 600 }) }
  const rects = {
    0: { top: -200 },
    2: { top: 180 },
    4: { top: 520 }
  }
  const getEl = (idx) => ({ getBoundingClientRect: () => rects[idx] })

  assert.equal(findActiveUserNavIndex(items, getEl, container, false), 1)
  assert.equal(findActiveUserNavIndex(items, getEl, container, true), 2)
  assert.equal(findActiveUserNavIndex([], getEl, container, false), -1)
})

test('按距离焦点刻度的远近返回刻度层级', () => {
  assert.equal(getTickLevel(5, 5, -1), 'active')
  assert.equal(getTickLevel(4, 5, -1), 'near-1')
  assert.equal(getTickLevel(3, 5, -1), 'near-2')
  assert.equal(getTickLevel(1, 5, -1), 'default')
  assert.equal(getTickLevel(2, 5, 2), 'hovered')
  assert.equal(getTickLevel(1, 5, 2), 'near-1')
  assert.equal(getTickLevel(5, 5, 2), 'active')
})
