export function createOptimisticUploadDocument(file, item, now = new Date()) {
  const timestamp = now instanceof Date ? now.toISOString() : String(now)
  return {
    id: item.optimisticId,
    base_id: item.baseId,
    folder_id: item.folderId || null,
    name: file.name,
    content_type: file.type || 'application/octet-stream',
    size_bytes: Number(file.size || 0),
    status: 'pending',
    progress: 0,
    error: null,
    allowed_actions: [],
    original_available: false,
    optimistic: true,
    created_at: timestamp,
    updated_at: timestamp,
  }
}

export function mergeServerDocumentsWithOptimistic(
  serverDocuments,
  currentDocuments,
  { baseId, folderId = '', preserve = true } = {}
) {
  const server = Array.isArray(serverDocuments) ? serverDocuments : []
  if (!preserve) return server
  const serverIds = new Set(server.map(item => item.id))
  // 上传接口可能先落库再返回：按名称+大小+目录识别同一文件，避免服务端行与乐观行并存
  const uploadKey = item => (
    item && item.name && item.size_bytes != null
      ? `${item.name}\u0000${Number(item.size_bytes)}\u0000${item.folder_id || ''}`
      : ''
  )
  const serverKeys = new Set(server.map(uploadKey).filter(Boolean))
  const pending = (Array.isArray(currentDocuments) ? currentDocuments : []).filter(item => (
    item.optimistic
    && item.base_id === baseId
    && (item.folder_id || '') === folderId
    && !serverIds.has(item.id)
    && (!uploadKey(item) || !serverKeys.has(uploadKey(item)))
  ))
  return [...pending, ...server]
}
