import { API_BASE_URL } from '../config.js'
import { authFetch } from './auth.js'

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

export async function getMcpMarket() {
  const response = await authFetch(`${API_BASE_URL}/api/settings/mcp/market`)
  if (!response.ok) throw new Error('获取 MCP 市场失败')
  return await response.json()
}

export async function addMcpFromMarket(name) {
  const response = await authFetch(`${API_BASE_URL}/api/settings/mcp/market/add`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({}))
    throw new Error(err.detail || '添加 MCP 市场服务失败')
  }
  return await response.json()
}

export async function updateMcpServers(servers) {
  const response = await authFetch(`${API_BASE_URL}/api/settings/mcp`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ servers }),
  })
  if (!response.ok) throw new Error('更新 MCP 配置失败')
  return await response.json()
}

export async function addMcpServer(config) {
  const response = await authFetch(`${API_BASE_URL}/api/settings/mcp/server`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ config }),
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({}))
    throw new Error(err.detail || '添加 MCP 服务失败')
  }
  return await response.json()
}

export async function deleteMcpServer(name) {
  const response = await authFetch(`${API_BASE_URL}/api/settings/mcp/server/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({}))
    throw new Error(err.detail || '删除 MCP 服务失败')
  }
  return await response.json()
}

export async function getModels() {
  const response = await authFetch(`${API_BASE_URL}/api/settings/models`)
  if (!response.ok) throw new Error('获取模型列表失败')
  return await response.json()
}
