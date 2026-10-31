import request from '../utils/request.js'

// 后端接口约定返回 JSON。若接口不存在并被 SPA 兜底成 index.html，
// axios 在 200 + text/html 时会返回字符串，`data.connectors` 之类会静默变 undefined，
// 这里统一转成「接口不存在 / 后端未重启」这类可操作提示。
function ensureJson(data, url, fallback) {
  if (typeof data === 'string') {
    throw new Error(`${fallback}：后端未返回 JSON（${url}，通常是接口不存在或后端未重启）`)
  }
  return data
}

export async function getConnectors() {
  const data = await request({ url: '/agent/connectors', method: 'get' }, '获取连接器失败')
  return ensureJson(data, '/agent/connectors', '获取连接器失败')
}

export async function installConnector(id, values = {}, validate = true) {
  const data = await request(
    { url: '/agent/connectors/install', method: 'post', data: { id, values, validate } },
    '安装连接器失败'
  )
  return ensureJson(data, '/agent/connectors/install', '安装连接器失败')
}

export async function uninstallConnector(id) {
  const data = await request(
    { url: '/agent/connectors/uninstall', method: 'post', data: { id } },
    '卸载连接器失败'
  )
  return ensureJson(data, '/agent/connectors/uninstall', '卸载连接器失败')
}

/** 启用/停用已安装的连接器（开关，默认开启） */
export async function toggleConnector(id, enabled) {
  const url = '/agent/connectors/toggle'
  const data = await request({ url, method: 'post', data: { id, enabled } }, '切换连接器开关失败')
  return ensureJson(data, url, '切换连接器开关失败')
}

/** 查看已安装连接器实际暴露的工具列表（{tools, error}） */
export async function getConnectorTools(name) {
  const url = `/agent/connectors/tools?name=${encodeURIComponent(name)}`
  const data = await request({ url, method: 'get' }, '获取工具列表失败')
  return ensureJson(data, url, '获取工具列表失败')
}

/** 校验所有已安装 MCP 的连接状态（{statuses: [{name, status, tools_count, error}]}） */
export async function getConnectorsStatus() {
  const url = '/agent/connectors/status'
  const data = await request({ url, method: 'get' }, '获取连接状态失败')
  return ensureJson(data, url, '获取连接状态失败')
}

/** 修改已安装连接器的白名单配置字段（url / key 等，值为 {字段路径: 新值}） */
export async function updateConnectorConfig(id, values) {
  const url = '/agent/connectors/config'
  const data = await request({ url, method: 'put', data: { id, values } }, '保存连接器配置失败')
  return ensureJson(data, url, '保存连接器配置失败')
}

export async function deleteMcpServer(name) {
  const url = `/agent/connectors/mcp/${encodeURIComponent(name)}`
  const data = await request({ url, method: 'delete' }, '删除 MCP 服务失败')
  return ensureJson(data, url, '删除 MCP 服务失败')
}

export async function getMcpRaw() {
  const data = await request({ url: '/agent/connectors/mcp/raw', method: 'get' }, '读取 mcp.json 失败')
  return ensureJson(data, '/agent/connectors/mcp/raw', '读取 mcp.json 失败')
}

export async function saveMcpRaw(content) {
  const data = await request(
    { url: '/agent/connectors/mcp/raw', method: 'put', data: { content } },
    '保存 mcp.json 失败'
  )
  return ensureJson(data, '/agent/connectors/mcp/raw', '保存 mcp.json 失败')
}

/**
 * 查询会话级连接器开关（输入框「+」→ 连接器面板）。
 * 返回 { session_id, connectors }：connectors 为 null 表示该会话还没动过开关（全部启用），
 * 空数组表示用户把开关全部关闭 —— 两种状态含义不同。
 */
export async function getSessionConnectors(sessionId) {
  const url = `/agent/connectors/session?session_id=${encodeURIComponent(sessionId)}`
  const data = await request({ url, method: 'get' }, '获取会话连接器开关失败')
  return ensureJson(data, url, '获取会话连接器开关失败')
}

/**
 * 全量设置会话级连接器开关（仅本次会话有效，不写 mcp.json）。
 * connectors 为当前启用的连接器标识（目录 id / 自定义 server 名），空数组 = 全部关闭。
 */
export async function setSessionConnectors(sessionId, connectors) {
  const url = '/agent/connectors/session'
  const data = await request(
    { url, method: 'post', data: { session_id: sessionId, connectors } },
    '保存会话连接器开关失败'
  )
  return ensureJson(data, url, '保存会话连接器开关失败')
}
