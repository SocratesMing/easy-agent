import request, { requestBlob, requestText } from '../utils/request.js'

export async function uploadFile(sessionId, file, onProgress, signal) {
  const formData = new FormData()
  formData.append('file', file)
  try {
    return await request({
      url: `/agent/sessions/${sessionId}/upload`,
      method: 'post',
      data: formData,
      // 用户点 × 删除"上传中"的文件时，通过 AbortSignal 中断请求
      signal,
      onUploadProgress: (progressEvent) => {
        if (onProgress && progressEvent.total) {
          onProgress(Math.round((progressEvent.loaded * 100) / progressEvent.total))
        }
      },
    })
  } catch (error) {
    // 限额类错误：后端 detail 是结构化对象（code/message/limit_mb/actual_mb），
    // 统一封装只取字符串 detail，这里归一成带 code 的 Error，便于卡片展示失败
    // 原因并决定是否给「重试」按钮。
    const detail =
      error && error.response && error.response.data
        ? error.response.data.detail
        : null
    if (detail && typeof detail === 'object' && detail.message) {
      const err = new Error(detail.message)
      err.code = detail.code || ''
      err.limitMb = detail.limit_mb
      err.actualMb = detail.actual_mb
      throw err
    }
    throw error
  }
}

export async function getSessionFiles(sessionId) {
  return request(
    { url: `/agent/sessions/${sessionId}/files`, method: 'get' },
    '获取文件列表失败'
  )
}

export async function getAllFiles() {
  return request(
    { url: '/agent/sessions/files/all', method: 'get' },
    '获取所有文件失败'
  )
}

export async function deleteFile(sessionId, file) {
  return request(
    { url: `/agent/sessions/${sessionId}/files/${file.id}`, method: 'delete' },
    '删除文件失败'
  )
}

export async function getUserProfile() {
  return request(
    { url: '/agent/auth/profile', method: 'get' },
    '获取用户资料失败'
  )
}

export async function getSessionGeneratedFiles(sessionId) {
  return request(
    { url: `/agent/files/session/${sessionId}`, method: 'get' },
    '获取生成的文件失败'
  )
}

export async function getWorkspaceTree(path = '', sessionId = null) {
  const params = {}
  if (path) params.path = path
  if (sessionId) params.session_id = sessionId
  return request(
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
  return request(
    { url: '/agent/auth/profile', method: 'put', data },
    '更新用户资料失败'
  )
}
