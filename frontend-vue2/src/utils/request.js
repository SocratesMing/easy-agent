/**
 * 统一请求封装（axios）
 *
 * - 默认导出 request：业务接口统一入口，返回响应体 data，并统一兜底错误信息。
 *   API 模块统一 `import request from '@/utils/request'` 后直接调用：
 *   request({ url: '/xxx', method: 'get' })
 * - 另导出少量特殊场景方法：requestRaw（需要完整响应）、requestText / requestBlob /
 *   requestArrayBuffer（非 JSON 响应），以及流式请求（SSE / fetch）相关辅助方法。
 */
import axios from 'axios'
import { API_BASE_URL } from '../config.js'

/** 登录态失效事件名：401 时派发，由 App 监听并自动重新免密登录 */
export const AUTH_EXPIRED_EVENT = 'auth-expired'

/** 用户活动事件名：每次请求前派发，用于空闲检测 */
export const USER_ACTIVITY_EVENT = 'user-activity'

const TOKEN_KEY = 'mini_agent_token'
const USERNAME_KEY = 'mini_agent_username'

/** 派发用户活动事件（标记用户仍在使用，避免被判定为空闲） */
export function dispatchUserActivity() {
  window.dispatchEvent(new CustomEvent(USER_ACTIVITY_EVENT))
}

/** 派发登录态失效事件（token 过期 / 被顶下线） */
export function dispatchAuthExpired() {
  window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT))
}

/** 读取本地存储的 token */
export function getStoredToken() {
  return localStorage.getItem(TOKEN_KEY)
}

/** 读取本地存储的用户名 */
export function getStoredUsername() {
  return localStorage.getItem(USERNAME_KEY)
}

/** 保存登录态（token + 用户名） */
export function storeAuth(token, username) {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USERNAME_KEY, username)
}

/** 清除登录态 */
export function clearAuth() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USERNAME_KEY)
}

/** 生成带 Bearer token 的请求头（未登录时返回空对象） */
export function getAuthHeaders() {
  const token = getStoredToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

/** axios 实例：统一 baseURL、超时、鉴权头与 401 处理 */
const requestInstance = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
})

requestInstance.interceptors.request.use((config) => {
  dispatchUserActivity()
  return {
    ...config,
    headers: {
      ...config.headers,
      ...getAuthHeaders(),
    },
  }
})

requestInstance.interceptors.response.use(
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

/** 从错误中提取后端 detail，取不到时使用兜底提示 */
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

/**
 * 统一请求入口：发起请求并返回响应体（response.data）
 *
 * @param {Object} config axios 请求配置，如 { url, method, params, data, headers }
 * @param {string} [fallbackMessage='请求失败'] 后端未返回 detail 时的兜底错误提示
 * @returns {Promise<any>} 响应体数据
 * @example
 * // GET /agent/sessions
 * request({ url: '/agent/sessions', method: 'get' })
 * // POST /agent/sessions 带 body 与自定义错误提示
 * request({ url: '/agent/sessions', method: 'post', data: { title } }, '创建会话失败')
 */
export default async function request(config, fallbackMessage = '请求失败') {
  try {
    const response = await requestInstance(config)
    return response.data
  } catch (error) {
    error.message = getErrorDetail(error, fallbackMessage)
    throw error
  }
}

/**
 * 原始请求：返回完整 axios 响应（需要 status / headers 时使用）
 *
 * @param {Object} config axios 请求配置
 * @returns {Promise<import('axios').AxiosResponse>} 完整响应对象
 * @example
 * const res = await requestRaw({ url: '/agent/auth/profile', method: 'get' })
 */
export function requestRaw(config) {
  return requestInstance(config)
}

/**
 * 文本请求：按纯文本返回（避免 axios 自动 JSON 解析）
 *
 * @param {Object} config axios 请求配置
 * @param {string} [fallbackMessage='请求失败'] 兜底错误提示
 * @returns {Promise<string>} 文本响应
 * @example
 * requestText({ url: '/agent/files/content', method: 'get', params: { file_path } })
 */
export async function requestText(config, fallbackMessage = '请求失败') {
  try {
    const response = await requestInstance({
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

/**
 * Blob 请求：用于文件下载等二进制响应
 *
 * @param {Object} config axios 请求配置
 * @returns {Promise<Blob>} 二进制数据
 * @example
 * requestBlob({ url: '/agent/files/download/a.txt', method: 'get' })
 */
export async function requestBlob(config) {
  const response = await requestInstance({
    ...config,
    responseType: 'blob',
  })
  return response.data
}

/**
 * ArrayBuffer 请求：用于 PDF / Office 预览等需要原始字节的场景
 *
 * @param {Object} config axios 请求配置
 * @returns {Promise<ArrayBuffer>} 原始字节数据
 * @example
 * requestArrayBuffer({ url: '/agent/files/raw/a.pdf', method: 'get' })
 */
export async function requestArrayBuffer(config) {
  const response = await requestInstance({
    ...config,
    responseType: 'arraybuffer',
  })
  return response.data
}

/**
 * 拼接流式接口地址（SSE / fetch 使用）
 *
 * @param {string} path 接口路径，如 '/agent/chat/stream'
 * @returns {string} 完整请求地址
 * @example
 * streamUrl('/agent/chat/stream')
 */
export function streamUrl(path) {
  return `${API_BASE_URL}${path}`
}

/**
 * 生成流式请求的请求头（含鉴权）
 *
 * @param {Object} [extraHeaders={}] 额外请求头
 * @returns {Object} 请求头对象
 * @example
 * streamHeaders({ 'X-Trace-Id': 'abc' })
 */
export function streamHeaders(extraHeaders = {}) {
  return {
    'Content-Type': 'application/json',
    ...getAuthHeaders(),
    ...extraHeaders,
  }
}

/**
 * 校验流式响应：401 时清理登录态并派发失效事件，非 2xx 时抛出后端错误
 *
 * @param {Response} response fetch 响应对象
 * @returns {Promise<Response>} 正常响应
 * @example
 * const res = await handleStreamResponse(await fetch(streamUrl('/agent/chat/stream')))
 */
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
    const detail = await response.json().catch(() => ({}))
    throw new Error(detail.detail || `请求失败: ${response.status}`)
  }
  return response
}
