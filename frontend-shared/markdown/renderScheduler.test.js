import assert from 'node:assert/strict'
import test from 'node:test'

import { createRenderScheduler } from './renderScheduler.js'

function fakeClock() {
  let now = 0
  let nextId = 1
  const timers = new Map()
  return {
    now: () => now,
    schedule(fn, delay) {
      const id = nextId++
      timers.set(id, { at: now + Math.max(0, delay), fn })
      return id
    },
    cancel(id) {
      timers.delete(id)
    },
    advance(ms) {
      const target = now + ms
      while (true) {
        const next = [...timers.entries()]
          .filter(([, timer]) => timer.at <= target)
          .sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0]
        if (!next) break
        const [id, timer] = next
        timers.delete(id)
        now = timer.at
        timer.fn()
      }
      now = target
    },
  }
}

test('持续分片不会重置中间帧计时器，最终帧从最后一次输入后计时', () => {
  const clock = fakeClock()
  const events = []
  const scheduler = createRenderScheduler({
    frameMs: 80,
    settleMs: 150,
    now: clock.now,
    schedule: clock.schedule,
    cancel: clock.cancel,
    render: () => events.push(['frame', clock.now()]),
    settle: () => events.push(['settle', clock.now()]),
  })

  scheduler.queue()
  for (let i = 0; i < 5; i += 1) {
    clock.advance(20)
    scheduler.queue()
  }

  // t=100：首帧已在 t=80 渲染；如果每次 queue 都重置计时器，这里仍是 0 次。
  assert.deepEqual(events.filter(([type]) => type === 'frame'), [['frame', 80]])

  clock.advance(150)
  assert.deepEqual(events.filter(([type]) => type === 'settle'), [['settle', 250]])
  assert.equal(events.at(-1)[0], 'settle')
})

test('dispose 会取消尚未执行的中间帧和最终帧', () => {
  const clock = fakeClock()
  const events = []
  const scheduler = createRenderScheduler({
    frameMs: 80,
    settleMs: 150,
    now: clock.now,
    schedule: clock.schedule,
    cancel: clock.cancel,
    render: () => events.push('frame'),
    settle: () => events.push('settle'),
  })

  scheduler.queue()
  scheduler.dispose()
  clock.advance(500)

  assert.deepEqual(events, [])
})
