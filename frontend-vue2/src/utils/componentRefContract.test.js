import assert from 'node:assert/strict'
import test from 'node:test'
import { readFile } from 'node:fs/promises'
import path from 'node:path'

const componentsDir = path.resolve(import.meta.dirname, '../components')

const read = (name) => readFile(path.join(componentsDir, name), 'utf8')

function definedMethods(source) {
  return new Set([...source.matchAll(/^ {4}(?:async )?([A-Za-z_]\w*)\(/gm)].map(m => m[1]))
}

function calledMethods(source, receivers) {
  const re = new RegExp(`\\b(?:${receivers.join('|')})\\.([A-Za-z_]\\w*)\\(`, 'g')
  return new Set([...source.matchAll(re)].map(m => m[1]))
}

test('Chat.vue only calls ChatInput methods that exist', async () => {
  const [chat, chatInput] = await Promise.all([read('Chat.vue'), read('ChatInput.vue')])
  const defined = definedMethods(chatInput)
  const called = calledMethods(chat, ['c', 'composer'])

  assert.ok(called.has('focusInput'))
  for (const name of called) assert.ok(defined.has(name), `ChatInput.vue 缺少方法 ${name}()`)
})

test('AiSidebar.vue only calls Chat methods that exist', async () => {
  const [sidebar, chat] = await Promise.all([read('AiSidebar.vue'), read('Chat.vue')])
  const defined = definedMethods(chat)
  const called = calledMethods(sidebar, ['chat'])

  assert.ok(called.has('focusComposer'))
  for (const name of called) assert.ok(defined.has(name), `Chat.vue 缺少方法 ${name}()`)
})

test('every <Chat> host wires expert selection', async () => {
  const hosts = await Promise.all([
    readFile(path.resolve(componentsDir, '../App.vue'), 'utf8'),
    read('AiSidebar.vue'),
  ])
  for (const source of hosts) {
    const chatTag = source.match(/<Chat\b[\s\S]*?>/)[0]
    for (const binding of [':activeExpert=', '@select-expert=', '@clear-expert=']) {
      assert.ok(chatTag.includes(binding), `<Chat> 缺少 ${binding}`)
    }
  }
})

test('pre-created sessions are marked as the displayed session', async () => {
  const mixin = await readFile(path.resolve(componentsDir, '../mixins/chatSession.js'), 'utf8')
  const body = mixin.match(/async ensureCurrentSession\([\s\S]*?\n {4}\},/)[0]
  assert.match(body, /this\.loadedSessionId = newSession\.session_id/)
})
