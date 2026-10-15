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
