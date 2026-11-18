import { backendErrorMessage } from '../utils/apiError.js'

const object = value => value !== null && typeof value === 'object' && !Array.isArray(value)
const terminal = value => ['compacted', 'skipped'].includes(value)
const recordValid = value => object(value) && typeof value.id === 'string' && !!value.id
  && terminal(value.status) && typeof value.message === 'string'
  && Number.isInteger(value.anchor_message_count) && value.anchor_message_count >= 0
  && Number.isFinite(value.after_tokens) && value.after_tokens >= 0
  && Number.isFinite(value.context_length) && value.context_length > 0

function invalidResponse(status) {
  const error = new Error('暂时无法获取压缩结果，请稍后重试')
  error.status = status
  error.code = 'INVALID_CONTEXT_RESPONSE'
  return error
}

// HTTP 成功不代表拿到了业务结果；只有校验通过才能清除 pending 请求或解除锁定。
export function readContextResponse(data, status, sessionId, requestId) {
  if (status >= 400) {
    const fallback = '压缩请求失败，请稍后重试'
    const message = backendErrorMessage(data, fallback)
    const error = message === fallback ? invalidResponse(status) : new Error(message)
    error.status = status
    throw error
  }
  if (!object(data) || data.session_id !== sessionId) throw invalidResponse(status)
  if (requestId !== undefined) {
    if (!recordValid(data.compaction) || data.compaction.id !== requestId
      || data.status !== data.compaction.status) throw invalidResponse(status)
    return { ...data, compaction: cleanRecord(data.compaction) }
  }
  if (typeof data.supported !== 'boolean' || typeof data.busy !== 'boolean' || typeof data.reason !== 'string'
    || !Array.isArray(data.compactions) || !data.compactions.every(recordValid)) throw invalidResponse(status)
  return { ...data, compactions: data.compactions.map(cleanRecord) }
}

function cleanRecord(record) {
  return { ...record, message: backendErrorMessage({ detail: record.message }, '压缩结果已更新') }
}
