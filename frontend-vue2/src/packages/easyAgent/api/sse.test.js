import assert from 'node:assert/strict'
import test from 'node:test'

import { readSSE } from './sse.js'

const encoder = new TextEncoder()

function sseResponse(chunks) {
  const stream = new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  })
  return new Response(stream, { status: 200 })
}

test('跨 chunk 拼接并解析多个 data 事件', async () => {
  const events = []
  await readSSE(
    sseResponse([
      'data: {"type":"content","content":"he',
      'llo"}\n\n',
      'data: {"type":"done","session_id":"s1"}\n\n',
    ]),
    (event) => events.push(event)
  )
  assert.deepEqual(events, [
    { type: 'content', content: 'hello' },
    { type: 'done', session_id: 's1' },
  ])
})

test('忽略非 data 行并支持末尾无换行事件', async () => {
  const events = []
  await readSSE(
    sseResponse([': keep-alive\n\ndata: {"type":"content","content":"tail"}']),
    (event) => events.push(event)
  )
  assert.deepEqual(events, [{ type: 'content', content: 'tail' }])
})

test('handler 抛错会中止读取并向上传播', async () => {
  await assert.rejects(
    readSSE(sseResponse(['data: {"type":"error","content":"boom"}\n\n']), () => {
      throw new Error('handler-boom')
    }),
    /handler-boom/
  )
})

test('已 abort 的 signal 不产生事件', async () => {
  const controller = new AbortController()
  controller.abort()
  const events = []
  await readSSE(sseResponse(['data: {"type":"content","content":"x"}\n\n']), (e) => events.push(e), {
    signal: controller.signal,
  })
  assert.equal(events.length, 0)
})
