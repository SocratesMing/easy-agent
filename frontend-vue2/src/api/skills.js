import request, { requestBlob } from '../utils/request.js'

export async function getPublicSkills() {
  return request(
    { url: '/agent/skill-center/public-skills', method: 'get' },
    '获取公共技能列表失败'
  )
}

export async function getUserSkills() {
  return request(
    { url: '/agent/skill-center/user-skills', method: 'get' },
    '获取用户技能列表失败'
  )
}

export async function addSkillToUser(dirName) {
  return request(
    {
      url: '/agent/skill-center/add-skill',
      method: 'post',
      data: { dir_name: dirName },
    },
    '添加技能失败'
  )
}

export async function removeSkillFromUser(dirName) {
  return request(
    {
      url: '/agent/skill-center/remove-skill',
      method: 'post',
      data: { dir_name: dirName },
    },
    '移除技能失败'
  )
}

export async function importSkill(file) {
  const formData = new FormData()
  formData.append('file', file)
  return request({
    url: '/agent/skill-center/import-skill',
    method: 'post',
    data: formData,
  })
}
/** 技能类别列表（供列表页的分类筛选；分类在技能创建时由后端自动归类定稿） */
export async function getSkillCategories() {
  return request(
    { url: '/agent/skill-center/categories', method: 'get' },
    '获取技能类别失败'
  )
}

export async function downloadSkill(dirName) {
  const blob = await requestBlob({
    url: `/agent/skill-center/download-skill/${encodeURIComponent(dirName)}`,
    method: 'get',
  })
  const blobUrl = window.URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = blobUrl
  link.download = `${dirName}.zip`
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  window.URL.revokeObjectURL(blobUrl)
}
async function postToggle(url, dirName) {
  return request(
    { url, method: 'post', data: { dir_name: dirName } },
    '操作失败'
  )
}

/** 启用技能（移回 Agent 加载目录） */
export async function enableSkill(dirName) {
  return postToggle('/agent/skill-center/enable-skill', dirName)
}

/** 停用技能（移出 Agent 加载目录，Agent 不再加载） */
export async function disableSkill(dirName) {
  return postToggle('/agent/skill-center/disable-skill', dirName)
}

/** 技能详情：正文（SKILL.md）+ 元数据 + 真实统计 */
export async function getSkillDetail(dirName, scope) {
  return request(
    {
      url: '/agent/skill-center/skill-detail',
      method: 'get',
      params: { dir_name: dirName, scope },
    },
    '获取技能详情失败'
  )
}

