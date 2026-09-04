import { API_BASE_URL } from '../config.js'

const TOKEN_KEY = 'mini_agent_token'
const USERNAME_KEY = 'mini_agent_username'

export const AUTH_EXPIRED_EVENT = 'auth-expired'
// 用户活动事件：后端交互（API 调用/流式数据）时派发，前端据此重置空闲自动登出计时器
export const USER_ACTIVITY_EVENT = 'user-activity'

export function dispatchUserActivity() {
  window.dispatchEvent(new CustomEvent(USER_ACTIVITY_EVENT))
}

export function dispatchAuthExpired() {
  window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT))
}

export async function authFetch(url, options = {}) {
  // 任何后端 API 调用都视为用户活动，重置空闲登出计时器
  dispatchUserActivity()
  const token = getStoredToken()
  const headers = {
    ...options.headers,
  }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  const response = await fetch(url, {
    ...options,
    headers,
  })

  if (response.status === 401) {
    // 识别"被踢下线"场景（账号在其他设备/IP 登录），设置标记供登录页提示用户
    try {
      const errBody = await response.clone().json()
      if (errBody && errBody.detail && /其他设备|被迫下线/.test(errBody.detail)) {
        localStorage.setItem('auth_kicked', '1')
      }
    } catch (_) { /* ignore */ }
    clearAuth()
    dispatchAuthExpired()
    throw new Error('登录已过期，请重新登录')
  }

  return response
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
  if (token) {
    return {
      'Authorization': `Bearer ${token}`
    }
  }
  return {}
}

export async function login(username, password) {
  const response = await fetch(`${API_BASE_URL}/agent/auth/login`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({ username, password })
  })

  if (!response.ok) {
    const error = await response.json()
    const err = new Error(error.detail || '登录失败')
    err.status = response.status
    throw err
  }

  const data = await response.json()
  storeAuth(data.access_token, data.username)
  return data
}

// 免密登录：用户名已存在直接登录；不存在则自动注册后登录。
// userId 为 0/空时由后端为新用户自动生成唯一 user_id。
export async function passwordlessLogin(username, userId = '0') {
  const response = await fetch(`${API_BASE_URL}/agent/auth/login-passwordless`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({ username, user_id: userId })
  })

  if (!response.ok) {
    const error = await response.json().catch(() => null)
    const err = new Error((error && error.detail) || '免密登录失败')
    err.status = response.status
    throw err
  }

  const data = await response.json()
  storeAuth(data.access_token, data.username)
  return data
}

export async function register(username, password, organizationId, email = '') {
  const response = await fetch(`${API_BASE_URL}/agent/auth/register`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({ username, password, organization_id: organizationId, email })
  })

  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail || '注册失败')
  }

  const data = await response.json()
  storeAuth(data.access_token, data.username)
  return data
}

export async function logout() {
  clearAuth()
}

export async function notifyLogout() {
  try {
    await authFetch(`${API_BASE_URL}/agent/auth/logout`, { method: 'POST' })
  } catch (e) {
    // best-effort：登出通知失败不影响前端登出流程
  }
}

export async function unregister() {
  const response = await authFetch(`${API_BASE_URL}/agent/auth/unregister`, {
    method: 'DELETE'
  })

  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail || '注销失败')
  }

  clearAuth()
  return await response.json()
}

export async function listUsers() {
  const response = await authFetch(`${API_BASE_URL}/agent/auth/admin/users`)

  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail || '获取用户列表失败')
  }

  return await response.json()
}

export async function resetUserPassword(username) {
  const response = await authFetch(
    `${API_BASE_URL}/agent/auth/admin/users/${encodeURIComponent(username)}/reset-password`,
    { method: 'POST' }
  )

  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail || '密码重置失败')
  }

  return await response.json()
}

export async function getCurrentUser() {
  const username = getStoredUsername()
  if (!username) {
    return null
  }

  const response = await fetch(`${API_BASE_URL}/agent/auth/me?username=${encodeURIComponent(username)}`, {
    headers: {
      ...getAuthHeaders()
    }
  })

  if (!response.ok) {
    if (response.status === 404) {
      clearAuth()
      return null
    }
    throw new Error('获取用户信息失败')
  }

  return await response.json()
}
