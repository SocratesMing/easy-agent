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
  storeAuth,
} from '@/utils/request'

// 以下为登录态与请求工具的透传导出，供组件直接使用（如 App.vue 监听 AUTH_EXPIRED_EVENT）
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

/**
 * 带鉴权的 fetch 风格请求（返回 { ok, status, json, text, blob }）
 * 用于需要自定义处理响应体的场景（如取消任务、下载 blob）
 *
 * @param {string} url 完整请求地址
 * @param {Object} [options={}] 请求选项：{ method, headers, body, signal }
 * @returns {Promise<{ok: boolean, status: number, json: Function, text: Function, blob: Function}>} 响应封装
 * @example
 * authFetch('/agent/chat/cancel?session_id=1', { method: 'post' })
 */
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

/**
 * 免密登录：用户名已存在则直接登录，不存在则自动注册后登录
 * 登录成功后会把 token 与用户名写入本地存储
 *
 * @param {string} username 用户名
 * @param {string|number} [userId='0'] 用户 ID，传 0/空时由后端为新用户自动生成
 * @returns {Promise<{access_token: string, username: string, max_input_tokens?: number}>} 登录结果
 * @example
 * passwordlessLogin('demo', '1001')
 */
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

/**
 * 获取鉴权相关配置（如是否开启注册等）
 *
 * @returns {Promise<Object>} 鉴权配置
 * @example
 * getAuthConfig()
 */
export async function getAuthConfig() {
  return request({
    url: '/agent/auth/config',
    method: 'get',
  })
}
