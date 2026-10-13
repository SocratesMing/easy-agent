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

export async function installConnector(id) {
  const data = await request(
    { url: '/agent/connectors/install', method: 'post', data: { id } },
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
