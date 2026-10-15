/**
 * 用户消息里「技能令牌」的解析（纯逻辑，便于单独验证）。
 *
 * 发送时输入框会把已选技能拼成 `/目录名` 前缀（见 skillSlash.buildSkillPayload），
 * 因此落库的用户消息形如 `/pptx-generator 生成一份PPT创作文档`。对话气泡需要把这段
 * 前缀还原成技能标签，只把剩余正文当作用户真正输入的内容展示 / 复制。
 *
 * 解析规则刻意保守，只认「消息开头连续出现、且目录名命中已知技能」的 /令牌：
 * - 正文里的 /usr/bin、/workspace/out 之类路径不会被误判成技能；
 * - 正文中间出现的 /技能名 是用户自己打的字，不属于发送时拼接的前缀，保持原样。
 */

/** 令牌形如 `/目录名`，目录名限字母/数字/连字符/下划线，且后面必须跟空白或结束 */
const TOKEN_PATTERN = /^\/([A-Za-z0-9][A-Za-z0-9_-]*)(?=\s|$)/

/** 防御上限：正常一次最多挂几个技能，异常长的前缀不再继续当令牌消费 */
const MAX_TOKENS = 32

/**
 * 拆分消息开头的技能令牌与剩余正文。
 *
 * @param {string} content 消息原文（发送时拼好的 `/目录名 正文`）
 * @param {Iterable<string>|Array<string>} knownDirNames 已知技能目录名（技能目录的 key）
 * @returns {{ dirNames: string[], body: string }} 命中的目录名（按出现顺序）与剩余正文；
 *          未命中任何令牌时 body 原样返回，不丢内容
 */
export function parseLeadingSkillTokens(content, knownDirNames) {
  const text = typeof content === 'string' ? content : ''
  const known = knownDirNames instanceof Set
    ? knownDirNames
    : new Set(Array.isArray(knownDirNames) ? knownDirNames : [])
  // 目录未知时不做任何解析：宁可显示 /目录名 原文，也不要把普通文本误切成标签
  if (!text || known.size === 0) return { dirNames: [], body: text }

  const dirNames = []
  let rest = text

  while (rest && dirNames.length < MAX_TOKENS) {
    const matched = TOKEN_PATTERN.exec(rest)
    if (!matched) break
    if (!known.has(matched[1])) break
    dirNames.push(matched[1])
    rest = rest.slice(matched[0].length).replace(/^\s+/, '')
  }

  if (dirNames.length === 0) return { dirNames: [], body: text }
  return { dirNames, body: rest }
}

/** 去掉前缀后只剩省略号等无意义字符（会话标题按 15 字截断会产生这种尾巴） */
const ONLY_PUNCTUATION = /^[.\u2026\s]+$/

/**
 * 展示用文本：去掉开头拼接的 `/技能名` 前缀。
 *
 * 会话标题取的是首条消息原文，用户挂技能时它是 `/demo-skill 测试对话`，侧边栏
 * 直接显示很丑，用它换成用户真正输入的内容。整段只有技能指令（如 `/docx`，或
 * 被截断成 `/skill-creator ...`）时退回技能展示名，避免展示成一个斜杠指令。
 * 目录未知或目录名不匹配时原样返回，绝不误删 `/workspace` 这类路径开头。
 *
 * @param {string} text 标题/正文原文
 * @param {Iterable<string>|Array<string>} knownDirNames 已知技能目录名
 * @param {Object<string, {name?: string, dir_name?: string}>} [skillByDirName] 技能信息（取展示名用）
 * @returns {string} 展示用文本
 */
export function stripSkillPrefix(text, knownDirNames, skillByDirName) {
  const source = typeof text === 'string' ? text : ''
  if (!source) return source

  const parsed = parseLeadingSkillTokens(source, knownDirNames)
  if (parsed.dirNames.length === 0) return source
  if (parsed.body && !ONLY_PUNCTUATION.test(parsed.body)) return parsed.body

  const skill = (skillByDirName || {})[parsed.dirNames[0]]
  return (skill && (skill.name || skill.dir_name)) || parsed.dirNames[0]
}
