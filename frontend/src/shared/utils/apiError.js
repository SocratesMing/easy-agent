// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/utils/apiError.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
// JSON 接口只展示结构化业务错误，网关页面和任意字符串正文不能作为提示。
export function backendErrorMessage(payload, fallback) {
  const detail = payload && typeof payload === 'object' && !Array.isArray(payload) ? payload.detail : null
  const message = typeof detail === 'string' ? detail : detail?.message
  if (typeof message !== 'string' || !message.trim() || message.length > 500
    || /<[^>]*>|<!doctype|&lt;\/?[a-z]/i.test(message)) return fallback
  return message
}
