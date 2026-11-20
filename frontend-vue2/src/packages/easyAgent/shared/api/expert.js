// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/api/expert.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
/**
 * 专家 Agent API 工厂（双端共享契约，由宿主注入 request 传输适配器）。
 */
export function createExpertApi(request) {
  return {
    async addExpert({ nameCn, prompt, sessionId = null, model = null }) {
      return request(
        {
          url: '/agent/add',
          method: 'post',
          data: {
            type: 'agent',
            name_cn: nameCn,
            prompt,
            session_id: sessionId,
            model,
          },
        },
        '创建专家失败'
      )
    },

    async listExperts({
      keyword = '',
      status = '',
      page = 1,
      pageSize = 20,
      includeDisabled = false,
    } = {}) {
      return request(
        {
          url: '/agent/list',
          method: 'get',
          params: {
            keyword,
            status,
            page,
            page_size: pageSize,
            include_disabled: includeDisabled,
          },
        },
        '获取专家列表失败'
      )
    },

    async listPublicExperts() {
      return request(
        { url: '/agent/public-list', method: 'get' },
        '获取公共专家列表失败'
      )
    },

    async getExpertMd(expertId) {
      return request(
        {
          url: '/agent/expert-md',
          method: 'get',
          params: { expert_id: expertId },
        },
        '获取专家提示词失败'
      )
    },

    async getSessionExperts(sessionId) {
      if (!sessionId) return { items: [] }
      return request(
        {
          url: '/agent/session-experts',
          method: 'get',
          params: { session_id: sessionId },
        },
        '获取会话内专家失败'
      )
    },

    async deleteExpert(exportAgentId, hard = false) {
      return request(
        {
          url: '/agent/del',
          method: 'post',
          data: { export_agent_id: exportAgentId, hard },
        },
        '删除专家失败'
      )
    },
  }
}
