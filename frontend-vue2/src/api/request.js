import axios from 'axios'
import { API_BASE_URL } from '../config.js'

export const AUTH_EXPIRED_EVENT = 'auth-expired'
export const USER_ACTIVITY_EVENT = 'user-activity'

const TOKEN_KEY = 'mini_agent_token'
const USERNAME_KEY = 'mini_agent_username'

export function dispatchUserActivity() {
  window.dispatchEvent(new CustomEvent(USER_ACTIVITY_EVENT))
}

export function dispatchAuthExpired() {
  window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT))
}

export function getStoredToken() {
  return localStorage.getItem(TOKEN_KEY)
}

export function getStoredUsername() {
  return localStorage.getItem(USERNAME_KEY)
}

export function storeAuth(token, username) {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USERNAME_KEY, username)
}

export function clearAuth() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USERNAME_KEY)
}

export function getAuthHeaders() {
  const token = getStoredToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export const request = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
})

request.interceptors.request.use((config) => {
  dispatchUserActivity()
  return {
    ...config,
    headers: {
      ...config.headers,
      ...getAuthHeaders(),
    },
  }
})

request.interceptors.response.use(
  (response) => response,
  (error) => {
    const response = error.response
    if (response && response.status === 401) {
      const detail = response.data && response.data.detail
      if (detail && /其他设备|被迫下线/.test(detail)) {
        localStorage.setItem('auth_kicked', '1')
      }
      clearAuth()
      dispatchAuthExpired()
      error.message = '登录已过期，请重新登录'
    }
    return Promise.reject(error)
  }
)

function getErrorDetail(error, fallbackMessage) {
  if (error.response && error.response.data) {
    if (typeof error.response.data.detail === 'string') {
      return error.response.data.detail
    }
    if (typeof error.response.data === 'string') {
      return error.response.data
    }
  }
  return fallbackMessage
}

export async function requestJson(config, fallbackMessage = '请求失败') {
  try {
    const response = await request(config)
    return response.data
  } catch (error) {
    error.message = getErrorDetail(error, fallbackMessage)
    throw error
  }
}

export async function requestText(config, fallbackMessage = '请求失败') {
  try {
    const response = await request({
      ...config,
      responseType: 'text',
      transformResponse: [(data) => data],
    })
    return response.data
  } catch (error) {
    error.message = getErrorDetail(error, fallbackMessage)
    throw error
  }
}

export async function requestBlob(config) {
  const response = await request({
    ...config,
    responseType: 'blob',
  })
  return response.data
}

export function streamUrl(path) {
  return `${API_BASE_URL}${path}`
}

export function streamHeaders(extraHeaders = {}) {
  return {
    'Content-Type': 'application/json',
    ...getAuthHeaders(),
    ...extraHeaders,
  }
}

export async function handleStreamResponse(response) {
  if (response.status === 401) {
    const detail = await response.json().catch(() => '')
    if (detail && /其他设备|被迫下线/.test(detail)) {
      localStorage.setItem('auth_kicked', '1')
    }
    clearAuth()
    dispatchAuthExpired()
    throw new Error('登录已过期，请重新登录')
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    const detail = payload?.detail
    const message = typeof detail === 'string'
      ? detail
      : detail?.message || payload?.message || `请求失败: ${response.status}`
    const error = new Error(message)
    error.status = response.status
    error.code = detail?.code || payload?.code || 'STREAM_REQUEST_FAILED'
    error.retryable = Boolean(detail?.retryable || payload?.retryable)
    throw error
  }
  return response
}
