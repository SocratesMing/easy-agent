// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/chat/freezeMessage.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
function deepFreeze(value) {
  if (!value || typeof value !== 'object') return value
  if (Object.isFrozen(value)) return value
  Object.freeze(value)
  if (Array.isArray(value)) {
    for (let i = 0; i < value.length; i++) {
      deepFreeze(value[i])
    }
  } else {
    const keys = Object.keys(value)
    for (let i = 0; i < keys.length; i++) {
      deepFreeze(value[keys[i]])
    }
  }
  return value
}

/**
 * 冻结已完成消息中体积较大的只读嵌套结构（工具参数、引用证据、子智能体步骤等），
 * 跳过 Vue2 Observer 对深层静态对象的递归响应式劫持。
 * 外层消息对象本身及正在流式写入的块保持可变，避免状态更新或审批关闭时抛错。
 */
export function freezeReadonlyMessageParts(msg) {
  if (!msg || typeof msg !== 'object') return msg
  if (msg.knowledge_evidence) deepFreeze(msg.knowledge_evidence)
  if (msg.knowledge_warnings) deepFreeze(msg.knowledge_warnings)
  if (msg.arguments && typeof msg.arguments === 'object') deepFreeze(msg.arguments)
  if (Array.isArray(msg.tool_calls)) deepFreeze(msg.tool_calls)
  if (Array.isArray(msg.blocks)) {
    for (let i = 0; i < msg.blocks.length; i++) {
      const blk = msg.blocks[i]
      if (!blk || typeof blk !== 'object' || blk.streaming) continue
      if (blk.arguments && typeof blk.arguments === 'object') deepFreeze(blk.arguments)
      if (!msg.loading && Array.isArray(blk.subagent_steps)) deepFreeze(blk.subagent_steps)
    }
  }
  return msg
}

export function freezeHistoryMessages(messages) {
  if (!Array.isArray(messages)) return messages
  for (let i = 0; i < messages.length; i++) {
    const m = messages[i]
    if (m && !m.loading) freezeReadonlyMessageParts(m)
  }
  return messages
}

/**
 * 生成 O(1) 的消息列表末项修订键，用于替代 Chat.vue 中对整个 messages 数组的 deep watch。
 */
export function lastMessageRevisionKey(messages) {
  if (!Array.isArray(messages) || messages.length === 0) return '0'
  const len = messages.length
  const last = messages[len - 1]
  if (!last || typeof last !== 'object') return `${len}`
  const blocks = Array.isArray(last.blocks) ? last.blocks : null
  const bLen = blocks ? blocks.length : 0
  const lastBlk = bLen > 0 ? blocks[bLen - 1] : null
  const blkKey = lastBlk
    ? `${lastBlk.type || ''}:${lastBlk.id || ''}:${lastBlk.streaming ? 1 : 0}:${lastBlk.duration ?? ''}:${lastBlk.content ? lastBlk.content.length : 0}:${Array.isArray(lastBlk.subagent_steps) ? lastBlk.subagent_steps.length : 0}:${typeof lastBlk.result === 'string' ? lastBlk.result.length : 0}`
    : ''
  return `${len}|${last.id || ''}|${last.loading ? 1 : 0}|${last.pending_approval ? 1 : 0}|${last.content ? last.content.length : 0}|${last.thinking ? last.thinking.length : 0}|${bLen}|${blkKey}`
}
