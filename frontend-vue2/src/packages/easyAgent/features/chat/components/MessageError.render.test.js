import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import test from 'node:test'
import { JSDOM } from 'jsdom'

test('消息错误超过 5 秒仍可见，历史回填后也不自动隐藏', async t => {
  const dom = new JSDOM('<html><body><div id="mount"></div></body></html>')
  const keys = ['window', 'document', 'navigator', 'Element', 'HTMLElement', 'SVGElement', 'Node']
  const originals = keys.map(key => Object.getOwnPropertyDescriptor(globalThis, key))
  for (const key of keys) Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true, writable: true })
  const Vue = createRequire(import.meta.url)('vue/dist/vue.common.js')
  let vm
  try {
    t.mock.timers.enable({ apis: ['setTimeout'] })
    const source = readFileSync(new URL('./MessageError.vue', import.meta.url), 'utf8')
    const template = source.match(/<template>([\s\S]*?)<\/template>/)[1]
    const script = source.match(/<script>([\s\S]*?)<\/script>/)[1]
    const component = new Function(script.replace('export default', 'return'))()
    Object.assign(component, Vue.compile(template))
    vm = new Vue({
      data: () => ({ error: '模型连接中断' }),
      render(h) { return h(component, { props: { error: this.error } }) },
    }).$mount('#mount')
    assert.match(dom.window.document.body.textContent, /模型连接中断/)
    t.mock.timers.tick(5001)
    await Vue.nextTick()
    assert.match(dom.window.document.body.textContent, /模型连接中断/)
    vm.error = ''
    await Vue.nextTick()
    assert.equal(dom.window.document.body.textContent, '')
    vm.error = '历史消息错误'
    await Vue.nextTick()
    t.mock.timers.tick(60000)
    await Vue.nextTick()
    assert.match(dom.window.document.body.textContent, /历史消息错误/)
  } finally {
    if (vm) vm.$destroy()
    dom.window.close()
    keys.forEach((key, index) => {
      if (originals[index]) Object.defineProperty(globalThis, key, originals[index])
      else delete globalThis[key]
    })
  }
})
