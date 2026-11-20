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

test('v-tip.hover and v-tip:hover both skip focus handlers', () => {
  const el1 = fakeEl()
  tip.bind(el1, { value: '输入', modifiers: { hover: true } })
  assert.equal(typeof el1.listeners.get('mouseenter'), 'function')
  assert.equal(el1.listeners.has('focus'), false)
  assert.equal(el1.listeners.has('blur'), false)

  const el2 = fakeEl()
  tip.bind(el2, { value: '输入', arg: 'hover', modifiers: {} })
  assert.equal(typeof el2.listeners.get('mouseenter'), 'function')
  assert.equal(el2.listeners.has('focus'), false)
  assert.equal(el2.listeners.has('blur'), false)
})

test('hideTip before rAF prevents empty tooltip from getting .show', () => {
  const classes = new Set()
  const tipEl = {
    className: '',
    id: '',
    textContent: '',
    style: {},
    offsetWidth: 100,
    offsetHeight: 24,
    setAttribute() {},
    classList: {
      add: (c) => classes.add(c),
      remove: (c) => classes.delete(c),
    },
  }
  const origDoc = globalThis.document
  const origWin = globalThis.window
  const origRaf = globalThis.requestAnimationFrame
  const origCancelRaf = globalThis.cancelAnimationFrame
  const rafQueue = new Map()
  let rafSeq = 1
  globalThis.document = {
    getElementById: () => ({ id: 'ea-tip-style' }),
    createElement: () => tipEl,
    head: { appendChild() {} },
    body: { appendChild() {} },
  }
  globalThis.window = { innerWidth: 1200, innerHeight: 800 }
  globalThis.requestAnimationFrame = (cb) => {
    const id = rafSeq++
    rafQueue.set(id, cb)
    return id
  }
  globalThis.cancelAnimationFrame = (id) => {
    rafQueue.delete(id)
  }

  try {
    const el = fakeEl()
    el.tagName = 'TEXTAREA'
    el.setAttribute = () => {}
    el.getBoundingClientRect = () => ({ top: 200, bottom: 230, left: 100, width: 400 })
    tip.bind(el, { value: '提示文字', modifiers: { hover: true } })

    el.listeners.get('mouseenter')({ currentTarget: el })
    // 同一帧内立即输入隐藏提示
    el.listeners.get('input')()
    // 即使残余 rAF 被执行，也不应给已隐藏的浮层加上 show
    for (const cb of rafQueue.values()) cb()
    assert.equal(classes.has('show'), false)
    tip.unbind(el)
  } finally {
    globalThis.document = origDoc
    globalThis.window = origWin
    globalThis.requestAnimationFrame = origRaf
    globalThis.cancelAnimationFrame = origCancelRaf
  }
})
