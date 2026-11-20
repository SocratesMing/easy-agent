// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/api/files.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
/**
 * 文件与用户资料 API 工厂（双端共享契约，由宿主注入 request / requestBlob 传输适配器）。
 *
 * 注：``uploadFile`` 因 Vue 2（axios AbortSignal）与 Vue 3（XHR xhrHolder）取消协议不同，
 * 由两侧薄封装保留，但共用本模块的 ``buildUploadLimitError`` 限额错误归一化逻辑。
 */
export function buildUploadLimitError(detail) {
  if (detail && typeof detail === 'object' && detail.message) {
    const err = new Error(detail.message)
    err.code = detail.code || ''
    err.limitMb = detail.limit_mb
    err.actualMb = detail.actual_mb
    return err
  }
  return null
}

export function createFilesApi(request, requestBlob) {
  return {
    async deleteFile(sessionId, file) {
      // 只有后端 session_files 的数字主键才可删；字符串 id 会被 FastAPI int 校验拒绝（422）。
      const fileId = Number(file && file.id)
      if (!Number.isInteger(fileId) || fileId <= 0) {
        return { status: 'skipped' }
      }
      return request(
        { url: `/agent/files/${fileId}`, method: 'delete' },
        '删除文件失败'
      )
    },

    async getUserProfile() {
      return request(
        { url: '/agent/auth/profile', method: 'get' },
        '获取用户资料失败'
      )
    },

    async getSessionGeneratedFiles(sessionId) {
      return request(
        { url: `/agent/files/session/${sessionId}`, method: 'get' },
        '获取生成的文件失败'
      )
    },

    async getWorkspaceTree(path = '', sessionId = null) {
      const params = {}
      if (path) params.path = path
      if (sessionId) params.session_id = sessionId
      return request(
        { url: '/agent/files/workspace/tree', method: 'get', params },
        '获取工作区文件树失败'
      )
    },

    async downloadFile(filePath, fileName) {
      const blob = await requestBlob(
        { url: `/agent/files/download/${filePath}`, method: 'get' },
        '下载失败'
      )
      const blobUrl = window.URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = blobUrl
      link.download = fileName || filePath.split('/').pop()
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      window.URL.revokeObjectURL(blobUrl)
    },

    async updateUserProfile(data) {
      return request(
        { url: '/agent/auth/profile', method: 'put', data },
        '更新用户资料失败'
      )
    },
  }
}
