/**
 * 流式 error 事件的展示分流（超时兜底 / 普通报错）。
 *
 * 后端在 error 帧里带 `code`：
 *   - 超时类异常（模型单次调用长时间收不到新 chunk）→ `code === 'timeout'`；
 *   - 其余异常（步数用尽、工具失败等）→ 不带 code。
 *
 * 超时场景下，半成品正文与兜底文案已由后端落库，前端只需要在消息上挂
 * `fallback_note`，由 ChatMessage 在正文后渲染常驻灰条。绝不能写
 * `message.error` 或全局 error —— 那会渲染成粉底红字条 + 页面底部弹窗，
 * 与「在页面内提示、不弹框」的产品要求冲突。
 *
 * 普通异常的展示保持原样：红字 + 全局提示，行为不变。
 */

/** 后端表示「模型流式静默超时」的 error code */
export const STREAM_TIMEOUT_CODE = 'timeout'

/** 超时中断时，未完成工具卡（tool_call）的 result 文案 */
export const STREAM_TIMEOUT_INTERRUPT_TEXT = '执行中断: 访问超时'

/** 普通异常的兜底文案（后端未给 content 时使用） */
const FALLBACK_ERROR_TEXT = '处理失败'

/**
 * 把 error 事件落到目标消息上，并返回需要写入全局 error 的文案。
 *
 * 四个流入口共用本函数，响应式写入由宿主注入。
 *
 * @param {Object}   options
 * @param {Array}    options.messages 消息数组（Vue2 需走 setReactive 才触发渲染）
 * @param {number}   options.index    目标消息下标，-1 表示未找到
 * @param {Object}   options.data     后端 error 帧（content / code）
 * @param {boolean}  options.isResume 是否为 HITL 恢复流（恢复流不动进行中的块状态）
 * @param {Function} setReactive 宿主的响应式写入函数
 * @returns {string|null} 需要写进全局 error 的文案；null = 只做页内提示，不弹窗
 */
export function applyStreamError({ messages, index, data, isResume }, setReactive) {
  const timedOut = data.code === STREAM_TIMEOUT_CODE
  const content = data.content || FALLBACK_ERROR_TEXT

  if (index !== -1 && messages[index]) {
    const msg = messages[index]
    msg.loading = false
    if (!timedOut) msg.error = content

    // 恢复流沿用后端已落库的块状态，不再本地补 duration / success
    if (!isResume) {
      for (const blk of msg.blocks) {
        if (blk.type === 'thinking' && blk.duration == null) blk.duration = 0
        if (blk.type === 'tool_call' && blk.duration == null) {
          blk.duration = 0
          blk.success = false
          if (!blk.result) blk.result = timedOut ? STREAM_TIMEOUT_INTERRUPT_TEXT : (data.content || '执行中断')
        }
      }
    }

    // 新增键必须放进「被替换的对象」里：Vue2 直接给原对象添属性不会触发视图更新
    const patch = { ...msg, blocks: [...msg.blocks] }
    if (timedOut && data.content) patch.fallback_note = data.content
    setReactive(messages, index, patch)
  }

  // 超时不写全局 error，避免页面底部弹出错误条
  return timedOut ? null : content
}
