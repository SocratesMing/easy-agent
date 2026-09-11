import { API_BASE_URL } from '../../config.js'
import { authFetch, dispatchAuthExpired, getStoredToken } from '../../api/auth.js'
import { handleStreamResponse, streamHeaders, streamUrl } from '../../api/request.js'

const ROOT = `${API_BASE_URL}/api/knowledge/v1`

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
  const response = await authFetch(`${ROOT}${path}`, init)
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

export function uploadDocument(baseId, file, { folderId = '', onProgress } = {}) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${ROOT}/bases/${encodeURIComponent(baseId)}/documents`)
    const token = getStoredToken()
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`)
    xhr.responseType = 'json'
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && onProgress) {
        onProgress(Math.round((event.loaded / event.total) * 100))
      }
    }
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

export async function getDocumentBlob(documentId, disposition = 'inline') {
  const response = await authFetch(
    `${ROOT}/documents/${encodeURIComponent(documentId)}/content?disposition=${disposition}`,
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

export function replacePermissions(baseId, items) {
  return request(`/bases/${encodeURIComponent(baseId)}/permissions`, {
    method: 'PUT',
    json: { items },
  })
}

export function searchPermissionSubjects(baseId, type, q = '') {
  const params = new URLSearchParams({ type, q })
  return request(`/bases/${encodeURIComponent(baseId)}/permission-subjects?${params}`)
}

export function askKnowledgeBase(baseId, payload) {
  return request(`/bases/${encodeURIComponent(baseId)}/ask`, { method: 'POST', json: payload })
}

export function prepareKnowledgeChatSession(baseId) {
  return request(`/bases/${encodeURIComponent(baseId)}/chat-session`, { method: 'POST' })
}

export async function askKnowledgeBaseStream(baseId, payload, onChunk, signal) {
  const response = await handleStreamResponse(
    await fetch(streamUrl(`/api/knowledge/v1/bases/${encodeURIComponent(baseId)}/ask/stream`), {
      method: 'POST',
      headers: streamHeaders(),
      body: JSON.stringify(payload),
      signal,
    })
  )
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        let event
        try { event = JSON.parse(line.slice(6)) } catch (_) { continue }
        if (event.type === 'error') {
          const error = new Error(event.content || '知识库问答失败')
          error.code = event.code || 'KNOWLEDGE_AGENT_FAILED'
          throw error
        }
        onChunk(event)
      }
    }
  } finally {
    reader.releaseLock()
  }
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
