// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/utils/fileReferencePicker.test.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

// 执行两侧实际使用的 Options API 组件，只替换文件树传输与 DOM 滚动。
const source = readFileSync(new URL('../../features/chat/components/FileReferencePicker.vue', import.meta.url), 'utf8')
const script = source.match(/<script>([\s\S]*?)<\/script>/)[1]
const picker = new Function('FileReferenceIcon', script.replace(/^import .*$/gm, '').replace('export default', 'return'))({})

function mount(loadTree, sessionId = 'A') {
  const events = []
  const vm = { ...picker.data(), sessionId, query: '', open: true, loadTree,
    $emit: (...args) => events.push(args), $nextTick: fn => fn(),
    $el: { querySelector: () => null }, events }
  for (const [name, fn] of Object.entries(picker.methods)) vm[name] = fn.bind(vm)
  Object.defineProperty(vm, 'filtered', { get: () => picker.computed.filtered.call(vm) })
  return vm
}

const folder = { name: '底稿', path: 'output/底稿', type: 'directory' }
const file = { name: '报告.docx', path: 'output/报告.docx', type: 'file' }

test('文件树必须带会话 ID；无会话不查询、不建会话', async () => {
  const calls = []
  const vm = mount(async (...args) => { calls.push(args); return { items: [folder, file] } })
  await vm.load('output')
  assert.deepEqual(calls, [['output', 'A']])
  vm.query = '报告'
  assert.deepEqual(vm.filtered, [file])
  vm.sessionId = ''
  await vm.load('')
  assert.equal(calls.length, 1)
  assert.deepEqual(vm.items, [])
  assert.equal(vm.loading, false)
})

test('键盘 Enter 选择目录，箭头独立进入与返回，空候选也拦截发送', async () => {
  const calls = []
  const vm = mount(async path => { calls.push(path); return { items: path === 'output' ? [folder, file] : [] } })
  await vm.load('output')
  const key = name => {
    let prevented = false
    const handled = vm.key({ key: name, preventDefault() { prevented = true } })
    assert.equal(prevented, true)
    assert.equal(handled, true)
  }
  key('Enter')
  assert.deepEqual(vm.events.pop(), ['select', folder])
  key('ArrowDown')
  key('Enter')
  assert.deepEqual(vm.events.pop(), ['select', file])
  key('ArrowUp')
  key('ArrowRight')
  assert.equal(vm.path, 'output/底稿')
  assert.deepEqual(vm.events.pop(), ['navigate'])
  key('ArrowLeft')
  assert.equal(vm.path, 'output')
  await vm.load('empty')
  key('Enter')
  assert.equal(vm.events.filter(e => e[0] === 'select').length, 0)
  key('Escape')
  assert.deepEqual(vm.events.pop(), ['close'])
  assert.deepEqual(calls.slice(0, 3), ['output', 'output/底稿', 'output'])
})

test('切换会话与关闭面板使旧请求失效，旧失败不能覆盖新结果', async () => {
  let finishA, finishB
  const vm = mount((_path, sessionId) => new Promise((resolve, reject) => {
    if (sessionId === 'A') finishA = reject
    else finishB = resolve
  }))
  const requestA = vm.load('output')
  vm.sessionId = 'B'
  picker.watch.sessionId.call(vm)
  finishB({ items: [file] })
  await Promise.resolve()
  finishA(new Error('旧会话失败'))
  await requestA
  assert.deepEqual(vm.items, [file])
  assert.equal(vm.error, '')
  const pending = vm.load('')
  picker.watch.open.handler.call(vm, false)
  finishB({ items: [folder] })
  await pending
  assert.deepEqual(vm.items, [])
  assert.equal(vm.loading, false)
})
