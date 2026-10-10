import request, {
  dispatchAuthExpired,
  getStoredToken,
  handleStreamResponse,
  requestRaw,
  streamHeaders,
  streamUrl,
} from '../../utils/request.js'

const ROOT = '/agent/knowledge/v1'
const ADMIN_ROOT = '/agent/knowledge/v1/admin'

export const getKnowledgeCapabilities = () =>
  request({ url: `${ROOT}/capabilities`, method: 'get' }, '获取知识服务能力失败')

export const getKnowledgeStatus = () =>
  request({ url: `${ROOT}/status`, method: 'get' }, '获取知识服务状态失败')

export function listKnowledgeBases({ page = 1, pageSize = 100 } = {}) {
  return request(
    { url: `${ROOT}/bases`, method: 'get', params: { page, page_size: pageSize } },
    '加载知识库失败'
  )
}

export function createKnowledgeBase(payload) {
  return request({ url: `${ROOT}/bases`, method: 'post', data: payload }, '创建知识库失败')
}

export function updateKnowledgeBase(baseId, payload) {
  return request(
    { url: `${ROOT}/bases/${encodeURIComponent(baseId)}`, method: 'patch', data: payload },
    '更新知识库失败'
  )
}

export function deleteKnowledgeBase(baseId) {
  return request(
    { url: `${ROOT}/bases/${encodeURIComponent(baseId)}`, method: 'delete' },
    '删除知识库失败'
  )
}

export function listFolders(baseId) {
  return request(
    { url: `${ROOT}/bases/${encodeURIComponent(baseId)}/folders`, method: 'get' },
    '加载目录失败'
  )
}

export function createFolder(baseId, name, parentId = '') {
  return request(
    {
      url: `${ROOT}/bases/${encodeURIComponent(baseId)}/folders`,
      method: 'post',
      data: { name, parent_id: parentId || null },
    },
    '创建目录失败'
  )
}

export function updateFolder(folderId, name) {
  return request(
    { url: `${ROOT}/folders/${encodeURIComponent(folderId)}`, method: 'patch', data: { name } },
    '重命名目录失败'
  )
}

export function deleteFolder(folderId) {
  return request(
    { url: `${ROOT}/folders/${encodeURIComponent(folderId)}`, method: 'delete' },
    '删除目录失败'
  )
}

export function listDocuments(baseId, { page = 1, pageSize = 100, folderId = '', directOnly = false, status = '', q = '' } = {}) {
  const params = { page, page_size: pageSize }
  if (folderId) params.folder_id = folderId
  if (directOnly) params.direct_only = 'true'
  if (status) params.status = status
  if (q) params.q = q
  return request(
    { url: `${ROOT}/bases/${encodeURIComponent(baseId)}/documents`, method: 'get', params },
    '加载资料失败'
  )
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
  return request(
    {
      url: `${ROOT}/documents/${encodeURIComponent(documentId)}`,
      method: 'patch',
      data: { folder_id: folderId || null },
    },
    '移动资料失败'
  )
}

export function deleteDocument(documentId) {
  return request(
    { url: `${ROOT}/documents/${encodeURIComponent(documentId)}`, method: 'delete' },
    '删除资料失败'
  )
}

export function retryDocument(documentId) {
  return request(
    { url: `${ROOT}/documents/${encodeURIComponent(documentId)}/retry`, method: 'post' },
    '重试解析失败'
  )
}

export async function getDocumentBlob(documentId, disposition = 'inline') {
  const response = await requestRaw({
    url: `${ROOT}/documents/${encodeURIComponent(documentId)}/content`,
    method: 'get',
    params: { disposition },
    responseType: 'blob',
  })
  return {
    blob: response.data,
    contentDisposition: response.headers['content-disposition'] || '',
  }
}

export function listPermissions(baseId) {
  return request(
    { url: `${ROOT}/bases/${encodeURIComponent(baseId)}/permissions`, method: 'get' },
    '加载权限失败'
  )
}

export function replacePermissions(baseId, items) {
  return request(
    {
      url: `${ROOT}/bases/${encodeURIComponent(baseId)}/permissions`,
      method: 'put',
      data: { items },
    },
    '保存权限失败'
  )
}

export function searchPermissionSubjects(baseId, type, q = '') {
  return request(
    {
      url: `${ROOT}/bases/${encodeURIComponent(baseId)}/permission-subjects`,
      method: 'get',
      params: { type, q },
    },
    '搜索授权对象失败'
  )
}

export function askKnowledgeBase(baseId, payload) {
  return request(
    { url: `${ROOT}/bases/${encodeURIComponent(baseId)}/ask`, method: 'post', data: payload },
    '知识库问答失败'
  )
}

export function prepareKnowledgeChatSession(baseId) {
  return request(
    { url: `${ROOT}/bases/${encodeURIComponent(baseId)}/chat-session`, method: 'post' },
    '创建知识会话失败'
  )
}

export async function askKnowledgeBaseStream(baseId, payload, onChunk, signal) {
  const response = await handleStreamResponse(
    await fetch(streamUrl(`${ROOT}/bases/${encodeURIComponent(baseId)}/ask/stream`), {
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
  return request(
    { url: `${ROOT}/bases/${encodeURIComponent(baseId)}/ask/cancel`, method: 'post' },
    '停止回答失败'
  )
}

export function listKnowledgeOperations({ page = 1, pageSize = 100 } = {}) {
  return request(
    { url: `${ROOT}/operations`, method: 'get', params: { page, page_size: pageSize } },
    '加载任务失败'
  )
}

export function getSessionKnowledgeScope(sessionId) {
  return request(
    { url: `${ROOT}/sessions/${encodeURIComponent(sessionId)}/knowledge-scope`, method: 'get' },
    '加载知识范围失败'
  )
}

export function replaceSessionKnowledgeScope(sessionId, baseIds) {
  return request(
    {
      url: `${ROOT}/sessions/${encodeURIComponent(sessionId)}/knowledge-scope`,
      method: 'put',
      data: { base_ids: baseIds },
    },
    '保存知识范围失败'
  )
}

export { ROOT, ADMIN_ROOT }
