import { API_BASE_URL } from '../config.js'
import { authFetch } from './auth.js'

const BASE_URL = `${API_BASE_URL}/agent/connectors`

/**
 * 统一解析后端响应，不假设「一定是 JSON」。
 *
 * 若接口不存在且被 SPA 兜底，会返回 200 + text/html（index.html），
 * 直接 response.json() 会抛 "Unexpected token '<'"，把真实原因藏起来；
 * 这里统一转成「接口不存在 / 后端未重启」这类可操作提示。
 */
async function readJson(response, url, fallback) {
  const text = await response.text()
  let data = null
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = null // 非 JSON（多为 HTML）
    }
  }

  if (!response.ok) {
    if (data && data.detail) throw new Error(data.detail)
    if (response.status === 404) {
      throw new Error(`${fallback}：接口不存在 ${url}（后端可能未重启或版本过旧）`)
    }
    throw new Error(`${fallback}（HTTP ${response.status}）`)
  }

  if (data === null) {
    throw new Error(`${fallback}：后端未返回 JSON（${url}，通常是接口不存在或后端未重启）`)
  }
  return data
}

async function send(method, path, body, fallback) {
  const url = `${BASE_URL}${path}`
  const options = { method }
  if (body !== undefined) {
    options.headers = { 'Content-Type': 'application/json' }
    options.body = JSON.stringify(body)
  }
  const response = await authFetch(url, options)
  return readJson(response, url, fallback)
}

export async function getConnectors() {
  return send('GET', '', undefined, '获取连接器失败')
}

export async function installConnector(id) {
  return send('POST', '/install', { id }, '安装连接器失败')
}

export async function uninstallConnector(id) {
  return send('POST', '/uninstall', { id }, '卸载连接器失败')
}

export async function addMcpConfig(config) {
  return send('POST', '/mcp', { config }, '添加 MCP 服务失败')
}

export async function deleteMcpServer(name) {
  return send('DELETE', `/mcp/${encodeURIComponent(name)}`, undefined, '删除 MCP 服务失败')
}

export async function getMcpRaw() {
  return send('GET', '/mcp/raw', undefined, '读取 mcp.json 失败')
}

export async function saveMcpRaw(content) {
  return send('PUT', '/mcp/raw', { content }, '保存 mcp.json 失败')
}
