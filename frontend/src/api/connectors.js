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

export async function installConnector(id, values = {}, validate = true) {
  return send('POST', '/install', { id, values, validate }, '安装连接器失败')
}

export async function uninstallConnector(id) {
  return send('POST', '/uninstall', { id }, '卸载连接器失败')
}

/** 启用/停用已安装的连接器（开关，默认开启） */
export async function toggleConnector(id, enabled) {
  return send('POST', '/toggle', { id, enabled }, '切换连接器开关失败')
}

/** 查看已安装连接器实际暴露的工具列表（{tools, error}） */
export async function getConnectorTools(name) {
  return send('GET', `/tools?name=${encodeURIComponent(name)}`, undefined, '获取工具列表失败')
}

/** 校验所有已安装 MCP 的连接状态（{statuses: [{name, status, tools_count, error}]}） */
export async function getConnectorsStatus() {
  return send('GET', '/status', undefined, '获取连接状态失败')
}

/** 修改已安装连接器的白名单配置字段（url / key 等，值为 {字段路径: 新值}） */
export async function updateConnectorConfig(id, values) {
  return send('PUT', '/config', { id, values }, '保存连接器配置失败')
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
