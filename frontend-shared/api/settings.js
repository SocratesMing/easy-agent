/**
 * 设置页 API 工厂（双端共享契约，由宿主注入 request 传输适配器）。
 */
export function createSettingsApi(request) {
  return {
    async getMemory() {
      return request({ url: '/agent/settings/memory', method: 'get' }, '获取记忆失败')
    },

    async updateMemory(content) {
      return request(
        { url: '/agent/settings/memory', method: 'put', data: { content } },
        '更新记忆失败'
      )
    },

    async getSystemPrompt() {
      return request(
        { url: '/agent/settings/system-prompt', method: 'get' },
        '获取系统提示词失败'
      )
    },

    async getVersionInfo() {
      return request({ url: '/agent/version', method: 'get' }, '获取版本信息失败')
    },

    async getSkills() {
      return request(
        { url: '/agent/settings/skills', method: 'get' },
        '获取 Skills 列表失败'
      )
    },

    async getModels() {
      return request(
        { url: '/agent/settings/models', method: 'get' },
        '获取模型列表失败'
      )
    },

    async getKnowledgeStatus() {
      return request(
        { url: '/agent/settings/knowledge', method: 'get' },
        '获取知识库状态失败'
      )
    },

    async getWebSearchStatus() {
      return request(
        { url: '/agent/settings/web-search', method: 'get' },
        '获取联网搜索状态失败'
      )
    },

    async getUploadLimits() {
      return request(
        { url: '/agent/settings/upload-limits', method: 'get' },
        '获取上传限额失败'
      )
    },

    async getApprovalSettings() {
      return request(
        { url: '/agent/settings/approval', method: 'get' },
        '获取权限设置失败'
      )
    },

    async updateApprovalSettings(mode) {
      return request(
        { url: '/agent/settings/approval', method: 'put', data: { mode } },
        '保存权限设置失败'
      )
    },
  }
}
