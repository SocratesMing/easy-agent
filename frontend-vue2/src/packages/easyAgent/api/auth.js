import request, {
  AUTH_EXPIRED_EVENT,
  USER_ACTIVITY_EVENT,
  clearAuth,
  dispatchAuthExpired,
  dispatchUserActivity,
  getAuthHeaders,
  getStoredToken,
  getStoredUsername,
  requestRaw,
  setDefaultAuthRefresher,
  storeAuth,
} from '../utils/request.js'
import { getRuntime } from '../runtime/config.js'

export {
  AUTH_EXPIRED_EVENT,
  USER_ACTIVITY_EVENT,
  clearAuth,
  dispatchAuthExpired,
  dispatchUserActivity,
  getAuthHeaders,
  getStoredToken,
  getStoredUsername,
  storeAuth,
}

export async function authFetch(url, options = {}) {
  const response = await requestRaw({
    url,
    method: options.method || 'get',
    headers: options.headers,
    data: options.body,
    signal: options.signal,
  })

  return {
    ok: response.status >= 200 && response.status < 300,
    status: response.status,
    json: async () => response.data,
    text: async () => String(response.data),
    blob: async () => response.data,
  }
}

// 登录：username 字段既接受用户名，也接受工号（后端按「用户名或工号」定位用户），
// 两种情况都必须校验密码。免密登录请用 passwordlessLogin。
export async function login(username, password) {
  const data = await request(
    {
      url: '/agent/auth/login',
      method: 'post',
      data: { username, password },
    },
    '登录失败'
  )
  storeAuth(data.access_token, data.username)
  return data
}

// 免密登录：用户名已存在直接登录；不存在则自动注册后登录。
// userId 为 0/空时由后端为新用户自动生成唯一 user_id。
export async function passwordlessLogin(username, userId = '0') {
  const data = await request(
    {
      url: '/agent/auth/login-passwordless',
      method: 'post',
      data: { username, user_id: userId },
    },
    '免密登录失败'
  )
  storeAuth(data.access_token, data.username)
  return data
}

// 注册：仅需用户名 + 密码 + 工号（工号必填且全局唯一，注册后不可更改）
export async function register(username, password, employeeId = '') {
  const data = await request(
    {
      url: '/agent/auth/register',
      method: 'post',
      data: {
        username,
        password,
        employee_id: employeeId,
      },
    },
    '注册失败'
  )
  storeAuth(data.access_token, data.username)
  return data
}

export async function logout() {
  clearAuth()
}

export async function notifyLogout() {
  try {
    await request({ url: '/agent/auth/logout', method: 'post' })
  } catch (error) {
    // Best-effort logout notification.
  }
}

export async function unregister() {
  const data = await request(
    { url: '/agent/auth/unregister', method: 'delete' },
    '注销失败'
  )
  clearAuth()
  return data
}

export async function listUsers() {
  return request(
    { url: '/agent/auth/admin/users', method: 'get' },
    '获取用户列表失败'
  )
}

export async function resetUserPassword(username) {
  return request(
    {
      url: `/agent/auth/admin/users/${encodeURIComponent(username)}/reset-password`,
      method: 'post',
    },
    '密码重置失败'
  )
}

export async function getAuthConfig() {
  return request({ url: '/agent/auth/config', method: 'get' })
}

export async function getCurrentUser() {
  const username = getStoredUsername()
  if (!username) return null

  try {
    return await request({
      url: '/agent/auth/me',
      method: 'get',
      params: { username },
    })
  } catch (error) {
    if (error.response && error.response.status === 404) {
      clearAuth()
      return null
    }
    error.message = '获取用户信息失败'
    throw error
  }
}

let signingIn = null

/**
 * 执行适配器 auth.signIn（宿主身份 → Agent token），并发调用合并为一次。
 *
 * signIn(helpers) 的 helpers：
 *   - passwordlessLogin(username, userId)：调用后端免密登录并保存 Agent token
 *   - setToken(token, username?)：宿主已持有 Agent token 时直接保存（不是宿主自己的登录凭证）
 * signIn 返回 false / null 表示无法登录；true 表示已拿到 token；
 * { profile: { username, organization_id, email } } 表示已拿到 token 并提供展示资料。
 *
 * @returns {Promise<null | { profile: object | null, contextLength: number | null }>}
 */
export function runSignIn() {
  const signIn = getRuntime().auth.signIn
  if (typeof signIn !== 'function') return Promise.resolve(null)
  if (signingIn) return signingIn
  signingIn = (async () => {
    let loginData = null
    const helpers = {
      passwordlessLogin: async (username, userId) => {
        loginData = await passwordlessLogin(username, userId)
        return loginData
      },
      setToken: (token, username = '') => {
        if (!token) throw new Error('setToken 需要 Agent token')
        storeAuth(token, username || '')
      },
    }
    try {
      const result = await signIn(helpers)
      if (!result) return null
      if (!getStoredToken()) throw new Error('auth.signIn 返回成功，但没有写入 Agent token')
      return {
        profile: (typeof result === 'object' && result.profile) || null,
        contextLength: (loginData && loginData.context_length) || null,
      }
    } finally {
      signingIn = null
    }
  })()
  return signingIn
}

// 配置了 auth.signIn 而没配 auth.refresh 时，401 续期 = 重新执行 signIn；
// 续期失败按失效处理（清 Agent 登录态、通知模块与适配器 auth.onExpired）
setDefaultAuthRefresher(async () => {
  try {
    if (await runSignIn()) return true
  } catch (error) {
    console.error('[EasyAgent] 续期（auth.signIn）失败:', error)
  }
  // 登录接口自身 401 时请求层已经清理并派发过失效，这里不重复派发
  if (getStoredToken()) {
    clearAuth()
    dispatchAuthExpired({ reason: 'expired', detail: '续期失败' })
  }
  return false
})
