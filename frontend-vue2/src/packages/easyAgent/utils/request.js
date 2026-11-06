/**
 * 统一请求层（模块唯一的传输实现，axios + 原生 fetch）。
 *
 * 返回合同（调用方按需选择，不靠 `result.data || result` 猜测）：
 * - request(config, fallback)     → 响应体（JSON 业务数据，即 response.data）；失败时 error.message 为后端 detail 或兜底文案
 * - requestRaw(config)            → 完整 axios 响应 { status, headers, data }（需要状态码 / 响应头时）
 * - requestText / requestBlob / requestArrayBuffer → 文本 / Blob / ArrayBuffer
 * - fetchWithAuth(url, init)      → 原生 fetch Response（流式 SSE、需要原生 body 时），不预读 body
 * - resolveUrl(path)              → 拼好前缀的地址（下载链接、iframe / img 预览、XHR 上传）
 *
 * 所有通道共用同一份运行时配置（runtime/config.js）：地址前缀、附加请求头、Agent token 存储键、
 * 401 续期与失效通知；配置在每次请求时读取，晚于 import 的 configureEasyAgent() 同样生效。
 *
 * 401：可续期时并发 401 合并为一次续期，原请求换新 token 最多重发一次；登录类接口与
 * 「被其他设备顶下线」不续期，直接失效。失效只清 Agent 自己的登录态并通知模块 / 宿主适配器，
 * 不派发 window 事件，不影响宿主登录态。
 *
 * 模块使用自己的 axios 实例：宿主 axios 实例上的拦截器（如 401 跳登录页）不会作用到这里。
 */
import axios from 'axios'
import { configureEasyAgent, getRuntime } from '../runtime/config.js'
import { emitRuntimeEvent } from '../runtime/events.js'

/** 登录态失效事件名（模块内部事件，见 runtime/events.js） */
export const AUTH_EXPIRED_EVENT = 'auth-expired'

/** 用户活动事件名：每次请求前派发（模块内部事件） */
export const USER_ACTIVITY_EVENT = 'user-activity'

const KICKED_DETAIL = /其他设备|被迫下线/
// 登录类接口（含 /api/auth 旧前缀）本身 401 不续期，否则续期过程中的登录请求会等待自己
const AUTH_ENDPOINT = /\/(agent|api)\/auth\//

let defaultRefresher = null
let renewing = null

/**
 * 兼容旧调用（侧边栏 SDK）：把旧字段映射到运行时配置。
 *   baseURL → api.baseURL；tokenKey / usernameKey → auth.*；
 *   refreshAuth → auth.refresh；onAuthExpired → auth.onExpired
 */
export function configureRequest(options = {}) {
  const api = {}
  const auth = {}
  if ('baseURL' in options) api.baseURL = options.baseURL || ''
  if ('tokenKey' in options) auth.tokenKey = options.tokenKey
  if ('usernameKey' in options) auth.usernameKey = options.usernameKey
  if ('refreshAuth' in options) auth.refresh = options.refreshAuth || null
  if ('onAuthExpired' in options) auth.onExpired = options.onAuthExpired || null
  configureEasyAgent({ api, auth })
}

/**
 * 注册默认续期函数（api/auth.js 用：配置了 auth.signIn 而没配 auth.refresh 时，
 * 401 续期即重新执行 signIn）。
 */
export function setDefaultAuthRefresher(fn) {
  defaultRefresher = typeof fn === 'function' ? fn : null
}

function authConfig() {
  return getRuntime().auth
}

/** 拼接模块请求地址：api.resolveUrl 优先，其次 api.baseURL 前缀；绝对地址原样返回 */
export function resolveUrl(path) {
  const value = String(path || '')
  if (/^[a-z][a-z0-9+.-]*:/i.test(value) || value.startsWith('//')) return value
  const api = getRuntime().api
  if (typeof api.resolveUrl === 'function') return api.resolveUrl(value)
  return (api.baseURL || '') + value
}

/** 旧名：流式接口地址 */
export const streamUrl = resolveUrl

function extraHeaders() {
  const fn = getRuntime().api.headers
  if (typeof fn !== 'function') return {}
  const headers = fn() || {}
  // 适配器不能借附加头覆盖 Agent 鉴权
  const out = {}
  for (const key of Object.keys(headers)) {
    if (key.toLowerCase() !== 'authorization') out[key] = headers[key]
  }
  return out
}

/** 派发用户活动事件（模块内部，标记用户仍在使用） */
export function dispatchUserActivity() {
  emitRuntimeEvent(USER_ACTIVITY_EVENT)
}

/** 派发登录态失效：模块内部事件 + 适配器 auth.onExpired 回调 */
export function dispatchAuthExpired(info = {}) {
  const payload = { reason: info.reason || 'expired', detail: info.detail || '' }
  emitRuntimeEvent(AUTH_EXPIRED_EVENT, payload)
  const onExpired = authConfig().onExpired
  if (typeof onExpired === 'function') {
    try {
      onExpired(payload)
    } catch (error) {
      console.error('[EasyAgent] auth.onExpired 出错:', error)
    }
  }
}

/** 读取本地存储的 token */
export function getStoredToken() {
  return localStorage.getItem(authConfig().tokenKey)
}

/** 读取本地存储的用户名 */
export function getStoredUsername() {
  return localStorage.getItem(authConfig().usernameKey)
}

/** 保存登录态（token + 用户名） */
export function storeAuth(token, username) {
  localStorage.setItem(authConfig().tokenKey, token)
  localStorage.setItem(authConfig().usernameKey, username)
}

/** 清除登录态 */
export function clearAuth() {
  localStorage.removeItem(authConfig().tokenKey)
  localStorage.removeItem(authConfig().usernameKey)
}

/** 生成带 Bearer token 的请求头（未登录时返回空对象） */
export function getAuthHeaders() {
  const token = getStoredToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

/** 模块请求头：适配器附加头 + 调用方头 + Agent 鉴权（不会带上宿主自己的 Authorization） */
export function moduleHeaders(extra = {}) {
  const headers = { ...extraHeaders(), ...extra }
  delete headers.authorization
  delete headers.Authorization
  return Object.assign(headers, getAuthHeaders())
}

function isKicked(detail) {
  return typeof detail === 'string' && KICKED_DETAIL.test(detail)
}

/** 401 未能续期：清理 Agent 登录态并派发失效 */
function expireAuth(detail) {
  // 打印原因，便于区分：空闲超时 / 被顶下线 / token 失效
  console.warn('[auth] 401 未授权，判定登出 | 原因:', detail || '(无 detail)')
  const reason = isKicked(detail) ? 'kicked' : 'expired'
  if (reason === 'kicked') localStorage.setItem('auth_kicked', '1')
  clearAuth()
  dispatchAuthExpired({ reason, detail: typeof detail === 'string' ? detail : '' })
}

function currentRefresher() {
  const auth = authConfig()
  if (typeof auth.refresh === 'function') return auth.refresh
  if (typeof auth.signIn === 'function' && defaultRefresher) return defaultRefresher
  return null
}

/** 登录类接口与「被顶下线」不续期 */
function canRenew(url, detail) {
  return !!currentRefresher() && !isKicked(detail) && !AUTH_ENDPOINT.test(url || '')
}

/** 续期 token：并发 401 合并为一次；token 已被别的请求换过则直接用新的 */
async function renewToken(usedToken) {
  const current = getStoredToken()
  if (current && current !== usedToken) return true
  if (!renewing) {
    const refresher = currentRefresher()
    renewing = (async () => {
      try {
        return !!(await refresher())
      } catch (_) {
        return false
      } finally {
        renewing = null
      }
    })()
  }
  return renewing
}

/**
 * 非 axios / fetch 通道（XHR 上传等无法自动重发的请求）遇到 401 时调用：
 * 能续期就在后台续期（本次请求仍按失败处理，用户重试即可），否则直接失效。
 */
export function notifyUnauthorized(url, detail) {
  if (canRenew(url, detail)) {
    renewToken(getStoredToken())
    return
  }
  expireAuth(detail)
}

/**
 * axios 实例：统一超时、鉴权头与 401 处理。
 * 显式设置 timeout / withCredentials / baseURL，不沿用宿主改过的 axios.defaults。
 */
const requestInstance = axios.create({
  timeout: 30000,
})

requestInstance.interceptors.request.use((config) => {
  dispatchUserActivity()
  const api = getRuntime().api
  const token = getStoredToken()
  const headers = { ...config.headers, ...extraHeaders() }
  // 宿主可能在 axios.defaults 上挂了自己的 Authorization：Agent 请求只带 Agent token
  delete headers.authorization
  if (token) headers.Authorization = `Bearer ${token}`
  else delete headers.Authorization
  const next = {
    ...config,
    withCredentials: Boolean(api.withCredentials),
    _authToken: token,
    headers,
  }
  if (!config._easyAgentUrl) {
    // 重发时 url 已经解析过，不重复加前缀
    next.url = resolveUrl(config.url)
    next.baseURL = ''
    next._easyAgentUrl = true
  }
  return next
})

requestInstance.interceptors.response.use(
  (response) => response,
  async (error) => {
    const response = error.response
    if (response && response.status === 401) {
      const config = error.config
      const detail = response.data && response.data.detail
      if (config && !config._authRetried && canRenew(config.url, detail)) {
        config._authRetried = true
        if (await renewToken(config._authToken)) return requestInstance(config)
      } else {
        expireAuth(detail)
      }
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
 * @param {Object} config axios 请求配置，如 { url, method, params, data, headers, signal }
 * @param {string} [fallbackMessage='请求失败'] 后端未返回 detail 时的兜底错误提示
 * @returns {Promise<any>} 响应体数据
 * @example
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
 */
export async function requestArrayBuffer(config) {
  const response = await requestInstance({
    ...config,
    responseType: 'arraybuffer',
  })
  return response.data
}

/**
 * 生成流式请求的请求头（含鉴权）
 *
 * @param {Object} [extra={}] 额外请求头
 * @returns {Object} 请求头对象
 */
export function streamHeaders(extra = {}) {
  return {
    'Content-Type': 'application/json',
    ...getAuthHeaders(),
    ...extra,
  }
}

/** 下载 / 预览链接上的鉴权参数（浏览器直接打开的地址无法带请求头） */
export function appendTokenParam(params) {
  const token = getStoredToken()
  if (token) params.set('token', token)
  return params
}

/**
 * 带鉴权头的原生 fetch（流式 / 需要原生 Response 的场景），401 处理与 axios 一致：
 * 可续期时换新 token 重发一次，否则清理 Agent 登录态并派发失效。
 *
 * @param {string} url 完整地址（用 resolveUrl 拼接）
 * @param {RequestInit} [init] fetch 参数，headers 会自动带上 Authorization
 * @returns {Promise<Response>} 响应（仍可能是 401，由调用方按失败处理）
 */
export async function fetchWithAuth(url, init = {}) {
  const send = () => {
    const options = { ...init, headers: moduleHeaders(init.headers) }
    if (!options.credentials && getRuntime().api.withCredentials) options.credentials = 'include'
    return fetch(url, options)
  }
  const usedToken = getStoredToken()
  const response = await send()
  if (response.status !== 401) return response
  const payload = await response.clone().json().catch(() => null)
  const detail = payload && payload.detail
  if (canRenew(url, detail)) return (await renewToken(usedToken)) ? send() : response
  expireAuth(detail)
  return response
}

/**
 * 校验流式响应：非 2xx 时抛出后端错误（401 的登录态处理已由 fetchWithAuth 完成）
 *
 * @param {Response} response fetch 响应对象
 * @returns {Promise<Response>} 正常响应
 */
export async function handleStreamResponse(response) {
  if (response.status === 401) {
    throw new Error('登录已过期，请重新登录')
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    // FastAPI HTTPException 的 detail 可能是字符串或 {code,message,retryable} 对象，
    // 直接 new Error(detail.detail) 会把对象变成 "[object Object]"
    const detail = payload?.detail
    const message = typeof detail === 'string'
      ? detail
      : detail?.message || payload?.message || `请求失败: ${response.status}`
    const error = new Error(message)
    error.status = response.status
    error.code = detail?.code || payload?.code
    error.retryable = Boolean(detail?.retryable ?? payload?.retryable)
    throw error
  }
  return response
}
