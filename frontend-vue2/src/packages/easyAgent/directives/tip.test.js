import assert from 'node:assert/strict'
import test from 'node:test'

import tip from './tip.js'

function fakeEl() {
  const listeners = new Map()
  return {
    listeners,
    addEventListener(type, fn) {
      listeners.set(type, fn)
    },
    removeEventListener(type, fn) {
      if (listeners.get(type) === fn) listeners.delete(type)
    },
    removeAttribute() {},
  }
}

test('v-tip binds hover, focus and keyboard handlers', () => {
  const el = fakeEl()
  tip.bind(el, { value: '发送', modifiers: {} })

  for (const type of ['mouseenter', 'mouseleave', 'keydown', 'focus', 'blur']) {
    assert.equal(typeof el.listeners.get(type), 'function', `${type} handler`)
  }
  assert.equal(el.__tipText, '发送')

  tip.unbind(el)
  assert.equal(el.listeners.size, 0)
})

test('v-tip:hover skips focus handlers', () => {
  const el = fakeEl()
  tip.bind(el, { value: '输入', modifiers: { hover: true } })

  assert.equal(typeof el.listeners.get('mouseenter'), 'function')
  assert.equal(el.listeners.has('focus'), false)
  assert.equal(el.listeners.has('blur'), false)
})
