import request from '../utils/request.js'

export async function getMemory() {
  return request(
    { url: '/agent/settings/memory', method: 'get' },
    '获取记忆失败'
  )
}

export async function updateMemory(content) {
  return request(
    { url: '/agent/settings/memory', method: 'put', data: { content } },
    '更新记忆失败'
  )
}

export async function getSystemPrompt() {
  return request(
    { url: '/agent/settings/system-prompt', method: 'get' },
    '获取系统提示词失败'
  )
}

export async function getSkills() {
  return request(
    { url: '/agent/settings/skills', method: 'get' },
    '获取 Skills 列表失败'
  )
}

export async function getModels() {
  return request(
    { url: '/agent/settings/models', method: 'get' },
    '获取模型列表失败'
  )
}

export async function getMcpApiKeyStatuses() {
  return request(
    { url: '/agent/settings/mcp/api-keys', method: 'get' },
    '获取 MCP API Key 状态失败'
  )
}

export async function generateMcpApiKey(business) {
  return request(
    {
      url: '/agent/settings/mcp/api-key',
      method: 'post',
      data: { business },
    },
    '生成 MCP API Key 失败'
  )
}

export async function getWebSearchStatus() {
  return request(
    { url: '/agent/settings/web-search', method: 'get' },
    '获取联网搜索状态失败'
  )
}
