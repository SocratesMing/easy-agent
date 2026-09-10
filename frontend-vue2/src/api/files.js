import request, { requestBlob, requestText } from '@/utils/request'

/**
 * 上传文件到指定会话
 *
 * @param {string} sessionId 会话 ID
 * @param {File} file 待上传的文件对象
 * @param {Function} [onProgress] 上传进度回调，参数为 0-100 的百分比
 * @returns {Promise<Object>} 上传后的文件信息
 * @example
 * uploadFile('session-1', file, (percent) => console.log(percent))
 */
export async function uploadFile(sessionId, file, onProgress) {
  const formData = new FormData()
  formData.append('file', file)
  return request({
    url: `/agent/sessions/${sessionId}/upload`,
    method: 'post',
    data: formData,
    onUploadProgress: (progressEvent) => {
      if (onProgress && progressEvent.total) {
        onProgress(Math.round((progressEvent.loaded * 100) / progressEvent.total))
      }
    },
  })
}

/**
 * 获取指定会话的文件列表
 *
 * @param {string} sessionId 会话 ID
 * @returns {Promise<Array>} 文件列表
 * @example
 * getSessionFiles('session-1')
 */
export async function getSessionFiles(sessionId) {
  return request(
    {
      url: `/agent/sessions/${sessionId}/files`,
      method: 'get',
    },
    '获取文件列表失败'
  )
}

/**
 * 获取所有会话的文件
 *
 * @returns {Promise<Array>} 文件列表
 * @example
 * getAllFiles()
 */
export async function getAllFiles() {
  return request(
    {
      url: '/agent/sessions/files/all',
      method: 'get',
    },
    '获取所有文件失败'
  )
}

/**
 * 删除会话中的某个文件
 *
 * @param {string} sessionId 会话 ID
 * @param {Object} file 文件对象，需包含 id
 * @returns {Promise<Object>} 删除结果
 * @example
 * deleteFile('session-1', { id: 1 })
 */
export async function deleteFile(sessionId, file) {
  return request(
    {
      url: `/agent/sessions/${sessionId}/files/${file.id}`,
      method: 'delete',
    },
    '删除文件失败'
  )
}

/**
 * 获取当前登录用户的资料
 *
 * @returns {Promise<{username: string, organization_id: string, email: string}>} 用户资料
 * @example
 * getUserProfile()
 */
export async function getUserProfile() {
  return request(
    {
      url: '/agent/auth/profile',
      method: 'get',
    },
    '获取用户资料失败'
  )
}

/**
 * 获取会话中生成的文件列表
 *
 * @param {string} sessionId 会话 ID
 * @returns {Promise<Array>} 生成的文件列表
 * @example
 * getSessionGeneratedFiles('session-1')
 */
export async function getSessionGeneratedFiles(sessionId) {
  return request(
    {
      url: `/agent/files/session/${sessionId}`,
      method: 'get',
    },
    '获取生成的文件失败'
  )
}

/**
 * 获取工作区文件树
 *
 * @param {string} [path=''] 子目录路径，为空表示根目录
 * @param {string|null} [sessionId=null] 会话 ID，用于限定会话工作区
 * @returns {Promise<Object>} 文件树节点
 * @example
 * getWorkspaceTree('', 'session-1')
 */
export async function getWorkspaceTree(path = '', sessionId = null) {
  const params = {}
  if (path) params.path = path
  if (sessionId) params.session_id = sessionId
  return request(
    {
      url: '/agent/files/workspace/tree',
      method: 'get',
      params,
    },
    '获取工作区文件树失败'
  )
}

/**
 * 获取文件内容（后端返回 JSON 时自动解析，否则返回文本）
 *
 * @param {string} filePath 文件路径
 * @returns {Promise<Object|string>} 文件内容
 * @example
 * getFileContent('demo/report.md')
 */
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

/**
 * 下载文件（以 blob 方式触发浏览器下载）
 *
 * @param {string} filePath 文件路径
 * @param {string} [fileName] 下载时使用的文件名，默认取路径最后一段
 * @returns {Promise<void>} 无返回值
 * @example
 * downloadFile('demo/report.xlsx', '报表.xlsx')
 */
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

