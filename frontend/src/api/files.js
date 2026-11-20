import { API_BASE_URL } from '../config.js'
import { getAuthHeaders } from './auth.js'
import request, { requestBlob } from './request.js'
import { buildUploadLimitError, createFilesApi } from '../shared/api/files.js'

export async function uploadFile(sessionId, file, onProgress, xhrHolder) {
  return new Promise((resolve, reject) => {
    const formData = new FormData()
    formData.append('file', file)

    const xhr = new XMLHttpRequest()
    if (xhrHolder) xhrHolder.xhr = xhr

    xhr.upload.addEventListener('progress', (progressEvent) => {
      if (onProgress && progressEvent.lengthComputable) {
        const percentCompleted = Math.round((progressEvent.loaded * 100) / progressEvent.total)
        onProgress(percentCompleted)
      }
    })

    xhr.addEventListener('abort', () => {
      reject(new Error('上传已取消'))
    })

    xhr.addEventListener('load', () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText))
        } catch {
          reject(new Error('Invalid response format'))
        }
      } else {
        try {
          const payload = JSON.parse(xhr.responseText)
          const detail = payload.detail
          const normalized = buildUploadLimitError(detail)
          if (normalized) {
            reject(normalized)
            return
          }
          reject(new Error(typeof detail === 'string' && detail ? detail : '文件上传失败'))
        } catch {
          reject(new Error('文件上传失败'))
        }
      }
    })

    xhr.addEventListener('error', () => {
      reject(new Error('网络错误，文件上传失败'))
    })

    xhr.open('POST', `${API_BASE_URL}/agent/sessions/${sessionId}/upload`)

    const authHeaders = getAuthHeaders()
    for (const [key, value] of Object.entries(authHeaders)) {
      xhr.setRequestHeader(key, value)
    }

    xhr.send(formData)
  })
}

export const {
  deleteFile,
  getUserProfile,
  getSessionGeneratedFiles,
  getWorkspaceTree,
  downloadFile,
  updateUserProfile,
} = createFilesApi(request, requestBlob)
