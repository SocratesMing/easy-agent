import { request, requestBlob, requestJson, requestText } from './request.js'

export async function uploadFile(sessionId, file, onProgress) {
  const formData = new FormData()
  formData.append('file', file)
  const response = await request({
    url: `/agent/sessions/${sessionId}/upload`,
    method: 'post',
    data: formData,
    onUploadProgress: (progressEvent) => {
      if (onProgress && progressEvent.total) {
        onProgress(Math.round((progressEvent.loaded * 100) / progressEvent.total))
      }
    },
  })
  return response.data
}

export async function getSessionFiles(sessionId) {
  return requestJson(
    { url: `/agent/sessions/${sessionId}/files`, method: 'get' },
    '获取文件列表失败'
  )
}

export async function getAllFiles() {
  return requestJson(
    { url: '/agent/sessions/files/all', method: 'get' },
    '获取所有文件失败'
  )
}

export async function deleteFile(sessionId, file) {
  return requestJson(
    { url: `/agent/sessions/${sessionId}/files/${file.id}`, method: 'delete' },
    '删除文件失败'
  )
}

export async function getUserProfile() {
  return requestJson(
    { url: '/agent/auth/profile', method: 'get' },
    '获取用户资料失败'
  )
}

export async function getSessionGeneratedFiles(sessionId) {
  return requestJson(
    { url: `/agent/files/session/${sessionId}`, method: 'get' },
    '获取生成的文件失败'
  )
}

export async function getWorkspaceTree(path = '', sessionId = null) {
  const params = {}
  if (path) params.path = path
  if (sessionId) params.session_id = sessionId
  return requestJson(
    { url: '/agent/files/workspace/tree', method: 'get', params },
    '获取工作区文件树失败'
  )
}

export async function getFileContent(filePath) {
  const text = await requestText(
    {
      url: '/agent/files/content',
      method: 'get',
      params: { file_path: filePath },
    },
    '获取文件内容失败'
  )
  try {
    return JSON.parse(text)
  } catch (error) {
    return text
  }
}

export async function downloadFile(filePath, fileName) {
  const blob = await requestBlob({
    url: `/agent/files/download/${filePath}`,
    method: 'get',
  })
  const blobUrl = window.URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = blobUrl
  link.download = fileName || filePath.split('/').pop()
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  window.URL.revokeObjectURL(blobUrl)
}

export async function updateUserProfile(data) {
  return requestJson(
    { url: '/agent/auth/profile', method: 'put', data },
    '更新用户资料失败'
  )
}
