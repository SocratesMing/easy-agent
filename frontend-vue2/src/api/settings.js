import request from '@/utils/request'

/**
 * 获取当前用户的长期记忆内容
 *
 * @returns {Promise<{content: string}>} 记忆内容
 * @example
 * getMemory()
 */
export async function getMemory() {
  return request(
    {
      url: '/agent/settings/memory',
      method: 'get',
    },
    '获取记忆失败'
  )
}

/**
 * 更新当前用户的长期记忆内容
 *
 * @param {string} content 记忆内容（支持 Markdown）
 * @returns {Promise<Object>} 保存结果
 * @example
 * updateMemory('# 我的记忆')
 */
export async function updateMemory(content) {
  return request(
    {
      url: '/agent/settings/memory',
      method: 'put',
      data: { content },
    },
    '更新记忆失败'
  )
}

/**
 * 获取当前生效的系统提示词（只读）
 *
 * @returns {Promise<{content: string}>} 系统提示词
 * @example
 * getSystemPrompt()
 */
export async function getSystemPrompt() {
  return request(
    {
      url: '/agent/settings/system-prompt',
      method: 'get',
    },
    '获取系统提示词失败'
  )
}

/**
 * 获取 Skills 列表
 *
 * @returns {Promise<Array>} Skills 列表
 * @example
 * getSkills()
 */
export async function getSkills() {
  return request(
    {
      url: '/agent/settings/skills',
      method: 'get',
    },
    '获取 Skills 列表失败'
  )
}

/**
 * 获取 MCP 服务配置（含来源：用户配置 / 全局默认）
 *
 * @returns {Promise<{servers: Array, source: string}>} MCP 配置
 * @example
 * getMcpServers()
 */
export async function getMcpServers() {
  return request(
    {
      url: '/agent/settings/mcp',
      method: 'get',
    },
    '获取 MCP 配置失败'
  )
}

/**
 * 获取 MCP 市场可添加的服务列表
 *
 * @returns {Promise<{servers: Array}>} 市场服务列表
 * @example
 * getMcpMarket()
 */
export async function getMcpMarket() {
  return request(
    {
      url: '/agent/settings/mcp/market',
      method: 'get',
    },
    '获取 MCP 市场失败'
  )
}

/**
 * 从市场添加一个 MCP 服务
 *
 * @param {string} name 市场中的服务名
 * @returns {Promise<Object>} 添加结果
 * @example
 * addMcpFromMarket('amap')
 */
export async function addMcpFromMarket(name) {
  return request(
    {
      url: '/agent/settings/mcp/market/add',
      method: 'post',
      data: { name },
    },
    '添加 MCP 市场服务失败'
  )
}

/**
 * 全量更新 MCP 服务配置
 *
 * @param {Array} servers 服务配置数组
 * @returns {Promise<Object>} 保存结果
 * @example
 * updateMcpServers([{ name: 'demo', transport: 'stdio' }])
 */
export async function updateMcpServers(servers) {
  return request(
    {
      url: '/agent/settings/mcp',
      method: 'put',
      data: { servers },
    },
    '更新 MCP 配置失败'
  )
}

/**
 * 新增一个自定义 MCP 服务
 *
 * @param {Object} config 服务配置，如 { name, transport, command, args, env }
 * @returns {Promise<Object>} 新增结果
 * @example
 * addMcpServer({ name: 'demo', transport: 'stdio' })
 */
export async function addMcpServer(config) {
  return request(
    {
      url: '/agent/settings/mcp/server',
      method: 'post',
      data: { config },
    },
    '添加 MCP 服务失败'
  )
}

/**
 * 删除一个 MCP 服务
 *
 * @param {string} name 服务名
 * @returns {Promise<Object>} 删除结果
 * @example
 * deleteMcpServer('demo')
 */
export async function deleteMcpServer(name) {
  return request(
    {
      url: `/agent/settings/mcp/server/${encodeURIComponent(name)}`,
      method: 'delete',
    },
    '删除 MCP 服务失败'
  )
}

/**
 * 获取可选模型列表
 *
 * @returns {Promise<Array>} 模型列表
 * @example
 * getModels()
 */
export async function getModels() {
  return request(
    {
      url: '/agent/settings/models',
      method: 'get',
    },
    '获取模型列表失败'
  )
}

/**
 * 获取各业务 MCP API Key 的生成状态
 *
 * @returns {Promise<{businesses: Array}>} 业务与已生成状态列表
 * @example
 * getMcpApiKeyStatuses()
 */
export async function getMcpApiKeyStatuses() {
  return request(
    {
      url: '/agent/settings/mcp/api-keys',
      method: 'get',
    },
    '获取 MCP API Key 状态失败'
  )
}

/**
 * 为指定业务生成 MCP API Key
 *
 * @param {string} business 业务标识
 * @returns {Promise<{api_key: string}>} 生成的 Key
 * @example
 * generateMcpApiKey('default')
 */
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
