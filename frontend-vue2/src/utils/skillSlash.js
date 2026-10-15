/**
 * 输入框「技能 / 连接器」选择还原为发送文本的纯逻辑，便于单独验证与两版前端复用。
 *
 * - `parseSlashQuery`：判断光标前是否刚输入了「/查询词」（/ 必须位于开头或空白之后）
 * - `buildSkillPayload`：把界面上的技能标签还原成发给模型的 `/技能目录名` 指令前缀
 * - `buildConnectorPayload`：在上面基础上前置连接器胶囊对应的 `使用连接器[名称]` 指令
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
 * 组装实际发送给模型的内容：技能指令前缀 + 用户输入。
 *
 * 界面显示的是「工具图标 + 技能名」，模型需要的是 `/目录名`，例如：
 * 选了 pptx-generator 并输入「生成一份PPT创作文档」
 * → `/pptx-generator 生成一份PPT创作文档`
 *
 * @param {Array<{dir_name: string}>} skills 已选技能（按选择顺序）
 * @param {string} text 用户输入（原始文本，内部会压缩连续空白）
 */
export function buildSkillPayload(skills, text) {
  const body = (text || '').trim().replace(/\s+/g, ' ')
  const prefix = (skills || [])
    .map(skill => (skill && skill.dir_name ? `/${skill.dir_name}` : ''))
    .filter(Boolean)
    .join(' ')
  if (!prefix) return body
  return body ? `${prefix} ${body}` : prefix
}

/**
 * 组装实际发送给模型的内容：「技能指令」+「连接器指令」+ 用户输入。
 *
 * 连接器在界面上只显示胶囊名称，模型需要的是明确的使用指令，例如：
 * 选了「高德地图」并输入「帮我规划路线」
 * → `使用连接器[高德地图] 帮我规划路线`
 *
 * @param {Array<{name: string}>} connectors 已选连接器（按选择顺序）
 * @param {Array<{dir_name: string}>} skills 已选技能（按选择顺序）
 * @param {string} text 用户输入（原始文本，内部会压缩连续空白）
 */
export function buildConnectorPayload(connectors, skills, text) {
  const base = buildSkillPayload(skills, text)
  const directive = (connectors || [])
    .map(connector => (connector && connector.name ? `使用连接器[${connector.name}]` : ''))
    .filter(Boolean)
    .join(' ')
  if (!directive) return base
  return base ? `${directive} ${base}` : directive
}
