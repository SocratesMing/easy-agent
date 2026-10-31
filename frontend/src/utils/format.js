/**
 * 通用格式化工具（2026-10-20 从 ChatInput.vue 抽出，供上传区与额度文案共用）。
 */

/**
 * 字节数转可读大小（B / KB / MB，保留 1 位小数）。
 *
 * @param {number} bytes 字节数
 * @returns {string} 例：1.5 KB
 */
export function formatSize(bytes) {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

/**
 * 时间转「多久以前」：30 天以内用相对时间（5分钟前 / 3小时前 / 昨天 / 12天前），
 * 更早的显示日期（2026年9月14日）；空值或无效时间返回 —。
 *
 * 2026-10-30 从 SkillListPanel.vue 抽出。
 *
 * @param {string|number|Date} iso 时间（ISO 字符串 / 时间戳 / Date）
 * @param {number} [now] 当前时间戳，便于单测固定时钟
 * @returns {string}
 */
export function formatRelativeDate(iso, now = Date.now()) {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  const diff = date.getTime() - now
  const day = 86400000
  const hour = 3600000
  const minute = 60000
  const rtf = new Intl.RelativeTimeFormat('zh-CN', { numeric: 'auto' })
  if (Math.abs(diff) >= day && Math.abs(diff) <= 30 * day) return rtf.format(Math.round(diff / day), 'day')
  if (Math.abs(diff) >= hour && Math.abs(diff) < day) return rtf.format(Math.round(diff / hour), 'hour')
  if (Math.abs(diff) < hour) return rtf.format(Math.round(diff / minute), 'minute')
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium' }).format(date)
}
