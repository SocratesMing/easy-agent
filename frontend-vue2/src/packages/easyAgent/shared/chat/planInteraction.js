// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/chat/planInteraction.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
/**
 * Plan confirmation protocol shared by both frontend baselines.
 *
 * The backend exposes a HITL-only `ask_user` tool. Its arguments are rendered as a
 * multi-question form and the answers resume the same graph run with a `respond`
 * decision. Keeping normalization and resume payload construction here prevents the
 * Vue 2 and Vue 3 clients from drifting on what the model receives.
 */

export const ASK_USER_TOOL_NAME = 'ask_user'

export const INTERACTION_MODES = Object.freeze([
  { value: 'normal', label: '对话', shortLabel: '对话', description: '直接回答或执行' },
  { value: 'plan', label: '规划', shortLabel: '规划', description: '先确认关键方案再继续' },
  { value: 'brainstorm', label: '头脑风暴', shortLabel: '发散', description: '先发散收敛再落地' },
])

const MODE_VALUES = new Set(INTERACTION_MODES.map(item => item.value))

export const INTERACTION_COMMANDS = Object.freeze([
  {
    dir_name: 'plan',
    command: true,
    interactionMode: 'plan',
    name: '规划模式 plan',
    description: '先整理方案，在关键分歧处逐项向用户确认后再继续',
  },
])

function text (value, maxLength) {
  return String(value == null ? '' : value).trim().slice(0, maxLength)
}

function safeId (value, fallback) {
  const normalized = text(value, 48).replace(/[^A-Za-z0-9_-]/g, '_')
  return normalized || fallback
}

function parseLooseJson (value) {
  if (typeof value !== 'string') return value
  const trimmed = value.trim()
  if (!trimmed) return null
  try {
    return JSON.parse(trimmed)
  } catch {
    return null
  }
}

function unwrapToolResult (raw) {
  let value = parseLooseJson(raw)
  if (Array.isArray(value)) {
    const textParts = value
      .map(item => {
        if (typeof item === 'string') return item
        if (item && typeof item.text === 'string') return item.text
        return ''
      })
      .filter(Boolean)
    if (textParts.length) value = parseLooseJson(textParts.join('\n'))
  }
  if (value && typeof value === 'object' && !Array.isArray(value) &&
      'content' in value && !('answers' in value)) {
    value = parseLooseJson(value.content)
  }
  return value
}

export function normalizeInteractionMode (mode) {
  const value = String(mode || '').trim().toLowerCase()
  return MODE_VALUES.has(value) ? value : 'normal'
}

export function interactionModeMeta (mode) {
  const value = normalizeInteractionMode(mode)
  return INTERACTION_MODES.find(item => item.value === value)
}

/** 规划等斜线指令在输入框里用和技能相同的内联标签，不单独占一行。 */
export function modeTokenSkill (command) {
  if (!command || !command.dir_name || !command.interactionMode) return null
  const meta = interactionModeMeta(command.interactionMode)
  return {
    dir_name: command.dir_name,
    name: (meta && meta.shortLabel) || command.dir_name,
    interactionMode: command.interactionMode,
    command: true,
  }
}

export function interactionModeFromTokenMap (tokenMap, dirs) {
  const skills = tokenMap instanceof Map ? [...tokenMap.values()] : []
  for (const dir of dirs || []) {
    const skill = skills.find(item => item && item.dir_name === dir)
    const mode = normalizeInteractionMode(skill && skill.interactionMode)
    if (mode !== 'normal') return mode
  }
  return 'normal'
}

export function withoutInteractionSkills (dirs, tokenMap) {
  const skills = tokenMap instanceof Map ? [...tokenMap.values()] : []
  return (dirs || []).filter(dir => {
    const skill = skills.find(item => item && item.dir_name === dir)
    return normalizeInteractionMode(skill && skill.interactionMode) === 'normal'
  })
}

export function interactionCommandItems (query) {
  const q = String(query || '').trim().toLowerCase()
  if (!q) return [...INTERACTION_COMMANDS]
  return INTERACTION_COMMANDS.filter(item =>
    `${item.dir_name}${item.name}${item.description}`.toLowerCase().includes(q)
  )
}

export function normalizeInteractionRequest (raw, options = {}) {
  const source = parseLooseJson(raw)
  if (!source || typeof source !== 'object' || Array.isArray(source)) return null
  const requestedLimit = Number(options.maxQuestions)
  const maxQuestions = Number.isFinite(requestedLimit)
    ? Math.max(1, Math.min(64, Math.floor(requestedLimit)))
    : 4

  const seenQuestionIds = new Set()
  const questions = []
  for (const [index, candidate] of (Array.isArray(source.questions) ? source.questions : []).entries()) {
    if (!candidate || typeof candidate !== 'object') continue
    let questionId = safeId(candidate.id, `question_${index + 1}`)
    while (seenQuestionIds.has(questionId)) questionId = `${questionId}_${index + 1}`
    seenQuestionIds.add(questionId)

    const seenOptionIds = new Set()
    const options = []
    for (const [optionIndex, option] of (Array.isArray(candidate.options) ? candidate.options : []).entries()) {
      if (!option || typeof option !== 'object') continue
      const label = text(option.label, 80)
      if (!label) continue
      let optionId = safeId(option.id, `${questionId}_option_${optionIndex + 1}`)
      while (seenOptionIds.has(optionId)) optionId = `${optionId}_${optionIndex + 1}`
      seenOptionIds.add(optionId)
      options.push({
        id: optionId,
        label,
        description: text(option.description, 240),
        recommended: option.recommended === true,
      })
    }
    if (options.length < 2) continue

    const explicitMultiple = candidate.multiple === true ||
      ['multi', 'multiple', 'multi_select'].includes(String(candidate.type || '').toLowerCase())
    questions.push({
      id: questionId,
      title: text(candidate.title || candidate.label, 120) || `确认项 ${index + 1}`,
      description: text(candidate.description, 300),
      multiple: explicitMultiple,
      required: candidate.required !== false,
      options: options.slice(0, 6),
    })
    if (questions.length >= maxQuestions) break
  }
  if (!questions.length) return null

  return {
    title: text(source.title, 80) || '确认下一步方案',
    description: text(source.description, 600),
    questions,
    submitLabel: text(source.submit_label || source.submitLabel, 24) || '确认并继续',
    allowNote: source.allow_note !== false && source.allowNote !== false,
  }
}

export function isInteractionAction (action) {
  return !!action && (action.tool_name || action.name) === ASK_USER_TOOL_NAME
}

export function pendingInteractionActions (message) {
  const pending = message && message.pending_approval
  const actions = pending && Array.isArray(pending.action_requests) ? pending.action_requests : []
  return actions.filter(isInteractionAction)
}

/**
 * Group ask_user actions that belong to the same HITL interrupt. A model may emit
 * parallel ask_user calls; those calls must be answered independently in one resume.
 */
export function buildInteractionBatches (actionRequests) {
  const groups = new Map()
  ;(actionRequests || []).filter(isInteractionAction).forEach((action, index) => {
    const key = action.interrupt_id
      ? `i:${action.interrupt_id}`
      : action.tool_call_id
        ? `t:${action.tool_call_id}`
        : `x:${index}`
    const group = groups.get(key) || []
    group.push(action)
    groups.set(key, group)
  })

  return [...groups.entries()].map(([key, actions]) => {
    const sorted = [...actions].sort((a, b) =>
      (a.action_index == null ? 0 : Number(a.action_index)) -
      (b.action_index == null ? 0 : Number(b.action_index))
    )
    const prepared = sorted.map((action, sourceIndex) => ({
      action,
      sourceIndex,
      request: normalizeInteractionRequest(action.arguments),
    }))
    if (prepared.length === 1) {
      const request = prepared[0].request
      return {
        key,
        actions: sorted,
        questionLimit: request ? request.questions.length : 4,
        action: {
          ...sorted[0],
          questionLimit: request ? Math.max(1, request.questions.length) : 4,
        },
        requests: prepared,
        entries: request
          ? request.questions.map(question => ({
            questionId: question.id,
            originalId: question.id,
            sourceIndex: 0,
            request,
            question,
          }))
          : [],
      }
    }

    const entries = []
    const mergedQuestions = []
    for (const item of prepared) {
      if (!item.request) continue
      for (const question of item.request.questions) {
        let questionId = `a${item.sourceIndex}_${question.id}`
        while (entries.some(entry => entry.questionId === questionId)) {
          questionId += '_'
        }
        const mergedQuestion = { ...question, id: questionId }
        mergedQuestions.push(mergedQuestion)
        entries.push({
          questionId,
          originalId: question.id,
          sourceIndex: item.sourceIndex,
          request: item.request,
          question: mergedQuestion,
        })
      }
    }

    const validRequests = prepared.map(item => item.request).filter(Boolean)
    const titles = [...new Set(validRequests.map(request => request.title).filter(Boolean))]
    const descriptions = [...new Set(validRequests.map(request => request.description).filter(Boolean))]
    return {
      key,
      actions: sorted,
      action: {
        ...sorted[0],
        questionLimit: Math.max(1, mergedQuestions.length),
        arguments: {
          title: titles.length === 1 ? titles[0] : '确认多项方案',
          description: descriptions.join('\n\n'),
          questions: mergedQuestions,
          submit_label: validRequests[0]?.submitLabel || '确认并继续',
          allow_note: validRequests.some(request => request.allowNote),
        },
      },
      requests: prepared,
      entries,
    }
  })
}

/** Convert merged card answers back into one respond payload per original ask_user action. */
export function buildInteractionBatchResponses (batch, answers, note = '') {
  const selected = answers && typeof answers === 'object' ? answers : {}
  const requests = batch && Array.isArray(batch.requests) ? batch.requests : []
  const entries = batch && Array.isArray(batch.entries) ? batch.entries : []
  const lastValid = requests.reduce((last, item, index) => item.request ? index : last, -1)
  return requests.map((item, requestIndex) => {
    const response = { answers: {} }
    if (item.request) {
      for (const question of item.request.questions) {
        const entry = entries.find(candidate =>
          candidate.sourceIndex === item.sourceIndex && candidate.originalId === question.id
        )
        if (entry && selected[entry.questionId] !== undefined) {
          response.answers[question.id] = selected[entry.questionId]
        }
      }
    }
    const responseNote = text(note, 2000)
    if (requestIndex === lastValid && responseNote) response.note = responseNote
    const action = item.action || {}
    return {
      interruptId: action.interrupt_id || null,
      actionIndex: action.action_index != null ? action.action_index : item.sourceIndex,
      actionName: action.name || action.tool_name || ASK_USER_TOOL_NAME,
      response,
    }
  })
}

export function interactionKey (action, index = 0) {
  if (!action) return `interaction_${index}`
  return action.interrupt_id || action.tool_call_id || `interaction_${index}`
}

export function missingRequiredQuestionIds (request, answers) {
  const selected = answers && typeof answers === 'object' ? answers : {}
  return (request && request.questions ? request.questions : [])
    .filter(question => {
      if (!question.required) return false
      const value = selected[question.id]
      return question.multiple
        ? !Array.isArray(value) || value.length === 0
        : !value
    })
    .map(question => question.id)
}

/**
 * Build one interrupt's mixed decisions. Any non-ask_user tool accidentally batched
 * with the question is rejected so confirmation cannot silently execute side effects.
 */
export function buildInteractionResumeDecisions (actionRequests, options = {}) {
  const actions = (actionRequests || []).filter(Boolean)
  if (!actions.length) return { decisions: [], interruptId: null }

  const requestedId = options.interruptId || actions.find(action => action.interrupt_id)?.interrupt_id
  const legacy = !requestedId
  const batch = (legacy ? actions : actions.filter(action => action.interrupt_id === requestedId))
    .sort((a, b) => (a.action_index || 0) - (b.action_index || 0))

  const defaultResponse = {
    answers: options.answers && typeof options.answers === 'object' ? options.answers : {},
  }
  const note = text(options.note, 2000)
  if (note) defaultResponse.note = note
  const responseByActionIndex = new Map(
    (Array.isArray(options.responses) ? options.responses : []).map(response => [
      Number(response.actionIndex || 0),
      response.response,
    ])
  )

  const decisions = batch.map(action => {
    const routing = action.interrupt_id
      ? { interrupt_id: action.interrupt_id, action_index: action.action_index || 0 }
      : {}
    if (isInteractionAction(action)) {
      const actionIndex = Number(action.action_index || 0)
      const response = responseByActionIndex.has(actionIndex)
        ? responseByActionIndex.get(actionIndex)
        : (options.responses ? { answers: {} } : defaultResponse)
      return { type: 'respond', message: JSON.stringify(response), ...routing }
    }
    return {
      type: 'reject',
      message: '用户正在确认规划选项，本轮尚未授权执行该操作；请先根据确认结果继续方案。',
      ...routing,
    }
  })
  return { decisions, interruptId: requestedId || null }
}

function selectedLabels (question, value) {
  const ids = Array.isArray(value) ? value : (value ? [value] : [])
  return ids
    .map(id => question.options.find(option => option.id === id)?.label)
    .filter(Boolean)
}

export function answerLabelsForQuestion (question, value) {
  return selectedLabels(question, value)
}

/** User-visible confirmation text persisted as a normal user message. */
export function buildConfirmationMessage (rawRequest, answers, note = '', options = {}) {
  const request = normalizeInteractionRequest(rawRequest, options)
  if (!request) return ''
  const lines = []
  request.questions.forEach((question, index) => {
    const labels = selectedLabels(question, (answers || {})[question.id])
    lines.push(`${index + 1}. ${question.title}：${labels.length ? labels.join('、') : '未选择'}`)
  })
  const trimmedNote = text(note, 2000)
  if (trimmedNote) lines.push(`补充说明：${trimmedNote}`)
  return lines.join('\n')
}

export function buildConfirmationUserMessage (confirmation) {
  if (!confirmation || !confirmation.text) return null
  return {
    id: confirmation.id,
    role: 'user',
    content: confirmation.text,
    created_at: new Date().toISOString(),
    message_kind: 'plan_confirmation',
    display_only: true,
  }
}

export function parseInteractionResponse (raw) {
  const value = unwrapToolResult(raw)
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const answers = value.answers
  if (!answers || typeof answers !== 'object' || Array.isArray(answers)) return null
  return {
    answers,
    note: text(value.note, 2000),
  }
}

export function resolvedInteractionBlocks (message) {
  const blocks = (message && message.blocks) || []
  return blocks.filter(block =>
    block && block.type === 'tool_call' && block.tool_name === ASK_USER_TOOL_NAME &&
    (block.result !== undefined && block.result !== null && block.result !== '')
  )
}
