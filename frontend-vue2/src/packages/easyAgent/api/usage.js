import request from '../utils/request.js'
import { validateUsageDays } from '../shared/utils/tokenUsage.js'

export function getTokenUsage(days = 7, signal) {
  validateUsageDays(days)
  return request({ url: '/agent/usage', method: 'get', params: { days }, signal }, 'Token统计暂不可用，请重试')
}
