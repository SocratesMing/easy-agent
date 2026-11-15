// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/chat/planConfirmationTurn.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
import { buildConfirmationUserMessage } from './planInteraction.js'

/** Split the pending assistant into a completed card, a user confirmation, and a new reply. */
export function buildPlanContinuationTurn (messages, messageIndex, confirmation) {
  const original = (messages || [])[messageIndex]
  if (!original || original.role !== 'assistant') return null
  const confirmationMessage = buildConfirmationUserMessage(confirmation)
  if (!confirmationMessage) return null
  const resumeAssistantId = `assistant-${Date.now()}-continuation`
  const completed = {
    ...original,
    loading: false,
    created_at: new Date().toISOString(),
    pending_approval: null,
    blocks: [...(original.blocks || [])],
  }
  const continuation = {
    id: resumeAssistantId,
    role: 'assistant',
    content: '',
    created_at: null,
    thinking: '',
    tool_calls: [],
    blocks: [],
    loading: true,
    expert: original.expert || null,
  }
  return {
    completed,
    confirmationMessage,
    continuation,
    replacement: [completed, confirmationMessage, continuation],
    resumeAssistantId,
    initialBlockOrder: 0,
  }
}
