/**
 * 输入框技能选择（slash 检索）的纯逻辑，便于单独验证与两版前端复用。
 *
 * - `parseSlashQuery`：判断光标前是否刚输入了「/查询词」（/ 必须位于开头或空白之后）
 * - `buildMessageText`：归一化实际发送的消息正文（压缩空白，不拼任何指令前缀）
 *
 * 已选技能不进消息文本：以请求体的 `skills` 字段（技能目录名）单独传给后端，
 * 由后端决定怎么交给模型（见 `api/chat.js` / `api/expert.js` 的 payload）。这样气泡、
 * 会话标题与历史里都不会出现 `/技能名` 这类机器语言。
 * 连接器不随消息提交：本会话启用哪些连接器由输入框「+」→ 连接器面板的开关设置
 * （见 `api/connectors.js` 的 setSessionConnectors）。
 * 后端仍兼容消息文本里的旧指令写法（旧前端 / 用户手打）。
 */

/** 匹配光标前的 `/查询词`（查询词内不含空白与 `/`） */
const SLASH_PATTERN = /(^|\s)(\/[^\s/]*)$/

/**
 * 解析光标前的 slash 查询。
 *
 * @param {string} text 输入框全文
 * @param {number} caret 光标位置（未传时取文本末尾）
 * @returns {{ query: string, start: number } | null} 命中返回查询词与 `/` 的起始下标
 */
export function parseSlashQuery(text, caret) {
  const value = typeof text === 'string' ? text : ''
  const pos = Number.isInteger(caret) ? caret : value.length
  const matched = SLASH_PATTERN.exec(value.slice(0, pos))
  if (!matched) return null
  return { query: matched[2].slice(1), start: pos - matched[2].length }
}

/**
 * 实际发送的消息正文：只做归一化（去首尾空白、压缩连续空白）。
 *
 * 技能以结构化字段单独提交，正文就是用户输入的原话——这样落库内容、气泡与会话
 * 标题天然干净，不需要任何字符串清洗。
 *
 * @param {string} text 用户输入
 */
export function buildMessageText(text) {
  return (text || '').trim().replace(/\s+/g, ' ')
}
