/**
 * 输入框「内联技能标签」的纯逻辑：两版前端（frontend / frontend-vue2）逐字一致，可单独测试。
 *
 * 核心不变式：**标签占用的布局宽度逐字等于 textarea 文本里的一段子串**（token，形如
 * `／显示名␣`，首字符是全角斜线——镜像层把它的字形换成技能图标，宽度不变；
 * **末尾带一个全角空格（U+3000，1em ≈ 15px）**，这一格既是芯片的右内边距，也是芯片与
 * 后续文字 / 下一个芯片之间的间隙：插入后光标落在它之后（芯片外），后续输入的文字不会
 * 再压在芯片的留白上。
 * （用全角而不是半角空格：半角只有 ~4px，芯片右侧要留 4~5px 内边距就必然越界压住后面的字。）
 * 镜像层与 textarea 同字体/字号/行高，且标签不产生任何水平布局宽度（无 padding / border / margin，
 * 勾边用 box-shadow、底色用 background），于是两侧的换行位置与光标位置逐像素一致，
 * 「光标紧邻标签之后」就是纯文本下标判断：`selectionStart === tokenEnd`。
 *
 * 由此所有选择状态都由文本推导（buildSegments），不存在与文本脱节的数组：
 * 连续选多个技能、粘贴 / 撤销 / 全选删除、移动端原生删除半个标签，都能自愈。
 *
 * 发送时把 token 从正文里剥掉（stripSkillTokens），技能仍以请求体 `skills`（技能目录名）提交，
 * 落库正文、气泡与会话标题里不会出现 `／技能名` 这类机器语言。
 */

/**
 * 标签首字符：全角斜线（U+FF0F），正好占 1 个汉字宽（1em）。
 * 「图标 + 名字」的观感下图标仍不能占布局宽度，所以图标画在这个字符的占位框里
 * （镜像层把字形藏掉、把图标绝对定位进去，见 ChatInput 的 .input-skill-pill-mark），
 * textarea 里的字符宽度分毫不变 —— 长文本换行与光标位置依旧逐像素对齐。
 */
export const SKILL_TOKEN_MARK = '／'

/**
 * 标签尾随的空格：全角空格（U+3000），宽度恒为 1em（15px 字号下约 15px）。
 * 它的宽度同时充当「芯片右内边距」与「芯片和后续文字/下一个芯片的间隙」，
 * 因此不能用半角空格（仅约 4px，芯片右侧留白会不够，边缘就会压住后面的字）。
 */
export const SKILL_TOKEN_TRAILING = '　'

/** 目录名兜底 token（重名技能的最终归属，dir_name 天然唯一） */
function dirToken(skill) {
  return SKILL_TOKEN_MARK + skill.dir_name + SKILL_TOKEN_TRAILING
}

/**
 * 技能 → 标签文本。
 * 优先用显示名（`／Word 文档处理␣`，观感是「图标 + 名字」）；显示名含全角斜线或换行时回退目录名。
 * 末尾的全角空格属于 token 的一部分（芯片的右内边距 + 与后文的间隙），见文件头注释。
 *
 * @param {{ dir_name?: string, name?: string }} skill
 * @returns {string} 形如 `／显示名␣`；无目录名时返回空串
 */
export function tokenForSkill(skill) {
  if (!skill || !skill.dir_name) return ''
  const name = typeof skill.name === 'string' ? skill.name.trim() : ''
  if (name && !name.includes(SKILL_TOKEN_MARK) && !/[\r\n\t]/.test(name)) {
    return SKILL_TOKEN_MARK + name + SKILL_TOKEN_TRAILING
  }
  return dirToken(skill)
}

/**
 * 建立「标签文本 → 技能」映射（token 必须唯一，否则无法由文本反解技能）。
 *
 * 每个技能收录两个 token：显示名 token（`/Word 文档处理`，面板选中后插入的形式）
 * 与目录名 token（`/docx`，天然唯一）。收录目录名后，手打 `/目录名+内容`
 * ——包括后面不加空格直接贴内容——也能渲染成标签并在发送时还原成结构化 skills，
 * 不再依赖后端解析文本前缀。
 *
 * 两个技能显示名相同时**双方**都回退 `/目录名`（避免"谁先谁赢"的随机归属）；
 * 若某个目录名 token 撞上另一个技能的显示名 token（极少数），后者（后出现的目录名）
 * 不参与解析，只能靠「/ 面板选中」时的结构化 skills 字段生效。
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
  // 第一遍：显示名 token（含重名回退），保持原有归属语义
  for (const skill of list) {
    let token = tokenForSkill(skill)
    if (nameCount.get(token) > 1) token = dirToken(skill)
    if (!token) continue
    const owner = map.get(token)
    if (owner && owner.dir_name !== skill.dir_name) continue
    map.set(token, skill)
  }
  // 第二遍：目录名 token 仅在空闲时补录，不挤占第一遍已定的显示名归属
  // （某技能显示名恰等于另一技能目录名时，显示名优先，双方都不丢失）
  for (const skill of list) {
    const dir = dirToken(skill)
    if (dir && !map.has(dir)) map.set(dir, skill)
  }
  return map
}

/**
 * 文本 index 处是否是一个完整技能标签：命中返回 token 与结束下标。
 * 只判断「此处是否为已知标签」，不判断左边界（由调用方保证，同 buildSegments）。
 *
 * @param {string} text
 * @param {number} index
 * @param {Map<string, object>} tokenMap
 * @returns {{ token: string, end: number } | null}
 */
export function matchTokenAt(text, index, tokenMap) {
  const value = typeof text === 'string' ? text : ''
  if (!Number.isInteger(index) || index < 0 || index >= value.length) return null
  // 同时接受现行全角标记与旧草稿里的半角形式（与 buildSegments 的 legacyMark 一致）
  if (value[index] !== SKILL_TOKEN_MARK && value[index] !== '/') return null
  const candidates = tokenMap instanceof Map
    ? Array.from(tokenMap.keys()).sort((a, b) => b.length - a.length)
    : []
  for (const candidate of candidates) {
    if (candidate.length > 1 && value.startsWith(candidate, index)) {
      return { token: candidate, end: index + candidate.length }
    }
    // 旧草稿或手打目录名时没有尾随全角空格：按去空格的核心形式匹配
    const core = candidate.endsWith(SKILL_TOKEN_TRAILING)
      ? candidate.slice(0, -SKILL_TOKEN_TRAILING.length)
      : ''
    if (core.length > 1 && value.startsWith(core, index)) {
      return { token: core, end: index + core.length }
    }
  }
  return null
}

/**
 * 把文本切成「普通文字段 + 技能标签段」。
 *
 * 匹配规则：token 必须位于文本开头、空白之后，或**紧邻上一个标签**（`/docx/pdf` = 连续两个标签）；
 * 同时命中的多个 token 取最长（`/docx-tools` 优先于 `/docx`）。文本里未知的 `/xxx` 一律按普通文字。
 * token 的标准形式带尾随空格（芯片的右内边距）；文本里没有这个空格时（旧草稿、或标签后直接
 * 接文字）按「去空格的核心形式」匹配 —— 芯片右缘贴着文字，观感稍紧但功能一致。
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
  const legacyMark = !!(options && options.legacyMark)

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
    // legacyMark：迁移旧版草稿时用（旧 token 是半角 `/`），正式解析只认全角斜线
    if (
      leftOk
      && index !== suppressStart
      && (value.startsWith(SKILL_TOKEN_MARK, index) || (legacyMark && value[index] === '/'))
    ) {
      for (const candidate of candidates) {
        if (candidate.length > 1 && value.startsWith(candidate, index)) {
          token = candidate
          break
        }
        // 宽松回退：标准 token 带尾随全角空格，文本里没有时按核心形式（去尾空格）匹配
        if (candidate.endsWith(SKILL_TOKEN_TRAILING)) {
          const core = candidate.slice(0, -SKILL_TOKEN_TRAILING.length)
          if (core.length > 1 && value.startsWith(core, index)) {
            token = core
            break
          }
        }
      }
    }

    if (token) {
      flushPlain()
      const skill = tokenMap.get(token) || tokenMap.get(token + SKILL_TOKEN_TRAILING)
      segments.push({
        type: 'skill',
        token,
        // 去掉全角斜线后的显示名（镜像层模板直接用，斜线的占位框让给图标；
        // 尾随空格保留在 label 里 —— 它的宽度是芯片右内边距的一部分）
        label: token.slice(SKILL_TOKEN_MARK.length),
        dirName: skill.dir_name,
        name: skill.name || skill.dir_name,
        icon: skill.icon || '',
        categoryId: skill.category_id == null ? null : skill.category_id,
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
 * 在光标处插入标签，返回新文本与「标签之后」的光标位置（选中技能后光标停在这里，
 * 即尾随空格之后 —— 芯片外，后续输入不会压住芯片）。
 *
 * 间隔保障：前方紧贴文字（非空白）时补一个空格，芯片左缘不压前一个字；
 * token 自带尾随空格、而插入点后紧跟空格时吃掉一个，避免出现双空格。
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
  let insert = token
  const prev = pos > 0 ? value[pos - 1] : ''
  if (prev && !/\s/.test(prev)) insert = ' ' + insert
  let rest = value.slice(pos)
  // token 自带尾随全角空格（已是间隔），插入点后紧跟空白时吃掉一个，避免出现双空格
  if (/\s$/.test(insert) && /^[ 　]/.test(rest)) rest = rest.slice(1)
  return { text: value.slice(0, pos) + insert + rest, caret: pos + insert.length }
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
 * 时才生效，且**只针对原生单字符删除**（removed ≤ 2，兼容代理对）：选区删除 / 大段删除 /
 * 光标落在标签中间 / 粘贴 / 替换 / 输入法上屏一律返回 null，交回常规处理
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
  // removed 过大说明是选区删除（如 Ctrl+A）：用户删掉的东西不能被自愈「找回来」
  if (removed <= 0 || removed > 2 || caret + removed > prev.length) return null
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

/**
 * 旧版草稿迁移：token 格式历经三版 —— ① 半角 `/显示名`（最初）；② 全角 `／显示名`（无尾随空格）；
 * ③ 全角 `／显示名 `（现行，尾随空格是芯片的右内边距）。挂载时把草稿里的旧形式整体改写成
 * 现行形式，避免旧草稿渲染成普通文本、或发送时把 `／显示名` 原样带给模型。
 *
 * @param {string} text 草稿文本
 * @param {Map<string, object>} tokenMap 现行 token 映射
 * @returns {string} 迁移后的文本；没有旧形式时原样返回
 */
export function migrateLegacySkillTokens(text, tokenMap) {
  const value = typeof text === 'string' ? text : ''
  if (!value || !(tokenMap instanceof Map) || !tokenMap.size) return value

  // 兼容历史形式的组合映射：现行（全角 + 尾随全角空格）、全角无尾空格、半角无尾空格
  const combined = new Map()
  for (const [token, skill] of tokenMap) {
    const core = token.endsWith(SKILL_TOKEN_TRAILING)
      ? token.slice(0, -SKILL_TOKEN_TRAILING.length)
      : token
    combined.set(token, skill)
    if (core.length > 1) combined.set(core, skill)
    combined.set('/' + core.slice(SKILL_TOKEN_MARK.length), skill)
  }
  const segments = buildSegments(value, combined, { legacyMark: true })
  if (!segments.some((seg) => seg.type === 'skill')) return value

  let out = ''
  for (const seg of segments) {
    const skill = seg.type === 'skill' ? combined.get(seg.token) : null
    if (skill) {
      out += tokenForSkill(skill)
      continue
    }
    // 旧形式后面本来就带空格、而现行 token 自带尾随空格 → 吃掉一个，不留双空格
    let t = seg.text
    if (/\s$/.test(out) && /^[ 　]/.test(t)) t = t.slice(1)
    out += t
  }
  return out
}
