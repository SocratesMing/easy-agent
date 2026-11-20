// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/api/skills.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
/**
 * 技能中心 API 工厂（双端共享契约，由宿主注入 request / requestBlob 传输适配器）。
 */
export function createSkillsApi(request, requestBlob) {
  async function postToggle(url, dirName) {
    return request(
      { url, method: 'post', data: { dir_name: dirName } },
      '操作失败'
    )
  }

  return {
    async getPublicSkills() {
      return request(
        { url: '/agent/skill-center/public-skills', method: 'get' },
        '获取公共技能列表失败'
      )
    },

    async getUserSkills() {
      return request(
        { url: '/agent/skill-center/user-skills', method: 'get' },
        '获取用户技能列表失败'
      )
    },

    async addSkillToUser(dirName) {
      return request(
        {
          url: '/agent/skill-center/add-skill',
          method: 'post',
          data: { dir_name: dirName },
        },
        '添加技能失败'
      )
    },

    async removeSkillFromUser(dirName) {
      return request(
        {
          url: '/agent/skill-center/remove-skill',
          method: 'post',
          data: { dir_name: dirName },
        },
        '移除技能失败'
      )
    },

    async importSkill(file) {
      const formData = new FormData()
      formData.append('file', file)
      return request(
        {
          url: '/agent/skill-center/import-skill',
          method: 'post',
          data: formData,
        },
        '导入技能失败'
      )
    },

    async getSkillCategories() {
      return request(
        { url: '/agent/skill-center/categories', method: 'get' },
        '获取技能类别失败'
      )
    },

    async downloadSkill(dirName) {
      const blob = await requestBlob(
        {
          url: `/agent/skill-center/download-skill/${encodeURIComponent(dirName)}`,
          method: 'get',
        },
        '下载技能失败'
      )
      const blobUrl = window.URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = blobUrl
      link.download = `${dirName}.zip`
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      window.URL.revokeObjectURL(blobUrl)
    },

    async enableSkill(dirName) {
      return postToggle('/agent/skill-center/enable-skill', dirName)
    },

    async disableSkill(dirName) {
      return postToggle('/agent/skill-center/disable-skill', dirName)
    },

    async getSkillDetail(dirName, scope) {
      return request(
        {
          url: '/agent/skill-center/skill-detail',
          method: 'get',
          params: { dir_name: dirName, scope },
        },
        '获取技能详情失败'
      )
    },
  }
}
