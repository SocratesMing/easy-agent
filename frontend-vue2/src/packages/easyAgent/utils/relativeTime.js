/**
 * 会话列表右侧的相对时间文案。
 *
 * 设计稿（历史会话列表）用的是「5分钟前 / 2天前」这类紧凑写法，所以这里不显示秒、
 * 不带空格；超过一周改显示日期，跨年补上年份，避免出现「昨天」这种需要额外判断的说法。
 */

const MINUTE = 60 * 1000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

/**
 * @param {string|number|Date} value 时间（ISO 字符串 / 时间戳 / Date）
 * @param {number} [now] 当前时间戳，便于单测固定时钟
 * @returns {string} 刚刚 / N分钟前 / N小时前 / N天前 / M月D日 / YYYY年M月D日；无效时间返回空串
 */
export function formatRelativeTime(value, now = Date.now()) {
  if (!value) return ''
  const time = value instanceof Date ? value.getTime() : new Date(value).getTime()
  if (!time || Number.isNaN(time)) return ''

  const diff = now - time
  // 时钟漂移导致的「未来时间」按刚刚处理，不显示负数
  if (diff < MINUTE) return '刚刚'
  if (diff < HOUR) return `${Math.floor(diff / MINUTE)}分钟前`
  if (diff < DAY) return `${Math.floor(diff / HOUR)}小时前`

  const days = Math.floor(diff / DAY)
  if (days < 7) return `${days}天前`

  const date = new Date(time)
  const monthDay = `${date.getMonth() + 1}月${date.getDate()}日`
  const currentYear = new Date(now).getFullYear()
  return date.getFullYear() === currentYear ? monthDay : `${date.getFullYear()}年${monthDay}`
}
