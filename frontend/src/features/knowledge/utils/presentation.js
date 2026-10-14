export function processingElapsedText(document, operations, now = Date.now()) {
  const operation = operations.find(item =>
    item.resource_id === document.id && ['accepted', 'running'].includes(item.status)
  )
  const started = new Date(operation?.created_at || document.updated_at || document.created_at).getTime()
  if (!started) return ''

  const minutes = Math.max(0, Math.floor((now - started) / 60000))
  if (minutes < 1) return '刚刚开始'

  const sizeLabel = Number(document.size_bytes || 0) >= 20 * 1024 * 1024 ? '大文件 · ' : ''
  return `${sizeLabel}已用时 ${minutes} 分钟`
}
