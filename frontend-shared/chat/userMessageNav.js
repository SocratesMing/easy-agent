const MAX_PROMPT_LENGTH = 120
const MAX_REPLY_LENGTH = 180

function normalizePreviewText(raw, maxLength) {
  if (!raw) return ''
  const text = String(raw).replace(/\s+/g, ' ').trim()
  if (!text) return ''
  if (text.length <= maxLength) return text
  return `${text.slice(0, maxLength)}…`
}

function extractUserPrompt(msg) {
  const text = normalizePreviewText(msg && msg.content, MAX_PROMPT_LENGTH)
  if (text) return text
  const files = Array.isArray(msg && msg.files) ? msg.files : []
  if (files.length > 0) {
    const names = files
      .map(f => (f && (f.name || f.filename)) || '')
      .filter(Boolean)
      .slice(0, 2)
    return names.length > 0 ? `[附件] ${names.join('、')}` : '[附件消息]'
  }
  return '用户消息'
}

function extractAssistantReply(msg) {
  if (!msg) return ''
  const direct = normalizePreviewText(msg.content, MAX_REPLY_LENGTH)
  if (direct) return direct
  const blocks = Array.isArray(msg.blocks) ? msg.blocks : []
  for (let i = 0; i < blocks.length; i++) {
    const block = blocks[i]
    if (block && block.type === 'content' && block.content) {
      const text = normalizePreviewText(block.content, MAX_REPLY_LENGTH)
      if (text) return text
    }
  }
  return ''
}

/**
 * 从会话消息列表中提取按时间顺序排列的用户提问导航项（附带对应助手回复摘要）。
 */
export function buildUserMessageNavItems(messages) {
  if (!Array.isArray(messages) || messages.length === 0) return []
  const items = []
  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i]
    if (!msg || msg.role !== 'user') continue
    let reply = ''
    for (let j = i + 1; j < messages.length; j++) {
      const next = messages[j]
      if (!next) continue
      if (next.role === 'user') break
      if (next.role === 'assistant') {
        reply = extractAssistantReply(next)
        if (reply) break
      }
    }
    items.push({
      id: msg.id != null ? String(msg.id) : `user-${i}`,
      messageIndex: i,
      turnNumber: items.length + 1,
      prompt: extractUserPrompt(msg),
      reply
    })
  }
  return items
}

/**
 * 根据滚动容器与各用户消息元素位置，计算当前视口对应的导航项索引。
 */
export function findActiveUserNavIndex(items, getElement, container, isAtBottom = false) {
  if (!Array.isArray(items) || items.length === 0) return -1
  if (isAtBottom) return items.length - 1
  if (!container || typeof container.getBoundingClientRect !== 'function') {
    return items.length - 1
  }
  const containerRect = container.getBoundingClientRect()
  const anchorY = containerRect.top + Math.min(containerRect.height * 0.35, 220)
  let active = 0
  for (let i = 0; i < items.length; i++) {
    const el = typeof getElement === 'function' ? getElement(items[i].messageIndex) : null
    if (!el || typeof el.getBoundingClientRect !== 'function') continue
    const rect = el.getBoundingClientRect()
    if (rect.top <= anchorY) {
      active = i
    } else {
      break
    }
  }
  return active
}

/**
 * 计算刻度线的视觉层级：active > hovered > near（邻近刻度渐宽）> default。
 */
export function getTickLevel(index, activeIndex, hoveredIndex) {
  if (index === activeIndex) return 'active'
  if (index === hoveredIndex) return 'hovered'
  const pivot = hoveredIndex >= 0 ? hoveredIndex : activeIndex
  if (pivot >= 0) {
    const dist = Math.abs(index - pivot)
    if (dist === 1) return 'near-1'
    if (dist === 2) return 'near-2'
  }
  return 'default'
}
