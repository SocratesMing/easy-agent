import { API_BASE_URL } from '../config.js'
import { authFetch } from './auth.js'

export async function getMemory() {
  const response = await authFetch(`${API_BASE_URL}/agent/settings/memory`)
  if (!response.ok) throw new Error('获取记忆失败')
  return await response.json()
}

export async function updateMemory(content) {
  const response = await authFetch(`${API_BASE_URL}/agent/settings/memory`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  })
  if (!response.ok) throw new Error('更新记忆失败')
  return await response.json()
}

export async function getSystemPrompt() {
  const response = await authFetch(`${API_BASE_URL}/agent/settings/system-prompt`)
  if (!response.ok) throw new Error('获取系统提示词失败')
  return await response.json()
}

export async function getSkills() {
  const response = await authFetch(`${API_BASE_URL}/agent/settings/skills`)
  if (!response.ok) throw new Error('获取 Skills 列表失败')
  return await response.json()
}

export async function getModels() {
  const response = await authFetch(`${API_BASE_URL}/agent/settings/models`)
  if (!response.ok) throw new Error('获取模型列表失败')
  return await response.json()
}

export async function getKnowledgeStatus() {
  const response = await authFetch(`${API_BASE_URL}/agent/settings/knowledge`)
  if (!response.ok) throw new Error('获取知识库状态失败')
  return await response.json()
}

export async function getWebSearchStatus() {
  const response = await authFetch(`${API_BASE_URL}/agent/settings/web-search`)
  if (!response.ok) throw new Error('获取联网搜索状态失败')
  return await response.json()
}

/**
 * 获取对话附件上传限额（后端 config.yaml 的 upload_limits 段）。
 * 返回 { enabled, max_file_size_mb, max_files_per_message, max_total_size_mb }。
 */
export async function getUploadLimits() {
  const response = await authFetch(`${API_BASE_URL}/agent/settings/upload-limits`)
  if (!response.ok) throw new Error('获取上传限额失败')
  return await response.json()
}
