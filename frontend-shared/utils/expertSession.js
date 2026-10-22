/**
 * 专家 ↔ 会话的关联表（localStorage，按专家维度存最近一次会话）。
 *
 * 为什么需要：会话上下文（历史消息、会话记忆 memory.md、摘要、工作区交付物）都绑定在
 * **会话**上，而一个会话只应属于一个专家——同一会话里换专家，上一个专家的对话历史与
 * 记忆规则会继续注入给新专家（实测：先问利率债专家，再切 Java 专家问汇率，模型照着
 * 利率债的"仅做利率债、汇率拒答"记忆拒答，并照抄了历史里的专家名）。
 *
 * 因此约定「换专家 = 换会话」，但不能简单粗暴地每次新建：用户切回某个专家时应该回到
 * 他上次跟这个专家聊的会话，这样对话记录、交付物、会话记忆都不丢。这里只存
 * `专家 ID -> 最近会话 ID` 的映射；会话是否还存在由调用方用会话列表校验
 * （localStorage 里的 ID 可能已被用户删除）。
 *
 * 存储格式：`easy_agent_expert_session_{export_agent_id}` = `{session_id}`。
 * 源文件 frontend-shared/utils/expertSession.js，由 scripts/dev/sync-frontend-shared.mjs
 * 同步到两套前端的 src/shared/utils/，**不要直接改 src 下的副本**。
 */

/** localStorage 键前缀 */
const KEY_PREFIX = 'easy_agent_expert_session_'

/**
 * 取某个专家最近一次使用的会话 ID。
 *
 * @param {string} expertId 专家 ID（export_agent_id）
 * @returns {string} 会话 ID；没有记录或读取失败返回空串
 */
export function getExpertLastSession(expertId) {
  if (!expertId) return ''
  try {
    return localStorage.getItem(KEY_PREFIX + expertId) || ''
  } catch (e) {
    // localStorage 不可用（隐私模式 / 配额已满）：退化为「没有记录」，不影响主流程
    return ''
  }
}

/**
 * 记录某个专家最近一次使用的会话 ID。
 *
 * @param {string} expertId 专家 ID（export_agent_id）
 * @param {string} sessionId 会话 ID（新会话在收到 SSE start 事件后才有值）
 */
export function setExpertLastSession(expertId, sessionId) {
  if (!expertId || !sessionId) return
  try {
    localStorage.setItem(KEY_PREFIX + expertId, sessionId)
  } catch (e) {
    // 写失败只影响下次「回到最近会话」，不阻断对话
  }
}

/**
 * 清除某个专家的会话关联（会话被删除时调用，避免下次跳到已不存在的会话）。
 *
 * @param {string} expertId 专家 ID（export_agent_id）
 */
export function clearExpertLastSession(expertId) {
  if (!expertId) return
  try {
    localStorage.removeItem(KEY_PREFIX + expertId)
  } catch (e) {
    // 忽略：失效映射本身有「会话列表校验」兜底
  }
}
