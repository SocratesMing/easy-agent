import request, { requestBlob } from '../utils/request.js'
import { buildUploadLimitError, createFilesApi } from '../shared/api/files.js'

export async function uploadFile(sessionId, file, onProgress, signal) {
  const formData = new FormData()
  formData.append('file', file)
  try {
    return await request({
      url: `/agent/sessions/${sessionId}/upload`,
      method: 'post',
      data: formData,
      signal,
      onUploadProgress: (progressEvent) => {
        if (onProgress && progressEvent.total) {
          onProgress(Math.round((progressEvent.loaded * 100) / progressEvent.total))
        }
      },
    })
  } catch (error) {
    const detail =
      error && error.response && error.response.data
        ? error.response.data.detail
        : null
    const normalized = buildUploadLimitError(detail)
    if (normalized) throw normalized
    throw error
  }
}

export const {
  deleteFile,
  getUserProfile,
  getSessionGeneratedFiles,
  getWorkspaceTree,
  downloadFile,
  updateUserProfile,
} = createFilesApi(request, requestBlob)
