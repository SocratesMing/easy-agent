import { authFetch } from './auth.js'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

export async function getMemory() {
  const response = await authFetch(`${API_BASE_URL}/api/settings/memory`)
  if (!response.ok) throw new Error('获取记忆失败')
  return await response.json()
}

export async function updateMemory(content) {
  const response = await authFetch(`${API_BASE_URL}/api/settings/memory`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  })
  if (!response.ok) throw new Error('更新记忆失败')
  return await response.json()
}

export async function getSystemPrompt() {
  const response = await authFetch(`${API_BASE_URL}/api/settings/system-prompt`)
  if (!response.ok) throw new Error('获取系统提示词失败')
  return await response.json()
}

export async function getSkills() {
  const response = await authFetch(`${API_BASE_URL}/api/settings/skills`)
  if (!response.ok) throw new Error('获取 Skills 列表失败')
  return await response.json()
}

export async function getMcpServers() {
  const response = await authFetch(`${API_BASE_URL}/api/settings/mcp`)
  if (!response.ok) throw new Error('获取 MCP 配置失败')
  return await response.json()
}
