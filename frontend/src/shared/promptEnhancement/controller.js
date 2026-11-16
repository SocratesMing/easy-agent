// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/promptEnhancement/controller.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
/** Per-input request ownership. All editing entrances invalidate synchronously. */
export function createEnhancementController({ read, write, request, publish }) {
  let revision = 0
  let current = null
  let controlled = false
  let disposed = false
  const state = { pending: false, notice: '' }
  const update = (patch) => { Object.assign(state, patch); publish({ ...state }) }
  const fingerprint = () => JSON.stringify(read())
  function invalidate() {
    if (controlled) return
    revision++
    const old = current
    current = null
    update({ pending: false, notice: '' })
    if (old) old.controller.abort()
  }
  function controlledWrite(text) {
    controlled = true
    try { write(text) } finally { controlled = false }
  }
  async function enhance() {
    const snapshot = read()
    if (disposed || current || snapshot.blocked || !snapshot.body.trim()) return
    const owner = { controller: new AbortController(), revision, fingerprint: fingerprint() }
    current = owner
    update({ pending: true, notice: '' })
    const valid = () => !disposed && current === owner && owner.revision === revision && owner.fingerprint === fingerprint()
    try {
      const result = await request(snapshot.body, owner.controller.signal)
      if (!valid()) return
      if (!result || typeof result.enhanced_prompt !== 'string' || !result.enhanced_prompt.trim()) {
        throw new Error('提示词增强失败，请重试')
      }
      const after = snapshot.restore(result.enhanced_prompt.trim())
      controlledWrite(after)
      update({ notice: '' })
    } catch (error) {
      if (valid() && !owner.controller.signal.aborted) {
        update({ notice: enhancementError(error) })
      }
    } finally {
      if (current === owner) {
        current = null
        update({ pending: false })
      }
    }
  }
  return { enhance, invalidate, dispose() { invalidate(); disposed = true } }
}

export function enhancementError(error) {
  const status = error?.response?.status || error?.status
  if (error?.code === 'ECONNABORTED' || error?.name === 'TimeoutError') return '提示词增强超时，请重试'
  return ({
    401: '登录已过期，请重新登录',
    422: '请输入有效提示词，或缩短输入内容',
    429: '请求过于频繁，请稍后重试',
    503: '提示词增强暂不可用，请联系管理员检查模型配置',
    504: '提示词增强超时，请重试',
  })[status] || '提示词增强失败，请重试'
}

/** Keep recognized tokens (including duplicate-name directory fallbacks) in original order. */
export function enhancementSnapshot(text, tokenMap, { stripSkillTokens, skillSegments, insertSkillToken }) {
  const tokens = []
  const seen = new Set()
  for (const segment of skillSegments(text, tokenMap)) {
    if (seen.has(segment.dirName)) continue
    seen.add(segment.dirName)
    tokens.push({ token: segment.token.endsWith('　') ? segment.token : segment.token + '　', dirName: segment.dirName })
  }
  return {
    text, body: stripSkillTokens(text, tokenMap).trim(), tokens,
    restore(body) {
      // Model output must not add skills or duplicate the saved selection.
      const content = stripSkillTokens(body, tokenMap).trim()
      if (!content) throw new Error('提示词增强失败，请重试')
      let prefix = ''
      for (const { token } of tokens) prefix = insertSkillToken(prefix, prefix.length, token).text
      return prefix + content
    },
  }
}
