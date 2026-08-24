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
      url: '/api/auth/login',
      method: 'post',
      data: { username, password },
    },
    '登录失败'
  )
  storeAuth(data.access_token, data.username)
  return data
}

export async function register(username, password, organizationId, email = '') {
  const data = await requestJson(
    {
      url: '/api/auth/register',
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
    await request({ url: '/api/auth/logout', method: 'post' })
  } catch (error) {
    // Best-effort logout notification.
  }
}

export async function unregister() {
  const data = await requestJson(
    { url: '/api/auth/unregister', method: 'delete' },
    '注销失败'
  )
  clearAuth()
  return data
}

export async function listUsers() {
  return requestJson(
    { url: '/api/auth/admin/users', method: 'get' },
    '获取用户列表失败'
  )
}

export async function resetUserPassword(username) {
  return requestJson(
    {
      url: `/api/auth/admin/users/${encodeURIComponent(username)}/reset-password`,
      method: 'post',
    },
    '密码重置失败'
  )
}

export async function getAuthConfig() {
  return requestJson({ url: '/api/auth/config', method: 'get' })
}

export async function getCurrentUser() {
  const username = getStoredUsername()
  if (!username) return null

  try {
    return await requestJson({
      url: '/api/auth/me',
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
