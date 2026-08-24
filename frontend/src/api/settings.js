import { requestJson } from './request.js'

export async function getMemory() {
  return requestJson(
    { url: '/api/settings/memory', method: 'get' },
    '获取记忆失败'
  )
}

export async function updateMemory(content) {
  return requestJson(
    { url: '/api/settings/memory', method: 'put', data: { content } },
    '更新记忆失败'
  )
}

export async function getSystemPrompt() {
  return requestJson(
    { url: '/api/settings/system-prompt', method: 'get' },
    '获取系统提示词失败'
  )
}

export async function getSkills() {
  return requestJson(
    { url: '/api/settings/skills', method: 'get' },
    '获取 Skills 列表失败'
  )
}

export async function getMcpServers() {
  return requestJson(
    { url: '/api/settings/mcp', method: 'get' },
    '获取 MCP 配置失败'
  )
}

export async function getMcpMarket() {
  return requestJson(
    { url: '/api/settings/mcp/market', method: 'get' },
    '获取 MCP 市场失败'
  )
}

export async function addMcpFromMarket(name) {
  return requestJson(
    {
      url: '/api/settings/mcp/market/add',
      method: 'post',
      data: { name },
    },
    '添加 MCP 市场服务失败'
  )
}

export async function updateMcpServers(servers) {
  return requestJson(
    { url: '/api/settings/mcp', method: 'put', data: { servers } },
    '更新 MCP 配置失败'
  )
}

export async function addMcpServer(config) {
  return requestJson(
    {
      url: '/api/settings/mcp/server',
      method: 'post',
      data: { config },
    },
    '添加 MCP 服务失败'
  )
}

export async function deleteMcpServer(name) {
  return requestJson(
    {
      url: `/api/settings/mcp/server/${encodeURIComponent(name)}`,
      method: 'delete',
    },
    '删除 MCP 服务失败'
  )
}

export async function getModels() {
  return requestJson(
    { url: '/api/settings/models', method: 'get' },
    '获取模型列表失败'
  )
}
