import { API_BASE_URL } from '../config.js'
import { authFetch } from './auth.js'

export async function getPublicSkills() {
  const response = await authFetch(`${API_BASE_URL}/agent/skill-center/public-skills`)
  if (!response.ok) throw new Error('获取公共技能列表失败')
  return await response.json()
}

export async function getUserSkills() {
  const response = await authFetch(`${API_BASE_URL}/agent/skill-center/user-skills`)
  if (!response.ok) throw new Error('获取用户技能列表失败')
  return await response.json()
}

export async function addSkillToUser(dirName) {
  const response = await authFetch(`${API_BASE_URL}/agent/skill-center/add-skill`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dir_name: dirName }),
  })
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(data.detail || '添加技能失败')
  }
  return await response.json()
}

export async function removeSkillFromUser(dirName) {
  const response = await authFetch(`${API_BASE_URL}/agent/skill-center/remove-skill`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dir_name: dirName }),
  })
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(data.detail || '移除技能失败')
  }
  return await response.json()
}

export async function importSkill(file) {
  const formData = new FormData()
  formData.append('file', file)
  const response = await authFetch(`${API_BASE_URL}/agent/skill-center/import-skill`, {
    method: 'POST',
    body: formData,
  })
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(data.detail || '导入技能失败')
  }
  return await response.json()
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

export async function downloadSkill(dirName) {
  const response = await authFetch(`${API_BASE_URL}/agent/skill-center/download-skill/${encodeURIComponent(dirName)}`)
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(data.detail || '下载技能失败')
  }
  const blob = await response.blob()
  const blobUrl = window.URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = blobUrl
  link.download = `${dirName}.zip`
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  window.URL.revokeObjectURL(blobUrl)
}
