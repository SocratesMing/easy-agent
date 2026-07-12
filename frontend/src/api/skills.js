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
