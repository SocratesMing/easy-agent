/**
 * Chat.vue 的流式跟随滚动不能再对 messages 做深度监听。
 *
 * 深度监听每次刷新都遍历整个会话（工具参数 / 结果 / blocks）：30 轮长会话实测每次约 12ms；
 * 后台会话的流事件经 runInSession 换入换出 messages 时还会连带触发，多会话并行时当前会话越长越卡。
 * 现改为非深度监听 + 只读末条消息的 tailSignature。
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import Vue from 'vue'

const src = readFileSync(new URL('./Chat.vue', import.meta.url), 'utf8')
const body = src.match(/\n {4}tailSignature\(\) \{\n([\s\S]*?)\n {4}\},\n {4}canGoToPrev/)[1]
// eslint-disable-next-line no-new-func
const tailSignature = new Function(body)

test('messages 监听不得使用 deep', () => {
  const watchBlock = src.slice(src.indexOf('  watch: {'), src.indexOf('  mounted()'))
  assert.equal(/deep:\s*true/.test(watchBlock), false)
})

test('tailSignature 感知末条消息的原地增长，不受旧消息与后台换入换出影响', async () => {
  const vm = new Vue({ data: { messages: [] }, computed: { tailSignature } })
  const tick = () => new Promise(resolve => Vue.nextTick(resolve))
  vm.messages = [{ id: 'u', role: 'user', content: '旧问题' }, { id: 'a', role: 'assistant', content: '', blocks: [], loading: true }]
  await tick()
  let sig = vm.tailSignature

  vm.messages[1].blocks.push({ type: 'content', content: '' })
  vm.messages[1].blocks[0].content = '北京的秋天'
  assert.notEqual(vm.tailSignature, sig, '正文原地增长要触发')
  sig = vm.tailSignature

  vm.messages[1].blocks.push({ type: 'tool_call', streaming: true, arguments: { content: 'a' } })
  sig = vm.tailSignature
  vm.messages[1].blocks[1].arguments = { content: 'abcdef' }
  assert.notEqual(vm.tailSignature, sig, '工具参数分片增长要触发')
  sig = vm.tailSignature

  vm.messages[0].content = '改了旧消息'
  assert.equal(vm.tailSignature, sig, '旧消息变化不影响')

  const saved = vm.messages
  vm.messages = [{ id: 'other' }]
  vm.messages = saved
  assert.equal(vm.tailSignature, sig, '后台会话换入换出后签名不变')
  vm.$destroy()
})
