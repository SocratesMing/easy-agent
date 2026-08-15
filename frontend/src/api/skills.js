import { API_BASE_URL } from '../config.js'
import { authFetch } from './auth.js'

export async function getPublicSkills() {
  const response = await authFetch(`${API_BASE_URL}/api/skill-center/public-skills`)
  if (!response.ok) throw new Error('获取公共技能列表失败')
  return await response.json()
}

export async function getUserSkills() {
  const response = await authFetch(`${API_BASE_URL}/api/skill-center/user-skills`)
  if (!response.ok) throw new Error('获取用户技能列表失败')
  return await response.json()
}

export async function addSkillToUser(dirName) {
  const response = await authFetch(`${API_BASE_URL}/api/skill-center/add-skill`, {
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
  const response = await authFetch(`${API_BASE_URL}/api/skill-center/remove-skill`, {
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
  const response = await authFetch(`${API_BASE_URL}/api/skill-center/import-skill`, {
    method: 'POST',
    body: formData,
  })
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(data.detail || '导入技能失败')
  }
  return await response.json()
}

export async function downloadSkill(dirName) {
  const response = await authFetch(`${API_BASE_URL}/api/skill-center/download-skill/${encodeURIComponent(dirName)}`)
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
