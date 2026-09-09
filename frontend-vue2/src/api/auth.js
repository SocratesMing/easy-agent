import {
  AUTH_EXPIRED_EVENT,
  USER_ACTIVITY_EVENT,
  clearAuth,
  dispatchAuthExpired,
  dispatchUserActivity,
  getAuthHeaders,
  getStoredToken,
  getStoredUsername,
  request,
  requestJson,
  storeAuth,
} from './request.js'

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
  const response = await request({
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

export async function login(username, password) {
  const data = await requestJson(
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
  const data = await requestJson(
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

export async function register(username, password, organizationId, email = '') {
  const data = await requestJson(
    {
      url: '/agent/auth/register',
      method: 'post',
      data: {
        username,
        password,
        organization_id: organizationId,
        email,
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
  const data = await requestJson(
    { url: '/agent/auth/unregister', method: 'delete' },
    '注销失败'
  )
  clearAuth()
  return data
}

export async function listUsers() {
  return requestJson(
    { url: '/agent/auth/admin/users', method: 'get' },
    '获取用户列表失败'
  )
}

export async function resetUserPassword(username) {
  return requestJson(
    {
      url: `/agent/auth/admin/users/${encodeURIComponent(username)}/reset-password`,
      method: 'post',
    },
    '密码重置失败'
  )
}

export async function getAuthConfig() {
  return requestJson({ url: '/agent/auth/config', method: 'get' })
}

export async function getCurrentUser() {
  const username = getStoredUsername()
  if (!username) return null

  try {
    return await requestJson({
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
