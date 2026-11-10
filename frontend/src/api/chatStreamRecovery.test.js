import assert from 'node:assert/strict'
import test from 'node:test'
import { readSSE } from './sse.js'
import { recoverChatStream } from '../shared/utils/chatStreamRecovery.js'

const frame = data => 'data: ' + JSON.stringify(data) + '\n\n'
function response(events) {
  return new Response(new ReadableStream({start(c) {
    for (const event of events) c.enqueue(new TextEncoder().encode(frame(event)))
    c.close()
  }}))
}
function setup(initial, overrides = {}) {
  const events = []
  const calls = []
  return {events, calls, options: {
    response: response(initial), sessionId: 's1', onChunk: e => events.push(e), readSSE,
    getStatus: async sid => {calls.push(['status',sid]);return {active:true}},
    openLive: async sid => {calls.push(['live',sid]);return response([{type:'done'}])},
    wait: async () => {}, ...overrides,
  }}
}

test('提前 EOF 保持请求未完成，查询状态后只通过 live 回放恢复', async () => {
  let release
  let settled = false
  const ready = new Promise(resolve => {release=resolve})
  const h = setup([{type:'start',session_id:'s1'},{type:'content',content:'A'}], {
    openLive: async () => {await ready;return response([{type:'start',session_id:'s1'},{type:'content',content:'AB'},{type:'done'}])},
  })
  const pending = recoverChatStream(h.options).then(()=>{settled=true})
  await new Promise(resolve=>setImmediate(resolve))
  assert.equal(settled,false,'不能提前进入页面 finally 清除停止入口')
  assert.ok(h.events.some(e=>e.type==='stream_connection' && e.status==='reconnecting'))
  release()
  await pending
  assert.equal(h.events.filter(e=>e.type==='stream_replay').length,1)
  assert.equal(h.events.at(-1).type,'done')
})

test('新会话从 start 获取 session_id；即使已完成也回放最终内容', async () => {
  const h=setup([{type:'start',session_id:'new-id'},{type:'content',content:'partial'}],{sessionId:null})
  h.options.getStatus=async sid=>{h.calls.push(['status',sid]);return {active:false}}
  await recoverChatStream(h.options)
  assert.deepEqual(h.calls,[['status','new-id'],['live','new-id']])
})

for(const type of ['done','error','approval_required']) test(type+' 业务终态不重连',async()=>{
  const h=setup([{type,content:'结束',thread_id:'t1'}])
  await recoverChatStream(h.options)
  assert.equal(h.calls.length,0)
})

test('网络读取异常重连；状态查询暂时失败也不误报任务完成',async()=>{
  const h=setup([])
  let reads=0, queries=0
  h.options.readSSE=async(res,cb,opts)=>{if(++reads===1)throw new TypeError('socket lost');return readSSE(res,cb,opts)}
  h.options.getStatus=async()=>{if(++queries===1)throw new TypeError('offline');return {active:true}}
  await recoverChatStream(h.options)
  assert.equal(queries,2)
  assert.equal(h.events.at(-1).type,'done')
})

test('停止/卸载在退避期间中止恢复，不再发起 live 请求',async()=>{
  const controller=new AbortController()
  const h=setup([{type:'content',content:'partial'}],{signal:controller.signal,wait:async()=>controller.abort()})
  await recoverChatStream(h.options)
  assert.ok(!h.calls.some(([kind])=>kind==='live'))
})

test('回调程序异常不能伪装成断网无限重试',async()=>{
  const h=setup([{type:'content',content:'x'}],{onChunk:()=>{throw new Error('handler bug')}})
  await assert.rejects(recoverChatStream(h.options),/handler bug/)
  assert.deepEqual(h.calls,[])
})

test('取消正在等待的重连响应会及时结束',async()=>{
  const controller=new AbortController()
  const h=setup([{type:'start',session_id:'s1'}],{signal:controller.signal})
  h.options.openLive=async(_sid,signal)=>{
    assert.equal(signal,controller.signal)
    controller.abort()
    throw new DOMException('aborted','AbortError')
  }
  await recoverChatStream(h.options)
})


test('初次挂载失败也保持恢复循环，直到 live 可用', async () => {
  const h = setup([], { response: null })
  let attempts = 0
  h.options.openLive = async () => {
    if (++attempts === 1) throw new TypeError('gateway failed')
    return response([{type:'done'}])
  }
  await recoverChatStream(h.options)
  assert.equal(attempts, 2)
  assert.equal(h.events.at(-1).type, 'done')
})

for (const status of [401, 403, 404]) test('不可恢复状态 ' + status + ' 结束重试', async () => {
  const h = setup([])
  h.options.getStatus = async () => { throw Object.assign(new Error('unavailable'), {status}) }
  await assert.rejects(recoverChatStream(h.options), /unavailable/)
  assert.equal(h.calls.length, 0)
})

test('真实退避计时可取消，并清除重连提示', async () => {
  const controller = new AbortController()
  const h = setup([], {signal:controller.signal})
  delete h.options.wait
  const pending = recoverChatStream(h.options)
  await new Promise(resolve => setImmediate(resolve))
  controller.abort()
  await pending
  assert.equal(h.events.at(-1).status, 'stopped')
  assert.deepEqual(h.calls, [])
})
