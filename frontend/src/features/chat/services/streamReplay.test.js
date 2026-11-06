import assert from 'node:assert/strict'
import test from 'node:test'
import { createStreamChunkHandler } from './chunkHandler.js'

function harness(resume = false) {
  const vm={
    $:{uid:1},
    messages:[{id:'a1',role:'assistant',content:resume?'审批前内容':'',blocks:resume?[{type:'content',content:'审批前内容',step:1,order:1}]:[],loading:true}],
    currentSessionId:'s1',sessionUsage:{input_tokens:10},sessionDuration:10,iterationCount:1,currentTodos:[],sessionStates:{},sessions:[],attachedStreamingSessions:{},
    $set:(obj,k,v)=>{obj[k]=v}, markStreaming:()=>{},loadSessions:()=>{},runInSession:(_sid,_ctx,fn)=>fn(),error:null,
  }
  const ctx={assistantMsgId:'a1',streamSessionId:'s1',isResume:resume,initialBlockOrder:resume?1:0,preStreamDuration:10,preStreamIterationCount:1}
  return {vm,onChunk:createStreamChunkHandler(vm,ctx).onChunk}
}

for(const resume of [false,true]) test('从头回放重建内容和用量，不重复累加；resume='+resume,()=>{
  const {vm,onChunk}=harness(resume)
  const events=[{type:'content',content:'新增',step:2},{type:'token_usage',input_tokens:20,elapsed_time:2,step_count:2}]
  for(const e of events)onChunk(e)
  const expected=JSON.stringify(vm.messages)
  const duration=vm.sessionDuration, steps=vm.iterationCount
  for(let n=0;n<2;n++){
    onChunk({type:'stream_replay'})
    for(const e of events)onChunk(e)
    assert.equal(JSON.stringify(vm.messages),expected)
    assert.equal(vm.sessionDuration,duration)
    assert.equal(vm.iterationCount,steps)
  }
})

test('重连提示不结束消息；恢复后清除重连提示',()=>{
  const {vm,onChunk}=harness()
  onChunk({type:'content',content:'保留内容'})
  onChunk({type:'stream_connection',status:'reconnecting'})
  assert.match(vm.error,/重新连接/)
  assert.equal(vm.messages[0].loading,true)
  assert.ok(vm.messages[0].blocks.some(b=>b.content==='保留内容'))
  onChunk({type:'stream_connection',status:'connected'})
  assert.equal(vm.error,null)
})


test('思考与工具结果回放只保留一份', () => {
  const {vm, onChunk} = harness()
  const events = [
    {type:'start', session_id:'s1'},
    {type:'thinking_start', step:1},
    {type:'thinking', content:'分析', step:1},
    {type:'thinking_end', duration:1, step:1},
    {type:'tool_call', tool_name:'search', tool_call_id:'t1', arguments:{q:'风险'}, step:1},
    {type:'tool_result', tool_name:'search', tool_call_id:'t1', result:'结果', success:true, step:1},
    {type:'content', content:'结论', step:2},
  ]
  for(const event of events)onChunk(event)
  const expected=JSON.stringify(vm.messages)
  onChunk({type:'stream_replay'})
  for(const event of events)onChunk(event)
  assert.equal(JSON.stringify(vm.messages),expected)
  assert.equal(vm.messages[0].blocks.filter(b=>b.type==='tool_call').length,1)
})
