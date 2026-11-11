import { API_BASE_URL } from '../config.js'
import { authFetch } from './auth.js'
import { validateUsageDays } from '../shared/utils/tokenUsage.js'

export async function getTokenUsage(days = 7, signal) {
  validateUsageDays(days)
  const response = await authFetch(`${API_BASE_URL}/agent/usage?days=${days}`, { signal, cache: 'no-store' })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    const error = new Error(typeof payload.detail === 'string' ? payload.detail : 'Token统计暂不可用，请重试')
    error.status = response.status
    throw error
  }
  return response.json()
}
