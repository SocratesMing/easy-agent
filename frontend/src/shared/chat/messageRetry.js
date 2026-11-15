// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/chat/messageRetry.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
import { normalizeInteractionMode } from './planInteraction.js'

/** Display-only rows are persisted for history rendering, not for replaying a user turn. */
export function canRetryUserMessage(message) {
  return !!message &&
    message.role === 'user' &&
    message.display_only !== true &&
    String(message.content || '').trim().length > 0
}

/** Build a retry request that preserves the original turn's interaction mode. */
export function buildUserRetryRequest(message) {
  if (!canRetryUserMessage(message)) return null
  return {
    content: String(message.content || ''),
    interactionMode: normalizeInteractionMode(message.interaction_mode),
  }
}
