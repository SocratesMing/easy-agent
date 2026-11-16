// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/citations/popover.test.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
import assert from 'node:assert/strict'
import test from 'node:test'
import popover from './popover.js'

// Node 20 的 EventTarget.removeEventListener 不正确处理布尔 capture；
// 归一成对象，保持浏览器的监听 / 移除语义。
class BrowserEventTarget extends EventTarget {
  addEventListener(type, listener, options) {
    super.addEventListener(type, listener, typeof options === 'boolean' ? { capture: options } : options)
  }
  removeEventListener(type, listener, options) {
    super.removeEventListener(type, listener, typeof options === 'boolean' ? { capture: options } : options)
  }
}

// 执行生产 mixin 的事件与生命周期；布局由现有 citations.test.js 覆盖。
function setup(t) {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const win = new BrowserEventTarget()
  const doc = new BrowserEventTarget()
  Object.assign(win, { innerWidth: 1024, innerHeight: 768, document: doc, visualViewport: new BrowserEventTarget() })
  Object.assign(win.visualViewport, { width: 1024, height: 768, offsetLeft: 0, offsetTop: 0 })
  doc.documentElement = { clientWidth: 1024 }
  const globals = ['window', 'document']
  const originals = globals.map(key => Object.getOwnPropertyDescriptor(globalThis, key))
  for (const [key, value] of Object.entries({ window: win, document: doc })) {
    Object.defineProperty(globalThis, key, { value, configurable: true })
  }
  const ticks = []
  const evidence = [{ document_id: 'a' }, { document_id: 'b' }]
  const vm = {
    ...popover.data(),
    messages: [{ content: '[知识依据2] [知识依据1]', knowledge_evidence: evidence }],
    $el: { contains: node => anchors.includes(node) },
    $refs: { citationPopover: { $el: {
      contains: node => node === cardChild,
      getBoundingClientRect: () => ({ height: 200 }),
    } } },
    $nextTick: callback => ticks.push(callback),
  }
  for (const [key, method] of Object.entries(popover.methods)) vm[key] = method.bind(vm)
  const anchors = [1, 2, 99].map(number => {
    const attrs = new Map()
    const anchor = {
      dataset: { cite: String(number) }, isConnected: true,
      closest: selector => selector === '.ke-cite-number' ? anchor : { dataset: { messageIndex: '0' } },
      contains: node => node === anchor,
      setAttribute: (key, value) => attrs.set(key, value),
      removeAttribute: key => attrs.delete(key),
      getAttribute: key => attrs.get(key),
      getBoundingClientRect: () => ({ left: 100, top: 100, bottom: 120 }),
      focus: () => vm.previewCitation({ target: anchor }),
    }
    return anchor
  })
  const cardChild = {}
  const flush = () => { while (ticks.length) ticks.shift()() }
  const dispatch = (target, type, node, extra = {}) => {
    const event = new Event(type, { cancelable: true })
    Object.defineProperty(event, 'target', { value: node })
    Object.assign(event, extra)
    target.dispatchEvent(event)
    return event
  }
  popover.mounted.call(vm)
  t.after(() => {
    vm.disposeCitations()
    globals.forEach((key, index) => {
      if (originals[index]) Object.defineProperty(globalThis, key, originals[index])
      else delete globalThis[key]
    })
  })
  return { vm, anchors, evidence, win, doc, cardChild, flush, dispatch }
}

test('悬停 / 聚焦按正文顺序展示证据，切换编号清理旧关联，无效编号不开卡片', t => {
  const { vm, anchors, evidence, flush } = setup(t)
  vm.previewCitation({ target: anchors[2] })
  assert.equal(vm.citationPreview, null)
  vm.previewCitation({ target: anchors[0] })
  assert.deepEqual(vm.citationPreview, { item: evidence[1], number: 1 })
  assert.equal(anchors[0].getAttribute('aria-describedby'), vm.citationPopoverId)
  assert.equal(vm.citationStyle.visibility, 'hidden')
  flush()
  assert.equal(vm.citationStyle.top, '128px')
  assert.equal(vm.citationStyle.visibility, undefined)
  vm.previewCitation({ target: anchors[1] })
  assert.equal(vm.citationPreview.item, evidence[0])
  assert.equal(anchors[0].getAttribute('aria-describedby'), undefined)
})

test('进入卡片取消延迟关闭，离开后 180ms 关闭并清理 aria 关联', t => {
  const { vm, anchors } = setup(t)
  vm.previewCitation({ target: anchors[0] })
  vm.leaveCitation({ target: anchors[0], relatedTarget: null })
  t.mock.timers.tick(179)
  assert.ok(vm.citationPreview)
  vm.keepCitation()
  t.mock.timers.tick(1000)
  assert.ok(vm.citationPreview)
  vm.deferCitationClose()
  t.mock.timers.tick(180)
  assert.equal(vm.citationPreview, null)
  assert.equal(anchors[0].getAttribute('aria-describedby'), undefined)
})

test('Escape 关闭并恢复焦点，恢复焦点产生的 focusin 不会重开卡片', t => {
  const { vm, anchors, doc, dispatch, flush } = setup(t)
  vm.previewCitation({ target: anchors[0] })
  flush()
  const focus = t.mock.method(anchors[0], 'focus', anchors[0].focus)
  const event = dispatch(doc, 'keydown', anchors[0], { key: 'Escape' })
  assert.equal(event.defaultPrevented, true)
  assert.equal(vm.citationPreview, null)
  assert.equal(focus.mock.callCount(), 1)
  assert.deepEqual(focus.mock.calls[0].arguments, [{ preventScroll: true }])
  flush()
  vm.previewCitation({ target: anchors[0] })
  assert.ok(vm.citationPreview)
})

test('卡片内点击 / 滚动保持打开，外部点击 / 滚动关闭', t => {
  const { vm, anchors, win, doc, cardChild, dispatch } = setup(t)
  for (const [target, type] of [[doc, 'pointerdown'], [win, 'scroll']]) {
    vm.previewCitation({ target: anchors[0] })
    dispatch(target, type, cardChild)
    assert.ok(vm.citationPreview)
    dispatch(target, type, {})
    assert.equal(vm.citationPreview, null)
  }
})

test('切会话、消息变化或锚点移除关闭卡片，卸载移除监听并取消计时', t => {
  const { vm, anchors, win, doc, dispatch, flush } = setup(t)
  for (const watcher of Object.values(popover.watch)) {
    vm.previewCitation({ target: anchors[0] })
    watcher.call(vm)
    assert.equal(vm.citationPreview, null)
  }
  vm.previewCitation({ target: anchors[0] })
  anchors[0].isConnected = false
  popover.updated.call(vm)
  assert.equal(vm.citationPreview, null)
  anchors[0].isConnected = true
  vm.previewCitation({ target: anchors[0] })
  flush()
  vm.deferCitationClose()
  const close = t.mock.method(vm, 'closeCitation')
  popover.beforeDestroy.call(vm)
  assert.equal(vm.citationPreview, null)
  close.mock.resetCalls()
  t.mock.timers.tick(1000)
  assert.equal(close.mock.callCount(), 0)
  vm.previewCitation({ target: anchors[0] })
  dispatch(doc, 'pointerdown', {})
  dispatch(doc, 'keydown', {}, { key: 'Escape' })
  dispatch(win, 'scroll', {})
  vm.citationStyle = {}
  dispatch(win, 'resize', {})
  dispatch(win.visualViewport, 'resize', {})
  dispatch(win.visualViewport, 'scroll', {})
  assert.ok(vm.citationPreview)
  assert.deepEqual(vm.citationStyle, {})
})
