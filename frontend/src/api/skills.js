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

async function postToggle(url, dirName) {
  const response = await authFetch(`${API_BASE_URL}${url}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dir_name: dirName }),
  })
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(data.detail || '操作失败')
  }
  return await response.json()
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
  const query = new URLSearchParams({ dir_name: dirName, scope })
  const response = await authFetch(`${API_BASE_URL}/agent/skill-center/skill-detail?${query}`)
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(data.detail || '获取技能详情失败')
  }
  return await response.json()
}

async function postCategory(url, payload, fallbackMessage) {
  const response = await authFetch(`${API_BASE_URL}${url}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(data.detail || fallbackMessage)
  }
  return await response.json()
}

/** 技能类别列表（含各技能的显式归属记录） */
export async function getSkillCategories() {
  const response = await authFetch(`${API_BASE_URL}/agent/skill-center/categories`)
  if (!response.ok) throw new Error('获取技能类别失败')
  return await response.json()
}

/** 新建类别 */
export async function createSkillCategory(name) {
  return postCategory('/agent/skill-center/create-category', { name }, '新建类别失败')
}

/** 重命名类别 */
export async function renameSkillCategory(categoryId, name) {
  return postCategory(
    '/agent/skill-center/rename-category',
    { category_id: categoryId, name },
    '重命名类别失败'
  )
}

/** 删除类别（其下技能回落自动归类） */
export async function deleteSkillCategory(categoryId) {
  return postCategory(
    '/agent/skill-center/delete-category',
    { category_id: categoryId },
    '删除类别失败'
  )
}

/** 调整类别顺序（按传入 id 顺序） */
export async function reorderSkillCategories(categoryIds) {
  return postCategory(
    '/agent/skill-center/reorder-categories',
    { category_ids: categoryIds },
    '调整类别顺序失败'
  )
}

/** 设置技能所属类别；categoryId 传空字符串表示恢复自动归类 */
export async function assignSkillCategory(dirName, categoryId) {
  return postCategory(
    '/agent/skill-center/assign-category',
    { dir_name: dirName, category_id: categoryId || '' },
    '更新技能类别失败'
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
