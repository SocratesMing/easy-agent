import { API_BASE_URL } from '../config.js'

const TOKEN_KEY = 'mini_agent_token'
const USERNAME_KEY = 'mini_agent_username'

export const AUTH_EXPIRED_EVENT = 'auth-expired'

export function dispatchAuthExpired() {
  window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT))
}

export async function authFetch(url, options = {}) {
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
  const response = await fetch(`${API_BASE_URL}/api/auth/login`, {
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

export async function register(username, password, organizationId, email = '') {
  const response = await fetch(`${API_BASE_URL}/api/auth/register`, {
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

export async function unregister() {
  const response = await authFetch(`${API_BASE_URL}/api/auth/unregister`, {
    method: 'DELETE'
  })

  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail || '注销失败')
  }

  clearAuth()
  return await response.json()
}

export async function resetPassword(username, newPassword) {
  const response = await fetch(`${API_BASE_URL}/api/auth/reset-password`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({ username, new_password: newPassword })
  })

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

  const response = await fetch(`${API_BASE_URL}/api/auth/me?username=${encodeURIComponent(username)}`, {
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
