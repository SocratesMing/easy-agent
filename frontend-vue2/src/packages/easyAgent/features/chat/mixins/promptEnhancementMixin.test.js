import test from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import Vue from 'vue'
import enhancementMixin from './promptEnhancementMixin.js'
import { configureEasyAgent } from '../../../runtime/config.js'
import { storeAuth } from '../../../utils/request.js'
import { emitRuntimeEvent } from '../../../runtime/events.js'
import { chatStore, chatBus } from '../store/chatStore.js'

const storage = () => {
  const values = new Map()
  return { getItem: key => values.get(key) || null, setItem: (key, value) => values.set(key, String(value)), removeItem: key => values.delete(key) }
}
globalThis.localStorage = storage()
globalThis.sessionStorage = storage()
globalThis.window = new EventTarget()
const requests = []
const waiting = []
const server = http.createServer((req, res) => {
  let body = ''
  req.on('data', data => { body += data })
  req.on('end', () => {
    requests.push({ req, res, body })
    waiting.shift()?.()
  })
})
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
configureEasyAgent({ api: { baseURL: `http://127.0.0.1:${server.address().port}/host`, headers: () => ({ 'x-host': 'embedded' }) }, auth: { tokenKey: 'custom-token', usernameKey: 'custom-user' } })
storeAuth('agent-token', 'alice')
test.after(() => { server.closeAllConnections(); server.close() })
const receive = () => new Promise(resolve => waiting.push(resolve))
const finish = (entry, text) => { entry.res.writeHead(200, { 'Content-Type': 'application/json' }); entry.res.end(JSON.stringify({ enhanced_prompt: text })) }
function input() {
  return new Vue({
    mixins: [enhancementMixin],
    data: () => ({ message: '原始\n    内容', tokenMap: new Map(), knownSkills: [], preselectedSkillDirs: [],
      pendingExpertEditId: '', activeExpert: null, expertPrompt: '', sessionId: 's1', draftKey: 'main', disabled: false,
      isStreaming: false, preparingFiles: false, uploadedFiles: ['attachment'] }),
    methods: { autoResize() {}, syncMirror() {} },
  })
}

test('Vue2 adapter uses host transport, preserves attachments and synchronous edits without success notices', async () => {
  const vm = input()
  assert.equal(vm.canEnhance, true)
  let received = receive()
  let pending = vm.enhancePrompt()
  await received
  const first = requests.at(-1)
  assert.equal(first.req.url, '/host/agent/prompts/enhance')
  assert.equal(first.req.headers.authorization, 'Bearer agent-token')
  assert.equal(first.req.headers['x-host'], 'embedded')
  assert.deepEqual(JSON.parse(first.body), { prompt: '原始\n    内容' })
  finish(first, '新正文'); await pending
  assert.equal(vm.message, '新正文')
  assert.equal(vm.enhancement.notice, '')
  assert.deepEqual([...vm.uploadedFiles], ['attachment'])
  received = receive(); pending = vm.enhancePrompt(); await received
  vm.message = '修改'; vm.message = '原始\n    内容'
  finish(requests.at(-1), '过期'); await pending
  assert.equal(vm.message, '原始\n    内容')
  assert.equal(vm.enhancement.pending, false)
  assert.equal(vm.enhancement.notice, '')
  vm.$destroy()
})

test('Vue2 cancels on IME, draft/account/selection/stream changes and unmount', async () => {
  const changes = [
    vm => vm.enhancementCompositionStart(),
    vm => { vm.activeExpert = { id: 'expert' }; vm.activeExpert = null },
    vm => { vm.preselectedSkillDirs.push('skill'); vm.preselectedSkillDirs.pop() },
    vm => { vm.sessionId = 's2'; vm.sessionId = 's1' },
    vm => { sessionStorage.setItem('sidebar', '侧栏草稿'); vm.draftKey = 'sidebar' },
    () => emitRuntimeEvent('auth-expired'),
    () => { storeAuth('b', 'bob'); storeAuth('agent-token', 'alice') },
    () => chatBus.$emit('stream-started', { sessionId: 's1' }),
    () => { chatStore.streamingSessions.push('s1'); chatStore.streamingSessions.pop() },
    vm => vm.$destroy(),
  ]
  for (const change of changes) {
    const vm = input()
    const received = receive(), pending = vm.enhancePrompt()
    await received
    change(vm)
    assert.equal(vm.enhancement.pending, false)
    finish(requests.at(-1), '过期')
    await pending
    assert.notEqual(vm.message, '过期')
    assert.equal(vm.enhancement.notice, '')
    vm.$destroy()
  }
})
