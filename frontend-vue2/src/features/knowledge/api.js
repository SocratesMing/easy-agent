import { dispatchAuthExpired, getStoredToken } from '../../api/auth.js'
import { fetchWithAuth, handleStreamResponse, streamHeaders, streamUrl } from '../../utils/request.js'
import { readSSE } from '../../api/sse.js'

// Keep native Response semantics for binary previews, Range headers and error bodies.
const authFetch = fetchWithAuth

// SDK 的 baseURL 在 configureRequest() 后才确定，不能在模块加载时固化
const root = () => streamUrl('/api/knowledge/v1')

export async function cancelMessage(sessionId) {
  const response = await authFetch(streamUrl(`/agent/chat/cancel?session_id=${encodeURIComponent(sessionId)}`), { method: 'POST' })
  if (!response.ok) return parseError(response)
  return response.json()
}

async function parseError(response) {
  let payload = null
  try {
    payload = await response.json()
  } catch (_) {
    // Keep the public message generic when an upstream proxy returns HTML.
  }
  const detail = payload?.detail
  const message = typeof detail === 'string'
    ? detail
    : detail?.message || payload?.message || `请求失败 (${response.status})`
  const error = new Error(message)
  error.status = response.status
  error.code = detail?.code || payload?.code || 'KNOWLEDGE_REQUEST_FAILED'
  error.retryable = Boolean(detail?.retryable || payload?.retryable)
  throw error
}

async function request(path, options = {}) {
  const headers = { ...(options.headers || {}) }
  const init = { ...options, headers }
  if (options.json !== undefined) {
    headers['Content-Type'] = 'application/json'
    init.body = JSON.stringify(options.json)
    delete init.json
  }
  const response = await authFetch(`${root()}${path}`, init)
  if (!response.ok) return parseError(response)
  if (response.status === 204) return null
  return response.json()
}

export const getKnowledgeCapabilities = () => request('/capabilities')
export const getKnowledgeStatus = () => request('/status')

export function listKnowledgeBases({ page = 1, pageSize = 100 } = {}) {
  return request(`/bases?page=${page}&page_size=${pageSize}`)
}

export function createKnowledgeBase(payload) {
  return request('/bases', { method: 'POST', json: payload })
}

export function updateKnowledgeBase(baseId, payload) {
  return request(`/bases/${encodeURIComponent(baseId)}`, { method: 'PATCH', json: payload })
}

export function deleteKnowledgeBase(baseId) {
  return request(`/bases/${encodeURIComponent(baseId)}`, { method: 'DELETE' })
}

export function listFolders(baseId) {
  return request(`/bases/${encodeURIComponent(baseId)}/folders`)
}

export function createFolder(baseId, name, parentId = '') {
  return request(`/bases/${encodeURIComponent(baseId)}/folders`, {
    method: 'POST',
    json: { name, parent_id: parentId || null },
  })
}

export function ensureFolders(baseId, { folderId = '', paths = [] } = {}) {
  return request(`/bases/${encodeURIComponent(baseId)}/folders/ensure`, {
    method: 'POST',
    json: { folder_id: folderId || null, paths },
  })
}

export function updateFolder(folderId, name) {
  return request(`/folders/${encodeURIComponent(folderId)}`, {
    method: 'PATCH',
    json: { name },
  })
}

export function deleteFolder(folderId) {
  return request(`/folders/${encodeURIComponent(folderId)}`, { method: 'DELETE' })
}

export function listDocuments(baseId, { page = 1, pageSize = 100, folderId = '', directOnly = false, status = '', q = '' } = {}) {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  if (folderId) params.set('folder_id', folderId)
  if (directOnly) params.set('direct_only', 'true')
  if (status) params.set('status', status)
  if (q) params.set('q', q)
  return request(`/bases/${encodeURIComponent(baseId)}/documents?${params}`)
}

export function uploadDocument(baseId, file, { folderId = '', relativePath = '' } = {}) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${root()}/bases/${encodeURIComponent(baseId)}/documents`)
    const token = getStoredToken()
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`)
    xhr.responseType = 'json'
    xhr.onload = () => {
      if (xhr.status === 401) {
        dispatchAuthExpired()
        reject(new Error('登录已过期，请重新登录'))
        return
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(xhr.response)
        return
      }
      const detail = xhr.response?.detail
      const error = new Error(
        typeof detail === 'string' ? detail : detail?.message || `上传失败 (${xhr.status})`
      )
      error.status = xhr.status
      error.code = detail?.code || 'KNOWLEDGE_UPLOAD_FAILED'
      reject(error)
    }
    xhr.onerror = () => reject(new Error('上传网络错误'))
    const form = new FormData()
    form.append('file', file)
    if (folderId) form.append('folder_id', folderId)
    // 文件夹上传时携带原始相对路径，后端据此逐级建目录保留层级
    if (relativePath) form.append('relative_path', relativePath)
    xhr.send(form)
  })
}

export function moveDocument(documentId, folderId) {
  return request(`/documents/${encodeURIComponent(documentId)}`, {
    method: 'PATCH',
    json: { folder_id: folderId || null },
  })
}

export function deleteDocument(documentId) {
  return request(`/documents/${encodeURIComponent(documentId)}`, { method: 'DELETE' })
}

export function retryDocument(documentId) {
  return request(`/documents/${encodeURIComponent(documentId)}/retry`, { method: 'POST' })
}

export function pauseDocument(documentId) {
  return request(`/documents/${encodeURIComponent(documentId)}/pause`, { method: 'POST' })
}

export function resumeDocument(documentId) {
  return request(`/documents/${encodeURIComponent(documentId)}/resume`, { method: 'POST' })
}

export async function getDocumentBlob(documentId, disposition = 'inline') {
  const response = await authFetch(
    `${root()}/documents/${encodeURIComponent(documentId)}/content?disposition=${disposition}`,
    { responseType: 'blob' }
  )
  if (!response.ok) return parseError(response)
  return {
    blob: await response.blob(),
    contentDisposition: response.headers.get('content-disposition') || '',
  }
}

export function listPermissions(baseId) {
  return request(`/bases/${encodeURIComponent(baseId)}/permissions`)
}

// 权限管理：userIds 为勾选的可见人员；勾选名单非空时该库仅勾选人员可见
export function replacePermissions(baseId, userIds) {
  return request(`/bases/${encodeURIComponent(baseId)}/permissions`, {
    method: 'PUT',
    json: {
      items: userIds.map(id => ({ subject_type: 'user', subject_id: id, role: 'viewer' })),
      restrict_access: userIds.length > 0,
    },
  })
}

export function listPermissionDirectory(baseId) {
  return request(`/bases/${encodeURIComponent(baseId)}/permission-directory`)
}

// 创建知识库时的可选人员目录（不依赖具体库，需公共空间管理白名单）
export function listCreationPermissionDirectory() {
  return request('/bases/permission-directory')
}

export function askKnowledgeBase(baseId, payload) {
  return request(`/bases/${encodeURIComponent(baseId)}/ask`, { method: 'POST', json: payload })
}

// peek=true 只读探测：会话不存在返回 404 且不创建会话记录，用于打开面板预取历史；
// 实际提问时用默认调用，此时才生成「[知识库问答]」会话记录。
export function prepareKnowledgeChatSession(baseId, { peek = false } = {}) {
  const suffix = peek ? '?peek=true' : ''
  return request(`/bases/${encodeURIComponent(baseId)}/chat-session${suffix}`, { method: 'POST' })
}

// 开启新一轮问答会话：旧会话保留为历史（主界面会话列表可回看），面板切换到全新会话
export function createKnowledgeChatRound(baseId) {
  return request(
    `/bases/${encodeURIComponent(baseId)}/chat-session/new`,
    { method: 'POST' }
  )
}

export async function askKnowledgeBaseStream(baseId, payload, onChunk, signal) {
  const response = await handleStreamResponse(
    await fetchWithAuth(streamUrl(`/api/knowledge/v1/bases/${encodeURIComponent(baseId)}/ask/stream`), {
      method: 'POST',
      headers: streamHeaders(),
      body: JSON.stringify(payload),
      signal,
    })
  )
  await readSSE(response, (event) => {
    if (event.type === 'error') {
      const error = new Error(event.content || '知识库问答失败')
      error.code = event.code || 'KNOWLEDGE_AGENT_FAILED'
      throw error
    }
    onChunk(event)
  }, { signal })
}

export function cancelKnowledgeBaseAnswer(baseId) {
  return request(`/bases/${encodeURIComponent(baseId)}/ask/cancel`, { method: 'POST' })
}

export function listKnowledgeOperations({ page = 1, pageSize = 100 } = {}) {
  return request(`/operations?page=${page}&page_size=${pageSize}`)
}

export function getSessionKnowledgeScope(sessionId) {
  return request(`/sessions/${encodeURIComponent(sessionId)}/knowledge-scope`)
}

export function replaceSessionKnowledgeScope(sessionId, baseIds) {
  return request(`/sessions/${encodeURIComponent(sessionId)}/knowledge-scope`, {
    method: 'PUT',
    json: { base_ids: baseIds },
  })
}
