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
  const pending = (Array.isArray(currentDocuments) ? currentDocuments : []).filter(item => (
    item.optimistic
    && item.base_id === baseId
    && (item.folder_id || '') === folderId
    && !serverIds.has(item.id)
  ))
  return [...pending, ...server]
}
