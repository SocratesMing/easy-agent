/**
 * 输入框「内联技能标签」的纯逻辑：两版前端（frontend / frontend-vue2）逐字一致，可单独测试。
 *
 * 核心不变式：**标签的可见文字逐字等于 textarea 文本里的一段子串**（token，形如 `/显示名`）。
 * 镜像层与 textarea 同字体/字号/行高，且标签不产生任何水平布局宽度（无 padding / border / margin，
 * 勾边用 box-shadow、底色用 background），于是两侧的换行位置与光标位置逐像素一致，
 * 「光标紧邻标签之后」就是纯文本下标判断：`selectionStart === tokenEnd`。
 *
 * 由此所有选择状态都由文本推导（buildSegments），不存在与文本脱节的数组：
 * 连续选多个技能、粘贴 / 撤销 / 全选删除、移动端原生删除半个标签，都能自愈。
 *
 * 发送时把 token 从正文里剥掉（stripSkillTokens），技能仍以请求体 `skills`（技能目录名）提交，
 * 落库正文、气泡与会话标题里不会出现 `/技能名` 这类机器语言。
 */

/** 目录名兜底 token（重名技能的最终归属，dir_name 天然唯一） */
function dirToken(skill) {
  return `/${skill.dir_name}`
}

/**
 * 技能 → 标签文本。
 * 优先用显示名（`/Word 文档处理`，观感接近原胶囊）；显示名含 `/` 或换行时回退目录名。
 *
 * @param {{ dir_name?: string, name?: string }} skill
 * @returns {string} 形如 `/显示名`；无目录名时返回空串
 */
export function tokenForSkill(skill) {
  if (!skill || !skill.dir_name) return ''
  const name = typeof skill.name === 'string' ? skill.name.trim() : ''
  if (name && !name.includes('/') && !/[\r\n\t]/.test(name)) return `/${name}`
  return dirToken(skill)
}

/**
 * 建立「标签文本 → 技能」映射（token 必须唯一，否则无法由文本反解技能）。
 *
 * 两个技能显示名相同时**双方**都回退 `/目录名`（避免"谁先谁赢"的随机归属）；
 * 若回退后仍与该目录名撞上另一个技能的名字（极少数），后出现者不参与解析，
 * 只能靠「/ 面板选中」时的结构化 skills 字段生效。
 *
 * @param {Array<{dir_name?: string, name?: string}>} skills
 * @returns {Map<string, object>}
 */
export function buildTokenMap(skills) {
  const list = (Array.isArray(skills) ? skills : []).filter((s) => s && s.dir_name)
  const nameCount = new Map()
  for (const skill of list) {
    const token = tokenForSkill(skill)
    nameCount.set(token, (nameCount.get(token) || 0) + 1)
  }

  const map = new Map()
  for (const skill of list) {
    let token = tokenForSkill(skill)
    if (nameCount.get(token) > 1) token = dirToken(skill)
    if (!token) continue
    const owner = map.get(token)
    if (owner && owner.dir_name !== skill.dir_name) continue
    map.set(token, skill)
  }
  return map
}

/**
 * 把文本切成「普通文字段 + 技能标签段」。
 *
 * 匹配规则：token 必须位于文本开头、空白之后，或**紧邻上一个标签**（`/docx/pdf` = 连续两个标签）；
 * 同时命中的多个 token 取最长（`/docx-tools` 优先于 `/docx`）。文本里未知的 `/xxx` 一律按普通文字。
 *
 * @param {string} text textarea 全文
 * @param {Map<string, object>} tokenMap buildTokenMap 的结果
 * @param {{ suppressStart?: number } | null} [options] suppressStart：该下标的 token 按普通文字渲染
 *        （用于「刚输入 /查询词 正在检索」时不要把半截查询词渲染成标签）
 * @returns {Array<{type: 'text', text: string} | {type: 'skill', token: string, dirName: string, name: string, start: number, end: number}>}
 */
export function buildSegments(text, tokenMap, options) {
  const value = typeof text === 'string' ? text : ''
  const segments = []
  if (!value) return segments

  const candidates = tokenMap instanceof Map
    ? Array.from(tokenMap.keys()).sort((a, b) => b.length - a.length)
    : []
  const suppressStart = options && Number.isInteger(options.suppressStart)
    ? options.suppressStart
    : -1

  let plain = ''
  let index = 0
  let lastSkillEnd = -1
  const flushPlain = () => {
    if (plain) {
      segments.push({ type: 'text', text: plain })
      plain = ''
    }
  }

  while (index < value.length) {
    const leftOk = index === 0 || index === lastSkillEnd || /\s/.test(value[index - 1])
    let token = ''
    if (leftOk && index !== suppressStart && value[index] === '/') {
      for (const candidate of candidates) {
        if (candidate.length > 1 && value.startsWith(candidate, index)) {
          token = candidate
          break
        }
      }
    }

    if (token) {
      flushPlain()
      const skill = tokenMap.get(token)
      segments.push({
        type: 'skill',
        token,
        dirName: skill.dir_name,
        name: skill.name || skill.dir_name,
        start: index,
        end: index + token.length,
      })
      lastSkillEnd = index + token.length
      index = lastSkillEnd
      continue
    }

    plain += value[index]
    index += 1
  }

  flushPlain()
  return segments
}

/**
 * 文本里的技能标签段（含位置，用于删除与发送前的剥离）。
 *
 * @param {string} text
 * @param {Map<string, object>} tokenMap
 * @returns {Array<{type: 'skill', token: string, dirName: string, name: string, start: number, end: number}>}
 */
export function skillSegments(text, tokenMap) {
  return buildSegments(text, tokenMap).filter((seg) => seg.type === 'skill')
}

/**
 * 文本里已选技能的目录名（按出现顺序去重）。发送时作为请求体 skills 字段。
 *
 * @param {string} text
 * @param {Map<string, object>} tokenMap
 * @returns {string[]}
 */
export function skillDirsInText(text, tokenMap) {
  const dirs = []
  for (const seg of skillSegments(text, tokenMap)) {
    if (!dirs.includes(seg.dirName)) dirs.push(seg.dirName)
  }
  return dirs
}

/**
 * 光标前是否紧邻一个技能标签（Backspace 的判定：删整段标签而非单个字符）。
 *
 * @param {string} text
 * @param {number} caret 光标下标
 * @param {Map<string, object>} tokenMap
 * @returns {object | null} 命中的标签段
 */
export function tokenBeforeCaret(text, caret, tokenMap) {
  if (!Number.isInteger(caret)) return null
  return skillSegments(text, tokenMap).find((seg) => seg.end === caret) || null
}

/**
 * 光标后是否紧邻一个技能标签（Delete 键的对称行为）。
 *
 * @param {string} text
 * @param {number} caret 光标下标
 * @param {Map<string, object>} tokenMap
 * @returns {object | null} 命中的标签段
 */
export function tokenAfterCaret(text, caret, tokenMap) {
  if (!Number.isInteger(caret)) return null
  return skillSegments(text, tokenMap).find((seg) => seg.start === caret) || null
}

/**
 * 在光标处插入标签，返回新文本与「标签之后」的光标位置（选中技能后光标停在这里）。
 *
 * @param {string} text
 * @param {number} caret
 * @param {string} token
 * @returns {{ text: string, caret: number }}
 */
export function insertSkillToken(text, caret, token) {
  const value = typeof text === 'string' ? text : ''
  const pos = Math.max(0, Math.min(Number.isInteger(caret) ? caret : value.length, value.length))
  if (!token) return { text: value, caret: pos }
  return { text: value.slice(0, pos) + token + value.slice(pos), caret: pos + token.length }
}

/**
 * 删除一段标签（或任意区间），光标落在区间起点。
 *
 * @param {string} text
 * @param {number} start
 * @param {number} end
 * @returns {{ text: string, caret: number }}
 */
export function removeTokenRange(text, start, end) {
  const value = typeof text === 'string' ? text : ''
  const from = Math.max(0, Math.min(start, value.length))
  const to = Math.max(from, Math.min(end, value.length))
  return { text: value.slice(0, from) + value.slice(to), caret: from }
}

/**
 * 批量删除标签（同一技能在草稿里可能出现多次）：从后往前删，光标停在最靠前那个标签的起点。
 *
 * @param {string} text
 * @param {Array<{start: number, end: number}>} segmentsToRemove
 * @returns {{ text: string, caret: number }}
 */
export function removeTokenSegments(text, segmentsToRemove) {
  let value = typeof text === 'string' ? text : ''
  const list = (Array.isArray(segmentsToRemove) ? segmentsToRemove : [])
    .filter((seg) => seg && Number.isInteger(seg.start) && Number.isInteger(seg.end))
    .sort((a, b) => b.start - a.start)
  if (!list.length) return { text: value, caret: value.length }

  for (const seg of list) {
    value = value.slice(0, seg.start) + value.slice(seg.end)
  }
  const caret = Math.min(...list.map((seg) => seg.start))
  return { text: value, caret: Math.min(caret, value.length) }
}

/**
 * 发送前的正文：剥掉所有技能标签（技能以结构化 skills 字段单独提交）。
 * 相邻文字会直接接上（如 `a/docx b` → `a b`），由 buildMessageText 再统一压缩空白。
 *
 * @param {string} text
 * @param {Map<string, object>} tokenMap
 * @returns {string}
 */
export function stripSkillTokens(text, tokenMap) {
  const value = typeof text === 'string' ? text : ''
  const segs = skillSegments(value, tokenMap)
  if (!segs.length) return value
  return removeTokenSegments(value, segs).text
}

/**
 * 移动端（Android Chrome 不给 Backspace keydown、部分浏览器无 beforeinput）的兜底自愈：
 * 光标紧邻标签时浏览器按单字符删，只啃掉标签的**首/尾一个字**，这里把整个标签补删干净，
 * 避免留下「半个标签」的残缺文本。
 *
 * 只在能确认「这次删除动的正是标签的首字（Delete）或尾字（Backspace）、且其余文本一字未改」
 * 时才生效：光标落在标签中间、粘贴 / 替换 / 输入法上屏等编辑一律返回 null，交回常规处理
 * （需求：光标不在标签紧邻位置时，保持常规的逐字符删除行为）。
 *
 * @param {string} prevText 编辑前文本
 * @param {string} nextText 编辑后文本
 * @param {number} caret 编辑后光标位置
 * @param {Map<string, object>} tokenMap
 * @returns {{ text: string, caret: number } | null}
 */
export function reconcileTokenDeletion(prevText, nextText, caret, tokenMap) {
  const prev = typeof prevText === 'string' ? prevText : ''
  const next = typeof nextText === 'string' ? nextText : ''
  if (!Number.isInteger(caret) || caret < 0) return null
  const removed = prev.length - next.length
  if (removed <= 0 || caret + removed > prev.length) return null
  // 删除点之外必须一字未改（保证这是一次「在 caret 处向前/向后删除」）
  if (prev.slice(0, caret) !== next.slice(0, caret)) return null
  if (prev.slice(caret + removed) !== next.slice(caret)) return null

  const end = caret + removed
  const touched = skillSegments(prev, tokenMap).filter((seg) => {
    // Backspace：删除区间正好收在标签末尾（啃掉的是标签尾巴）
    if (end === seg.end && caret >= seg.start && caret < seg.end) return true
    // Delete：删除点正好在标签开头（啃掉的是标签首字）
    if (caret === seg.start) return true
    return false
  })
  if (!touched.length) return null
  return removeTokenSegments(prev, touched)
}
