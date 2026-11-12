import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { compileScript, parse } from '@vue/compiler-sfc'
import * as Vue from 'vue'

// 编译实际 SFC，用内存宿主执行组件生命周期，避免依赖浏览器布局。
const source = readFileSync(new URL('./MessageError.vue', import.meta.url), 'utf8')
const { descriptor } = parse(source)
const compiled = compileScript(descriptor, { id: 'message-error-test', inlineTemplate: true }).content
const component = new Function('Vue', compiled
  .replace(/import\s+\{([^}]+)\}\s+from\s+['"]vue['"];?/g,
    (_, bindings) => `const { ${bindings.replace(/\bas\b/g, ':')} } = Vue`)
  .replace('export default', 'return'))(Vue)

function createHost() {
  const node = (type, text = '') => ({ type, text, children: [], parent: null })
  const remove = child => {
    if (child.parent) child.parent.children.splice(child.parent.children.indexOf(child), 1)
    child.parent = null
  }
  const renderer = Vue.createRenderer({
    createElement: type => node(type),
    createText: text => node('text', text),
    createComment: () => node('comment'),
    setText: (target, text) => { target.text = text },
    setElementText: (target, text) => { target.text = text; target.children = [] },
    patchProp: () => {},
    parentNode: target => target.parent,
    nextSibling: target => target.parent?.children[target.parent.children.indexOf(target) + 1] || null,
    insert(child, parent, anchor) {
      remove(child)
      const index = anchor ? parent.children.indexOf(anchor) : parent.children.length
      parent.children.splice(index, 0, child)
      child.parent = parent
    },
    remove,
  })
  return { renderer, root: node('root') }
}

const textContent = node => node.text + node.children.map(textContent).join('')

test('消息错误超过 5 秒仍可见，历史回填后也不自动隐藏', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const { renderer, root } = createHost()
  const error = Vue.ref('模型连接中断')
  const app = renderer.createApp({ render: () => Vue.h(component, { error: error.value }) })
  t.after(() => app.unmount())
  app.mount(root)
  assert.match(textContent(root), /模型连接中断/)
  t.mock.timers.tick(5001)
  await Vue.nextTick()
  assert.match(textContent(root), /模型连接中断/)
  error.value = ''
  await Vue.nextTick()
  assert.equal(textContent(root), '')
  error.value = '历史消息错误'
  await Vue.nextTick()
  t.mock.timers.tick(60000)
  await Vue.nextTick()
  assert.match(textContent(root), /历史消息错误/)
})
