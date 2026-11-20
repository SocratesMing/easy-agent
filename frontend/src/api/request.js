/**
 * Vue 3 基线统一传输适配器：把 `frontend-shared/api/*` 的统一 `{ url, method, params, data }`
 * 调用映射到 `authFetch` + `API_BASE_URL`，与 Vue 2 基线 `utils/request.js` 保持同一签名。
 */
import { API_BASE_URL } from '../config.js'
import { authFetch } from './auth.js'

function buildUrl(url, params) {
  let fullUrl = `${API_BASE_URL}${url}`
  if (params && typeof params === 'object') {
    const search = new URLSearchParams()
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null) search.set(k, String(v))
    }
    const qs = search.toString()
    if (qs) {
      fullUrl += (fullUrl.includes('?') ? '&' : '?') + qs
    }
  }
  return fullUrl
}

function buildFetchOptions(method, data) {
  const options = { method: (method || 'GET').toUpperCase() }
  if (data !== undefined) {
    if (typeof FormData !== 'undefined' && data instanceof FormData) {
      options.body = data
    } else {
      options.headers = { 'Content-Type': 'application/json' }
      options.body = JSON.stringify(data)
    }
  }
  return options
}

export async function request(config, fallback = '请求失败') {
  const fullUrl = buildUrl(config.url, config.params)
  const response = await authFetch(fullUrl, buildFetchOptions(config.method, config.data))
  const text = await response.text()
  let parsed = null
  if (text) {
    try {
      parsed = JSON.parse(text)
    } catch {
      parsed = text
    }
  }
  if (!response.ok) {
    if (parsed && typeof parsed === 'object' && parsed.detail) {
      throw new Error(typeof parsed.detail === 'string' ? parsed.detail : fallback)
    }
    if (response.status === 404) {
      throw new Error(`${fallback}：接口不存在 ${fullUrl}（后端可能未重启或版本过旧）`)
    }
    throw new Error(fallback)
  }
  return parsed
}

export async function requestBlob(config, fallback = '下载失败') {
  const fullUrl = buildUrl(config.url, config.params)
  const response = await authFetch(fullUrl, buildFetchOptions(config.method, config.data))
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(data.detail || fallback)
  }
  return await response.blob()
}

export default request
