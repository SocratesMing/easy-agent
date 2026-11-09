/**
 * Vue2 侧的流式事件状态机（2026-10-20 从 mixins/chatSession.js 原样抽出，行为不变）。
 *
 * 与 Vue3 的 features/chat/composables/useStreamChunks.js 同源逻辑，两套基线各存一份；
 * 抽出的目的是让 400 行事件处理脱离 1600 行 mixin，便于单测与后续按事件拆分。
 *
 * @param {object} vm 宿主组件（mixin 所在实例），提供 messages/sessions/sessionUsage 等
 * @param {object} ctx 本次流上下文（assistantMsgId、streamSessionId、回调等）
 */
import { chatBus } from '../store/chatStore.js'
import { setReactive } from '../../../utils/reactiveSet.js'
import { applyStreamError } from '../../../utils/streamErrorFallback.js'
import { parseMCPResult } from '../../../utils/mcpResult.js'
import { createStreamCheckpoint, STREAM_RECONNECTING_MESSAGE } from '../../../shared/utils/chatStreamRecovery.js'
import { setExpertLastSession } from '../../../shared/utils/expertSession.js'

/**
 * 审批续跑前的会话用量基数：会话合计扣掉被中断这条消息已计入的耗时 / 步数。
 * 续跑下发的是这条消息的累计值（含中断前），基数 + 累计值即会话合计，中断前那段不会算两次。
 * message.usage 来自历史消息（刷新后），或流式过程中 rememberMessageUsage 记下的值。
 */
export function resumeUsageBase(sessionDuration, iterationCount, message) {
  const usage = (message && message.usage) || {}
  return {
    preStreamDuration: Math.max(0, (sessionDuration || 0) - (usage.elapsed_time || 0)),
    preStreamIterationCount: Math.max(0, (iterationCount || 0) - (usage.step_count || 0)),
  }
}

export function createStreamChunkHandler(vm, ctx) {
  let currentBlock = null
  let currentThinking = ''
  let currentContent = ''
  let currentToolCalls = []
  let blockOrderCounter = ctx.initialBlockOrder || 0

  const findIdx = () => {
    return vm.messages.findIndex(m => m.id === ctx.assistantMsgId)
  }
  const touchBlocks = () => {
    const idx = findIdx()
    if (idx !== -1) setReactive(vm.messages, idx, { ...vm.messages[idx], blocks: [...vm.messages[idx].blocks] })
  }
  const ensureMessage = () => {
    if (ctx.ensureMessage) ctx.ensureMessage()
  }
  const addBlock = (type, data, replace = false) => {
    ensureMessage()
    const idx = findIdx()
    if (idx === -1) return null
    if (type === 'thinking' && data.step !== undefined) {
      const existing = vm.messages[idx].blocks.find(b => b.type === 'thinking' && b.step === data.step)
      if (existing) { currentBlock = existing; return currentBlock }
    }
    blockOrderCounter++
    // 严格按 order 展示时，reopen 场景（late reasoning 晚于同 step 工具块到达）会让
    // 思考块的 order 大于工具块而排到工具之后。此时给思考块一个更小的 order
    //（同 step 最小 order - 0.5），使其排在工具之前，符合「先思考后工具」。
    let blkOrder = blockOrderCounter
    if (type === 'thinking' && data.step !== undefined) {
      const sameStep = vm.messages[idx].blocks.filter(b => b.step === data.step && b.type !== 'thinking')
      if (sameStep.length > 0) {
        blkOrder = Math.min(...sameStep.map(b => b.order || 0)) - 0.5
      }
    }
    const needNewBlock = !currentBlock || currentBlock.type !== type ||
      (type === 'thinking' && data.step !== undefined && currentBlock.step !== data.step)
    if (needNewBlock) {
      currentBlock = { type, content: '', order: blkOrder, ...data }
      vm.messages[idx].blocks.push(currentBlock)
      setReactive(vm.messages, idx, { ...vm.messages[idx], blocks: [...vm.messages[idx].blocks] })
    } else {
      if (replace) {
        currentBlock.content = data.content || ''
      } else if (data.content !== undefined) {
        currentBlock.content = (currentBlock.content || '') + (data.content || '')
      }
      if (data.tool_name) currentBlock.tool_name = data.tool_name
      if (data.arguments !== undefined) currentBlock.arguments = data.arguments
      if (data.result !== undefined) currentBlock.result = data.result
      if (data.success !== undefined) currentBlock.success = data.success
      if (data.duration !== undefined) currentBlock.duration = data.duration
      if (data.step !== undefined) currentBlock.step = data.step
      if (data.id !== undefined) currentBlock.id = data.id
      setReactive(vm.messages, idx, { ...vm.messages[idx] })
    }
    return currentBlock
  }
  const updateThinkingDuration = (duration, step) => {
    const idx = findIdx()
    if (idx !== -1) {
      const blockIdx = vm.messages[idx].blocks.findIndex(b => b.type === 'thinking' && b.step === step)
      if (blockIdx !== -1) {
        setReactive(vm.messages[idx].blocks, blockIdx, { ...vm.messages[idx].blocks[blockIdx], duration })
      }
      setReactive(vm.messages, idx, { ...vm.messages[idx], thinking_duration: duration, blocks: [...vm.messages[idx].blocks] })
    }
  }
  const usagePatchFrom = (data) => {
    const patch = {
      input_tokens: data.input_tokens || 0,
      output_tokens: data.output_tokens || 0,
      reasoning_tokens: data.reasoning_tokens || 0,
      context_tokens: data.context_tokens || vm.sessionUsage.context_tokens || 0,
    }
    if (data.context_length) patch.context_length = data.context_length
    if (data.auto_compress_tokens) patch.auto_compress_tokens = data.auto_compress_tokens
    return patch
  }
  // 记下这条消息自己的累计耗时 / 步数：审批续跑时据此从会话合计里扣掉中断前已计入的部分
  const rememberMessageUsage = (data, rows) => {
    const idx = rows?.findIndex(m => m.id === ctx.assistantMsgId) ?? -1
    if (idx === -1) return
    const msg = rows[idx]
    const usage = {
      ...msg.usage,
      input_tokens: data.input_tokens || 0,
      output_tokens: data.output_tokens || 0,
      reasoning_tokens: data.reasoning_tokens || 0,
      context_tokens: data.context_tokens || data.input_tokens || 0,
    }
    if (typeof data.step_count === 'number') usage.step_count = data.step_count
    if (typeof data.elapsed_time === 'number') usage.elapsed_time = data.elapsed_time
    setReactive(rows, idx, { ...msg, usage })
  }
  const applyUsage = (data, sid) => {
    const patch = usagePatchFrom(data)
    // 状态路由按 runInSession 的 loadedSessionId 判定：组件数据要么就是该会话本身
    // （展示中），要么已被临时换成该会话的状态（ctx.stateSwapped）。两种情况都应
    // 直接写组件数据；只有既不是展示中也没换状态时，才退回写 sessionStates 缓存。
    const match = !sid || ctx.stateSwapped || vm.loadedSessionId === sid
    const rows = match ? vm.messages : vm.sessionStates[sid]?.messages
    rememberMessageUsage(data, rows)
    if (ctx.attachMode) {
      // 重挂载回放：token_usage/done 携带的 elapsed_time 是原始流从头开始的
      // 累计值，应 SET 而非累加（isResume 分支的累加语义只适用于 HITL 续跑）
      if (match) {
        Object.assign(vm.sessionUsage, patch)
        if (typeof data.elapsed_time === 'number') vm.sessionDuration = Math.round(data.elapsed_time * 10) / 10
        if (typeof data.step_count === 'number') vm.iterationCount = data.step_count
      } else {
        const cached = vm.sessionStates[sid]
        if (cached) {
          cached.sessionUsage = { ...cached.sessionUsage, ...patch }
          if (typeof data.elapsed_time === 'number') cached.sessionDuration = Math.round(data.elapsed_time * 10) / 10
          if (typeof data.step_count === 'number') cached.iterationCount = data.step_count
        }
      }
      return
    }
    // elapsed_time / step_count 是这条消息的累计值（审批续跑也接着中断前往下算），
    // 会话合计 = 流开始前其余消息的合计 + 本条累计值
    const newDuration = typeof data.elapsed_time === 'number' ? Math.round((ctx.preStreamDuration + data.elapsed_time) * 10) / 10 : null
    const newIterations = typeof data.step_count === 'number' ? ctx.preStreamIterationCount + data.step_count : null
    if (match) {
      Object.assign(vm.sessionUsage, patch)
      if (newDuration !== null) vm.sessionDuration = newDuration
      if (newIterations !== null) vm.iterationCount = newIterations
    } else {
      const cached = vm.sessionStates[sid]
      if (cached) {
        cached.sessionUsage = { ...cached.sessionUsage, ...patch }
        if (newDuration !== null) cached.sessionDuration = newDuration
        if (newIterations !== null) cached.iterationCount = newIterations
      }
    }
  }

  let replaying = false
  const checkpoint = createStreamCheckpoint(() => ({
    message: vm.messages.find(m => m.id === ctx.assistantMsgId) || null,
    usage: vm.sessionUsage, duration: vm.sessionDuration, iterations: vm.iterationCount,
  }), base => {
    const idx = findIdx()
    if (idx !== -1) {
      const message = base.message || { ...vm.messages[idx], content: '', thinking: '', tool_calls: [], blocks: [] }
      setReactive(vm.messages, idx, { ...message, loading: true })
    }
    Object.assign(vm.sessionUsage, base.usage)
    vm.sessionDuration = base.duration
    vm.iterationCount = base.iterations
  })

  const onChunk = (data) => {
    if (data.type === 'stream_connection') {
      if (!ctx.streamSessionId || vm.currentSessionId === ctx.streamSessionId) {
        if (data.status === 'reconnecting') vm.error = STREAM_RECONNECTING_MESSAGE
        else if (vm.error === STREAM_RECONNECTING_MESSAGE) vm.error = null
      }
      return
    }
    if (data.type === 'stream_replay') {
      checkpoint.restore()
      currentBlock = null
      currentThinking = ''
      currentContent = ''
      currentToolCalls = []
      blockOrderCounter = ctx.initialBlockOrder || 0
      replaying = true
      return
    }
    checkpoint.capture()
    const { type: eventType, content, duration, step, tool_name: toolName, tool_call_id: toolCallId, arguments: args, result, success } = data

    if (eventType === 'start') {
      if (!ctx.isResume) {
        vm.currentTodos = []
        // 仅「本次请求就是当前展示会话的新建」时才认领 session_id；
        // 后台会话（ctx.streamSessionId 已确定）的 start 不得把界面抢过来。
        if (data.session_id && !vm.currentSessionId && !ctx.streamSessionId) {
          vm.currentSessionId = data.session_id
          vm.loadedSessionId = data.session_id
          vm.loadSessions()
        }
        if (data.session_id) ctx.streamSessionId = data.session_id
        // 记录「本专家 -> 该会话」：新会话的 ID 只有在 start 事件里才拿到，所以关联写在
        // 这里而不是选专家的时刻（那时会话可能还不存在）。切回同一专家时用它回到原会话。
        if (data.session_id && ctx.expertId) {
          setExpertLastSession(ctx.expertId, data.session_id)
        }
        // 标记该会话已有本页流处理器：切回时按缓存直接显示，避免再起 attach 重复渲染
        if (data.session_id && !ctx.attachMode) {
          vm.$set(vm.attachedStreamingSessions, data.session_id, 'displayed')
        }
        vm.markStreaming(ctx.streamSessionId || vm.currentSessionId)
        if (data.session_id && !replaying) chatBus.$emit('stream-started', { sessionId: data.session_id, origin: vm._uid })
      }
    } else if (eventType === 'knowledge_evidence') {
      ensureMessage()
      const idx = findIdx()
      if (idx !== -1) setReactive(vm.messages, idx, {
        ...vm.messages[idx],
        knowledge_evidence: Array.isArray(data.evidence) ? data.evidence : [],
        knowledge_warnings: Array.isArray(data.warnings) ? data.warnings : [],
      })
    } else if (eventType === 'token_usage') {
      applyUsage(data, ctx.streamSessionId)
    } else if (eventType === 'thinking_start') {
      // 始终按 step 解析思考块，不再用「currentBlock 已是 thinking 则跳过」守卫：
      // 该守卫会在上一 step 的 currentBlock 未被 thinking_end 及时置空时跳过新 step
      // 的 thinking_start，导致后续 thinking 增量回退到上一步卡片，造成同一 step 的
      // 思考内容被拆分/串步渲染。按 step 查找：存在则复用（同 turn 思考分段重开），
      // 不存在则新建，保证每个 step 恰好一张思考卡片。
      // 第一步思考事件可能早于 content/tool_call 到达（此时 assistant 消息尚未创建），
      // 必须先 ensureMessage，否则 findIdx 返回 -1 导致首步思考被丢弃（历史能查到、实时不渲染）。
      ensureMessage()
      const idx = findIdx()
      if (idx !== -1) {
        const targetStep = step || 0
        const existing = vm.messages[idx].blocks.find(b => b.type === 'thinking' && b.step === targetStep)
        if (existing) {
          // 复用同 step 思考块：恢复已累积内容继续追加，不重置 duration 以避免卡片在
          //「思考过程」与「正在思考」间闪烁、也避免同一步骤思考被视觉上分成两段。
          currentThinking = existing.content || ''
          currentBlock = existing
          touchBlocks()
        } else {
          currentThinking = ''
          currentBlock = null
          addBlock('thinking', { content: '', step: targetStep })
        }
      }
    } else if (eventType === 'thinking') {
      const targetStep = step || 0
      // 优先用后端 full_content（本 step 思考完整内容）采用 SET 语义覆盖——幂等，
      // 重复/聚合增量也不会重复渲染；旧后端无 full_content 时回退到 append 增量。
      if (data.full_content) {
        currentThinking = data.full_content
      } else {
        currentThinking += content || ''
      }
      ensureMessage()
      const idx = findIdx()
      if (idx !== -1) {
        // 按 step 定位思考块写入；若该 step 尚无思考块（thinking_start 被跳过或事件
        // 乱序），按 step 新建一块，绝不回退到其他 step 的 currentBlock——否则会把
        // 本步思考写入上一步卡片（覆盖其内容），造成一个 step 的思考被拆分/错位。
        let blk = vm.messages[idx].blocks.find(b => b.type === 'thinking' && b.step === targetStep)
        if (!blk) {
          blk = addBlock('thinking', { content: '', step: targetStep })
          if (!data.full_content) currentThinking = content || ''
        }
        if (blk) {
          blk.content = currentThinking
          currentBlock = blk
          setReactive(vm.messages, idx, { ...vm.messages[idx] })
        }
      }
    } else if (eventType === 'thinking_end') {
      touchBlocks()
      if (ctx.isResume) {
        // 健壮地为对应思考块设置 duration。HITL 恢复流中，思考之后往往紧跟
        // tool_call/tool_result 事件，currentBlock 已被改写为工具块或 null，
        // 仅依赖 currentBlock 会导致思考块 duration 一直为 null（前端误显示
        // "正在思考…"）。因此优先按 step 匹配，再回退到 currentBlock 与最后
        // 一个无 duration 的思考块。
        const idx = findIdx()
        if (idx !== -1) {
          let blockIdx = vm.messages[idx].blocks.findIndex(b => b.type === 'thinking' && b.step === (step || 0))
          if (blockIdx === -1 && currentBlock && currentBlock.type === 'thinking') {
            blockIdx = vm.messages[idx].blocks.indexOf(currentBlock)
          }
          if (blockIdx === -1) {
            for (let i = vm.messages[idx].blocks.length - 1; i >= 0; i--) {
              if (vm.messages[idx].blocks[i].type === 'thinking') { blockIdx = i; break }
            }
          }
          if (blockIdx !== -1) {
            setReactive(vm.messages[idx].blocks, blockIdx, { ...vm.messages[idx].blocks[blockIdx], duration: duration || 0 })
            setReactive(vm.messages, idx, { ...vm.messages[idx], blocks: [...vm.messages[idx].blocks] })
          }
        }
      } else {
        updateThinkingDuration(duration || 0, step || 0)
      }
      currentThinking = ''
      currentBlock = null
    } else if (eventType === 'content') {
      const targetStep = step || 0
      // 正文块必须带 step，否则按 step 排序时会落到顶部（step=0）。同一 step 的
      // 正文分段到达（reopen）时复用已有正文块，避免同 step 产生多个正文块。
      if (!currentBlock || currentBlock.type !== 'content' || currentBlock.step !== targetStep) {
        const idx0 = findIdx()
        let existing = null
        if (idx0 !== -1) existing = vm.messages[idx0].blocks.find(b => b.type === 'content' && b.step === targetStep)
        if (existing) {
          currentContent = (existing.content || '') + (content || '')
          currentBlock = existing
        } else {
          currentContent = content || ''
          currentBlock = null
          addBlock('content', { content: currentContent, step: targetStep })
        }
      } else {
        currentContent += content || ''
      }
      if (currentBlock && currentBlock.type === 'content') currentBlock.content = currentContent
      const idx = findIdx()
      if (idx !== -1) {
        if (ctx.isResume) vm.messages[idx].content = (vm.messages[idx].content || '') + (content || '')
        touchBlocks()
      }
    } else if (eventType === 'content_end') {
      currentContent = ''
      currentBlock = null
    } else if (eventType === 'todo_list') {
      if (data.todos && Array.isArray(data.todos)) vm.currentTodos = data.todos
    } else if (eventType === 'tool_call_delta') {
      // 大参数工具（write_file/edit_file）的 arguments 分片快照：先把当前快照合入
      // 工具卡片，完整参数随后由 tool_call 事件权威覆盖（同一 tool_call_id）。
      if (!ctx.isResume) ensureMessage()
      const idx = findIdx()
      if (idx !== -1) {
        const callId = toolCallId || `tool-${toolName}`
        const blockIdx = vm.messages[idx].blocks.findIndex(b => b.type === 'tool_call' && (b.id === callId || b.tool_call_id === callId))
        if (blockIdx !== -1) {
          const prev = vm.messages[idx].blocks[blockIdx]
          setReactive(vm.messages[idx].blocks, blockIdx, { ...prev, arguments: args || prev.arguments || {}, streaming: true })
        } else {
          // 必须先清空 currentBlock：否则 addBlock 会把本次调用并进上一个 tool_call 块。
          currentBlock = null
          addBlock('tool_call', { id: callId, tool_name: toolName || '', arguments: args || {}, result: '', success: true, streaming: true, step: step || 0 })
        }
        touchBlocks()
      }
    } else if (eventType === 'tool_call') {
      if (!ctx.isResume) ensureMessage()
      const idx = findIdx()
      if (idx !== -1) {
        const callId = toolCallId || `tool-${toolName}`
        const existingBlockIdx = vm.messages[idx].blocks.findIndex(b => b.type === 'tool_call' && (b.id === callId || b.tool_call_id === callId))
        if (existingBlockIdx !== -1) {
          // tool_call 是权威参数：覆盖 delta 快照并清掉「写入中」标记。
          const prev = vm.messages[idx].blocks[existingBlockIdx]
          setReactive(vm.messages[idx].blocks, existingBlockIdx, { ...prev, arguments: args || {}, streaming: false })
          currentBlock = vm.messages[idx].blocks[existingBlockIdx]
          touchBlocks()
        } else {
          currentBlock = null
          if (!ctx.isResume) currentToolCalls.push({ tool_call_id: callId, tool_name: toolName || '', arguments: args || {}, result: '', success: true })
          addBlock('tool_call', { id: callId, tool_name: toolName || '', arguments: args || {}, result: '', success: true, step: step || 0 })
        }
      }
    } else if (eventType === 'tool_result') {
      const callId = toolCallId || `tool-${toolName}`
      const toolDuration = duration != null ? duration : 0
      if (!ctx.isResume && currentToolCalls.length > 0) {
        const matchingCall = currentToolCalls.find(tc => tc.tool_call_id === callId)
        if (matchingCall) {
          matchingCall.result = result || ''
          matchingCall.success = success !== false
          matchingCall.duration = toolDuration
          if (args) matchingCall.arguments = args
        }
      }
      const idx = findIdx()
      if (idx !== -1) {
        const blockIdx = vm.messages[idx].blocks.findIndex(b => b.type === 'tool_call' && (b.id === callId || b.tool_call_id === callId))
        if (blockIdx !== -1) {
          const blk = { ...vm.messages[idx].blocks[blockIdx], arguments: args || vm.messages[idx].blocks[blockIdx].arguments, result: parseMCPResult(result || ''), success: success !== false, duration: toolDuration }
          if (ctx.isResume) blk.loading = false
          setReactive(vm.messages[idx].blocks, blockIdx, blk)
          touchBlocks()
        }
      }
      if (!currentBlock || currentBlock.type !== 'content') currentBlock = null
    } else if (eventType === 'approval_required') {
      if (ctx.onApprovalRequired) ctx.onApprovalRequired(data)
    } else if (eventType === 'done') {
      const idx = findIdx()
      if (idx !== -1) {
        vm.messages[idx].loading = false
        vm.messages[idx].pending_approval = null
        vm.pendingApproval = null
        vm.messages[idx].created_at = new Date().toISOString()
        if (ctx.isResume) {
          if (Array.isArray(data.blocks) && data.blocks.length > 0) vm.messages[idx].blocks = data.blocks.map(b => ({ ...b }))
          for (const b of vm.messages[idx].blocks) { if (b.type === 'thinking' && b.duration == null) b.duration = 0 }
        } else {
          // done 事件不下发 content 字段：从正文块拼回完整正文，供复制/引用等
          // 仍读 message.content 的路径使用（气泡渲染本身以 blocks 为准）。
          vm.messages[idx].content = (vm.messages[idx].blocks || [])
            .filter(b => b.type === 'content')
            .map(b => b.content || '')
            .join('\n')
          // SET 语义下 currentThinking 只剩最后一个 step 的内容；此处从所有思考块
          // 拼接出完整 thinking，保证 message.thinking 字段（旧数据回退/兼容）正确。
          vm.messages[idx].thinking = (vm.messages[idx].blocks || [])
            .filter(b => b.type === 'thinking')
            .map(b => b.content || '')
            .join('')
          vm.messages[idx].tool_calls = currentToolCalls
        }
        if (data.usage) applyUsage({
          ...data.usage,
          elapsed_time: typeof data.elapsed_time === 'number' ? data.elapsed_time : data.usage.elapsed_time,
        }, ctx.streamSessionId)
        // 兜底：未收到完整参数的 streaming 占位块在流结束时清除标记，
        // 避免工具卡片一直停留在「写入中…」。
        for (const blk of vm.messages[idx].blocks || []) {
          if (blk.streaming) blk.streaming = false
        }
        setReactive(vm.messages, idx, { ...vm.messages[idx] })
      }
      // 后端 done 不带 title：会话标题由服务端生成，流结束后刷新列表回填。
      if (!ctx.isResume && typeof vm.loadSessions === 'function') vm.loadSessions()
    } else if (eventType === 'error') {
      // 超时兜底与普通报错的分流逻辑在 utils/streamErrorFallback.js，
      // 与主页面的 pageChunkHandler.js 共用同一份实现。
      // 返回非空才写全局 error：超时在页内显示常驻灰条，不弹框。
      const globalError = applyStreamError({
        messages: vm.messages,
        index: findIdx(),
        data,
        isResume: ctx.isResume
      })
      if (globalError) vm.error = globalError
    }
  }

  // 事件按「所属会话」路由：后台会话写入其自身缓冲，不污染当前展示
  return { onChunk: (data) => vm.runInSession(ctx.streamSessionId, ctx, () => onChunk(data)) }
}
