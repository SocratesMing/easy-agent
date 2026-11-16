import assert from 'node:assert/strict'
import test from 'node:test'
import { createAutoDismiss } from './autoDismiss.js'

/** 假定时器：手动触发已排队的回调，便于断言延迟与取消行为。 */
function createFakeTimers() {
  let nextId = 0
  const pending = new Map()
  return {
    setTimer(fn, ms) {
      const id = ++nextId
      pending.set(id, { fn, ms })
      return id
    },
    clearTimer(id) {
      pending.delete(id)
    },
    runAll() {
      for (const [id, timer] of [...pending]) {
        pending.delete(id)
        timer.fn()
      }
    },
    delays() {
      return [...pending.values()].map((timer) => timer.ms)
    },
  }
}

function createState(initial = null) {
  const state = { value: initial }
  return {
    state,
    read: () => state.value,
    clear: () => {
      state.value = null
    },
  }
}

test('非空提示按默认 5 秒自动清空', () => {
  const fake = createFakeTimers()
  const { state, read, clear } = createState()
  const schedule = createAutoDismiss({ read, clear, setTimer: fake.setTimer, clearTimer: fake.clearTimer })
  state.value = '后端报错：处理失败'
  schedule(state.value)
  assert.deepEqual(fake.delays(), [5000])
  fake.runAll()
  assert.equal(state.value, null)
})

test('新提示到来时取消旧定时器并重新计时', () => {
  const fake = createFakeTimers()
  const { state, read, clear } = createState()
  const schedule = createAutoDismiss({ read, clear, ttlMs: 5000, setTimer: fake.setTimer, clearTimer: fake.clearTimer })
  state.value = '第一条'
  schedule(state.value)
  state.value = '第二条'
  schedule(state.value)
  assert.equal(fake.delays().length, 1)
  fake.runAll()
  assert.equal(state.value, null)
})

test('触发时不会清掉随后出现的新提示', () => {
  const fake = createFakeTimers()
  const { state, read, clear } = createState()
  const schedule = createAutoDismiss({ read, clear, setTimer: fake.setTimer, clearTimer: fake.clearTimer })
  state.value = '旧提示'
  schedule(state.value)
  state.value = '新提示'
  fake.runAll()
  assert.equal(state.value, '新提示')
})

test('传空值只取消未触发的定时器', () => {
  const fake = createFakeTimers()
  const { state, read, clear } = createState()
  const schedule = createAutoDismiss({ read, clear, setTimer: fake.setTimer, clearTimer: fake.clearTimer })
  state.value = '提示'
  schedule(state.value)
  schedule(null)
  assert.equal(fake.delays().length, 0)
  assert.equal(state.value, '提示')
})
