// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/fileReferences.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
export function referenceQuery(text, caret) {
  if (typeof text !== 'string' || !Number.isInteger(caret)) return null
  const before = text.slice(0, caret)
  const match = /(?:^|\s)@([^\s@]*)$/.exec(before)
  return match ? { start: caret - match[1].length - 1, end: caret, query: match[1] } : null
}

export function addReference(references, item) {
  return references.some(ref => ref.path === item.path && ref.type === item.type)
    ? references : [...references, { path: item.path, type: item.type }]
}

export function referenceMessage(message, references = []) {
  return message.trim() || (references.length ? '查看引用对象：' + references.map(
    ref => ref.path + (ref.type === 'directory' ? '/' : '')
  ).join('、') : '')
}

// 复用输入草稿的存储键，兼容技能/专家入口写入的纯文本草稿。
export function referenceDraft(message, fileReferences = [], sessionId, previousRaw) {
  // 侧边栏先挂载输入框、再异步恢复会话。在会话尚未确定且正文未编辑时，
  // 保留原草稿里的会话绑定；界面仍只显示当前会话的引用。
  if (!sessionId && !fileReferences.length && previousRaw) {
    try {
      const previous = JSON.parse(previousRaw)
      if (previous.message === message && previous.sessionId && previous.fileReferences?.length) return previousRaw
    } catch (_) { /* 纯文本草稿 */ }
  }
  return fileReferences.length
    ? JSON.stringify({ message, fileReferences, sessionId }) : message
}

export function initialReferenceSelection(raw, message, sessionId, previousSessionId) {
  if (previousSessionId || !sessionId) return []
  const draft = restoreReferenceDraft(raw, sessionId)
  return draft.message === message ? draft.fileReferences : []
}

export function restoreReferenceDraft(raw, sessionId) {
  try {
    const value = JSON.parse(raw)
    if (typeof value.message === 'string' && Array.isArray(value.fileReferences)) {
      return { message: value.message, fileReferences: sessionId && value.sessionId === sessionId ? value.fileReferences : [] }
    }
  } catch (_) { /* 旧版纯文本草稿 */ }
  return { message: raw || '', fileReferences: [] }
}
