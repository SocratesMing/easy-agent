/**
 * SSE 流式事件 → 消息 blocks 的状态机（2026-10-20 从 App.vue 原样抽出，行为不变）。
 *
 * 为什么单独成文件：这段逻辑原先内联在 App.vue（441 行），既无法单测，
 * 也让 App.vue 同时承担布局、鉴权、模型列表与流式状态机。
 *
 * 约定：
 * - `messages` 由调用方传入（App.vue 的响应式数组）；本模块不持有全局状态；
 * - `ctx` 携带本次流的上下文（assistantMsgId、streamSessionId、回调等），
 *   字段与原内联实现完全一致，便于逐步收敛成显式参数。
 * - 每个流创建一个 handler 实例，实例内部闭包保存 currentBlock 等中间状态。
 */

export function createStreamChunkHandler(messages, ctx) {
  let currentBlock = null
  let currentThinking = ''
  let currentContent = ''
  let currentToolCalls = []
  let blockOrderCounter = ctx.initialBlockOrder || 0
  let totalThinkingDuration = 0

  function findIdx() {
    return messages.value.findIndex(m => m.id === ctx.assistantMsgId)
  }
  function touchBlocks() {
    const idx = findIdx()
    if (idx !== -1) messages.value[idx] = { ...messages.value[idx], blocks: [...messages.value[idx].blocks] }
  }
  function ensureMessage() {
    if (ctx.ensureMessage) ctx.ensureMessage()
  }
  function addBlock(type, data, replace = false) {
    ensureMessage()
    const idx = findIdx()
    if (idx === -1) return null
    if (type === 'thinking' && data.step !== undefined) {
      const existing = messages.value[idx].blocks.find(b => b.type === 'thinking' && b.step === data.step)
      if (existing) { currentBlock = existing; return currentBlock }
    }
    blockOrderCounter++
    // 严格按 order 展示时，reopen 场景（late reasoning 晚于同 step 工具块到达）会让
    // 思考块的 order 大于工具块而排到工具之后。此时给思考块一个更小的 order
    //（同 step 最小 order - 0.5），使其排在工具之前，符合「先思考后工具」。
    let blkOrder = blockOrderCounter
    if (type === 'thinking' && data.step !== undefined) {
      const sameStep = messages.value[idx].blocks.filter(b => b.step === data.step && b.type !== 'thinking')
      if (sameStep.length > 0) {
        blkOrder = Math.min(...sameStep.map(b => b.order || 0)) - 0.5
      }
    }
    const needNewBlock = !currentBlock || currentBlock.type !== type ||
      (type === 'thinking' && data.step !== undefined && currentBlock.step !== data.step)
    if (needNewBlock) {
      currentBlock = { type, content: '', order: blkOrder, ...data }
      messages.value[idx].blocks.push(currentBlock)
      messages.value[idx] = { ...messages.value[idx], blocks: [...messages.value[idx].blocks] }
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
      messages.value[idx] = { ...messages.value[idx] }
    }
    return currentBlock
  }
  function updateThinkingDuration(duration, step) {
    const idx = findIdx()
    if (idx !== -1) {
      const blockIdx = messages.value[idx].blocks.findIndex(b => b.type === 'thinking' && b.step === step)
      if (blockIdx !== -1) {
        messages.value[idx].blocks[blockIdx] = { ...messages.value[idx].blocks[blockIdx], duration }
      }
      messages.value[idx] = { ...messages.value[idx], thinking_duration: duration, blocks: [...messages.value[idx].blocks] }
    }
  }
  function usagePatchFrom(data) {
    const patch = {
      input_tokens: data.input_tokens || 0,
      output_tokens: data.output_tokens || 0,
      reasoning_tokens: data.reasoning_tokens || 0,
      context_tokens: data.context_tokens || sessionUsage.value.context_tokens || 0,
    }
    if (data.context_length) patch.context_length = data.context_length
    if (data.auto_compress_tokens) patch.auto_compress_tokens = data.auto_compress_tokens
    return patch
  }
  function applyUsage(data, sid) {
    const patch = usagePatchFrom(data)
    const match = !sid || currentSessionId.value === sid
    if (ctx.attachMode) {
      // 重挂载回放：token_usage/done 携带的 elapsed_time 是原始流从头开始的
      // 累计值，应 SET 而非累加（isResume 分支的累加语义只适用于 HITL 续跑）
      if (match) {
        Object.assign(sessionUsage.value, patch)
        if (typeof data.elapsed_time === 'number') sessionDuration.value = Math.round(data.elapsed_time * 10) / 10
        if (typeof data.step_count === 'number') iterationCount.value = data.step_count
      } else {
        const cached = sessionStates.value[sid]
        if (cached) {
          cached.sessionUsage = { ...cached.sessionUsage, ...patch }
          if (typeof data.elapsed_time === 'number') cached.sessionDuration = Math.round(data.elapsed_time * 10) / 10
          if (typeof data.step_count === 'number') cached.iterationCount = data.step_count
        }
      }
      return
    }
    if (ctx.isResume) {
      if (match) {
        Object.assign(sessionUsage.value, patch)
        if (typeof data.elapsed_time === 'number') sessionDuration.value = Math.round((sessionDuration.value + data.elapsed_time) * 10) / 10
        if (typeof data.step_count === 'number') iterationCount.value = data.step_count
      } else {
        const cached = sessionStates.value[sid]
        if (cached) {
          cached.sessionUsage = { ...cached.sessionUsage, ...patch }
          if (typeof data.elapsed_time === 'number') cached.sessionDuration = Math.round(((cached.sessionDuration || 0) + data.elapsed_time) * 10) / 10
          if (typeof data.step_count === 'number') cached.iterationCount = data.step_count
        }
      }
    } else {
      const newDuration = typeof data.elapsed_time === 'number' ? Math.round((ctx.preStreamDuration + data.elapsed_time) * 10) / 10 : null
      const newIterations = typeof data.step_count === 'number' ? ctx.preStreamIterationCount + data.step_count : null
      if (match) {
        Object.assign(sessionUsage.value, patch)
        if (newDuration !== null) sessionDuration.value = newDuration
        if (newIterations !== null) iterationCount.value = newIterations
      } else {
        const cached = sessionStates.value[sid]
        if (cached) {
          cached.sessionUsage = { ...cached.sessionUsage, ...patch }
          if (newDuration !== null) cached.sessionDuration = newDuration
          if (newIterations !== null) cached.iterationCount = newIterations
        }
      }
    }
  }

  function onChunk(data) {
    const { type: eventType, content, duration, step, tool_name, tool_call_id: toolCallId, arguments: args, result, success, title } = data
    const sid = ctx.streamSessionId

    if (eventType === 'start') {
      if (!ctx.isResume) {
        currentTodos.value = []
        // 仅「本次请求就是当前展示会话的新建」时才认领 session_id；
        // 后台会话（ctx.streamSessionId 已确定）的 start 不得把界面抢过来。
        if (data.session_id && !currentSessionId.value && !ctx.streamSessionId) {
          currentSessionId.value = data.session_id
          loadedSessionId.value = data.session_id
          loadSessions()
        }
        if (data.session_id) ctx.streamSessionId = data.session_id
        // 标记该会话已有本页流处理器：切回时直接按缓存显示，避免再起一个 attach
        // 处理器（会重复渲染同一段流）——send 链路专有，attach 链路由自身管理。
        if (data.session_id && !ctx.attachMode) {
          attachedStreamingSessions.value[data.session_id] = 'displayed'
        }
        markStreaming(ctx.streamSessionId || currentSessionId.value)
      }
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
        const existing = messages.value[idx].blocks.find(b => b.type === 'thinking' && b.step === targetStep)
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
        let blk = messages.value[idx].blocks.find(b => b.type === 'thinking' && b.step === targetStep)
        if (!blk) {
          blk = addBlock('thinking', { content: '', step: targetStep })
          if (!data.full_content) currentThinking = content || ''
        }
        if (blk) {
          blk.content = currentThinking
          currentBlock = blk
          messages.value[idx] = { ...messages.value[idx] }
        }
      }
    } else if (eventType === 'thinking_end') {
      touchBlocks()
      totalThinkingDuration += duration || 0
      if (ctx.isResume) {
        // 健壮地为对应思考块设置 duration。HITL 恢复流中，思考之后往往紧跟
        // tool_call/tool_result 事件，currentBlock 已被改写为工具块或 null，
        // 仅依赖 currentBlock 会导致思考块 duration 一直为 null（前端误显示
        // “正在思考…”）。因此优先按 step 匹配，再回退到 currentBlock 与最后
        // 一个无 duration 的思考块。
        const idx = findIdx()
        if (idx !== -1) {
          let blockIdx = messages.value[idx].blocks.findIndex(b => b.type === 'thinking' && b.step === (step || 0))
          if (blockIdx === -1 && currentBlock && currentBlock.type === 'thinking') {
            blockIdx = messages.value[idx].blocks.indexOf(currentBlock)
          }
          if (blockIdx === -1) {
            for (let i = messages.value[idx].blocks.length - 1; i >= 0; i--) {
              if (messages.value[idx].blocks[i].type === 'thinking') { blockIdx = i; break }
            }
          }
          if (blockIdx !== -1) {
            messages.value[idx].blocks[blockIdx] = { ...messages.value[idx].blocks[blockIdx], duration: duration || 0 }
            messages.value[idx] = { ...messages.value[idx], blocks: [...messages.value[idx].blocks] }
          }
        }
      } else {
        updateThinkingDuration(duration || 0, step || 0)
      }
      currentThinking = ''
      currentBlock = null
    } else if (eventType === 'content_start') {
      if (!currentBlock || currentBlock.type !== 'content') {
        currentContent = ''
        currentBlock = null
        addBlock('content', { content: '', step: step || 0 })
      }
    } else if (eventType === 'content') {
      const targetStep = step || 0
      // 正文块必须带 step，否则按 step 排序时会落到顶部（step=0）。同一 step 的
      // 正文分段到达（reopen）时复用已有正文块，避免同 step 产生多个正文块。
      if (!currentBlock || currentBlock.type !== 'content' || currentBlock.step !== targetStep) {
        const idx0 = findIdx()
        let existing = null
        if (idx0 !== -1) existing = messages.value[idx0].blocks.find(b => b.type === 'content' && b.step === targetStep)
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
        if (ctx.isResume) messages.value[idx].content = (messages.value[idx].content || '') + (content || '')
        touchBlocks()
      }
    } else if (eventType === 'content_end') {
      currentContent = ''
      currentBlock = null
    } else if (eventType === 'todo_list') {
      if (data.todos && Array.isArray(data.todos)) currentTodos.value = data.todos
    } else if (eventType === 'assistant_start') {
      if (!ctx.isResume) {
        ensureMessage()
        currentContent = ''
        currentThinking = ''
        currentToolCalls = []
        currentBlock = null
      }
    } else if (eventType === 'user_input_required') {
      if (!ctx.isResume) {
        const idx = findIdx()
        if (idx !== -1) { messages.value[idx].loading = false; messages.value[idx] = { ...messages.value[idx] } }
      }
    } else if (eventType === 'tool_call_delta') {
      // 大参数工具（write_file/edit_file）的 arguments 分片快照：模型还在生成
      // 文件内容时就先把当前快照合入对应工具卡片，diff 实时增长；完整参数随后由
      // tool_call 事件权威覆盖（同一 tool_call_id）。
      if (!ctx.isResume) ensureMessage()
      const idx = findIdx()
      if (idx !== -1) {
        const callId = toolCallId || `tool-${tool_name}`
        const existingBlockIdx = messages.value[idx].blocks.findIndex(b => b.type === 'tool_call' && (b.id === callId || b.tool_call_id === callId))
        if (existingBlockIdx !== -1) {
          const prev = messages.value[idx].blocks[existingBlockIdx]
          messages.value[idx].blocks[existingBlockIdx] = { ...prev, arguments: args || prev.arguments || {}, streaming: true }
        } else {
          addBlock('tool_call', { id: callId, tool_name: tool_name || '', arguments: args || {}, result: '', success: true, step: step || 0, streaming: true })
          if (!ctx.isResume) currentToolCalls.push({ tool_call_id: callId, tool_name: tool_name || '', arguments: args || {}, result: '', success: true })
        }
        touchBlocks()
      }
    } else if (eventType === 'tool_call') {
      if (!ctx.isResume) ensureMessage()
      const idx = findIdx()
      if (idx !== -1) {
        const callId = toolCallId || `tool-${tool_name}`
        let existingBlockIdx = messages.value[idx].blocks.findIndex(b => b.type === 'tool_call' && (b.id === callId || b.tool_call_id === callId))
        if (existingBlockIdx === -1) {
          // 参数分片阶段若无 id（部分 provider 首个分片才带 id），临时块的 key 是
          // `tool-<name>`；此处按工具名回退匹配仍在 streaming 的占位块，避免重复卡片。
          existingBlockIdx = messages.value[idx].blocks.findIndex(b => b.type === 'tool_call' && b.streaming && b.tool_name === tool_name)
        }
        if (existingBlockIdx !== -1) {
          const prev = messages.value[idx].blocks[existingBlockIdx]
          // 参数分片阶段的临时块：用权威参数覆盖，补齐 id/step，并清掉 streaming 标记。
          messages.value[idx].blocks[existingBlockIdx] = { ...prev, id: toolCallId || prev.id, tool_call_id: toolCallId || prev.tool_call_id, arguments: args || prev.arguments || {}, step: step || prev.step || 0, streaming: false }
          if (!ctx.isResume) {
            const tracked = currentToolCalls.find(tc => tc.tool_call_id === callId)
            if (tracked) {
              tracked.arguments = args || tracked.arguments
              tracked.tool_name = tool_name || tracked.tool_name
            } else {
              currentToolCalls.push({ tool_call_id: callId, tool_name: tool_name || '', arguments: args || {}, result: '', success: true })
            }
          }
          currentBlock = messages.value[idx].blocks[existingBlockIdx]
          touchBlocks()
        } else {
          currentBlock = null
          if (!ctx.isResume) currentToolCalls.push({ tool_call_id: callId, tool_name: tool_name || '', arguments: args || {}, result: '', success: true })
          addBlock('tool_call', { id: callId, tool_name: tool_name || '', arguments: args || {}, result: '', success: true, step: step || 0 })
        }
      }
    } else if (eventType === 'tool_result') {
      const callId = toolCallId || `tool-${tool_name}`
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
        let blockIdx = messages.value[idx].blocks.findIndex(b => b.type === 'tool_call' && (b.id === callId || b.tool_call_id === callId))
        if (blockIdx === -1) {
          blockIdx = messages.value[idx].blocks.findIndex(b => b.type === 'tool_call' && b.streaming && b.tool_name === tool_name)
        }
        if (blockIdx !== -1) {
          const blk = { ...messages.value[idx].blocks[blockIdx], arguments: args || messages.value[idx].blocks[blockIdx].arguments, result: parseMCPResult(result || ''), success: success !== false, duration: toolDuration, streaming: false }
          if (ctx.isResume) blk.loading = false
          messages.value[idx].blocks[blockIdx] = blk
          touchBlocks()
        }
      }
      if (!currentBlock || currentBlock.type !== 'content') currentBlock = null
    } else if (eventType === 'approval_required') {
      if (ctx.onApprovalRequired) ctx.onApprovalRequired(data)
    } else if (eventType === 'done') {
      const idx = findIdx()
      if (idx !== -1) {
        messages.value[idx].loading = false
        messages.value[idx].pending_approval = null
        pendingApproval.value = null
        messages.value[idx].created_at = new Date().toISOString()
        // 兜底：任何未收到完整参数的 streaming 占位块在流结束时清掉标记，
        // 避免工具卡片一直停留在「写入中…」。
        for (const b of messages.value[idx].blocks || []) { if (b.streaming) b.streaming = false }
        if (ctx.isResume) {
          if (Array.isArray(data.blocks) && data.blocks.length > 0) messages.value[idx].blocks = data.blocks.map(b => ({ ...b }))
          for (const b of messages.value[idx].blocks) { if (b.type === 'thinking' && b.duration == null) b.duration = 0 }
          if (data.usage) applyUsage(data.usage, ctx.streamSessionId)
        } else {
          const finalContent = data.content || currentContent
          messages.value[idx].content = finalContent
          // SET 语义下 currentThinking 只剩最后一个 step 的内容；此处从所有思考块
          // 拼接出完整 thinking，保证 message.thinking 字段（旧数据回退/兼容）正确。
          messages.value[idx].thinking = (messages.value[idx].blocks || [])
            .filter(b => b.type === 'thinking')
            .map(b => b.content || '')
            .join('')
          messages.value[idx].tool_calls = currentToolCalls
          if (data.usage) {
            messages.value[idx].usage = { input_tokens: data.usage.input_tokens || 0, output_tokens: data.usage.output_tokens || 0, reasoning_tokens: data.usage.reasoning_tokens || 0 }
          }
          if (data.usage) {
            const patch = usagePatchFrom(data.usage)
            const match = !ctx.streamSessionId || currentSessionId.value === ctx.streamSessionId
            const finalDuration = typeof data.elapsed_time === 'number' ? Math.round((ctx.preStreamDuration + data.elapsed_time) * 10) / 10 : null
            const finalIterations = data.usage && typeof data.usage.step_count === 'number' ? ctx.preStreamIterationCount + data.usage.step_count : null
            if (match) {
              Object.assign(sessionUsage.value, patch)
              if (finalDuration !== null) sessionDuration.value = finalDuration
              if (finalIterations !== null) iterationCount.value = finalIterations
            } else {
              const cached = sessionStates.value[ctx.streamSessionId]
              if (cached) {
                cached.sessionUsage = { ...cached.sessionUsage, ...patch }
                if (finalDuration !== null) cached.sessionDuration = finalDuration
                if (finalIterations !== null) cached.iterationCount = finalIterations
              }
            }
          }
        }
        messages.value[idx] = { ...messages.value[idx] }
      }
      if (!ctx.isResume && title) {
        const existingIdx = sessions.value.findIndex(s => s.session_id === currentSessionId.value)
        if (existingIdx === -1) {
          sessions.value = [{ session_id: currentSessionId.value, title, created_at: new Date().toISOString(), message_count: messages.value.length }, ...sessions.value]
        } else {
          sessions.value[existingIdx] = { ...sessions.value[existingIdx], title }
          sessions.value = [...sessions.value]
        }
      }
    } else if (eventType === 'error') {
      const idx = findIdx()
      if (idx !== -1) {
        messages.value[idx].loading = false
        messages.value[idx].error = data.content || '处理失败'
        if (!ctx.isResume) {
          for (const blk of messages.value[idx].blocks) {
            if (blk.type === 'thinking' && blk.duration == null) blk.duration = 0
            if (blk.type === 'tool_call' && blk.duration == null) { blk.duration = 0; blk.success = false; if (!blk.result) blk.result = data.content || '执行中断' }
          }
        }
        messages.value[idx] = { ...messages.value[idx], blocks: [...messages.value[idx].blocks] }
      }
      error.value = data.content || '处理失败'
    }
  }

  // 事件按「所属会话」路由：后台会话写入其自身缓冲，不污染当前展示
  return { onChunk: (data) => runInSession(ctx.streamSessionId, ctx, () => onChunk(data)) }
}
