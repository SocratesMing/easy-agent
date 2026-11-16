// RAGFlow 当前不支持 PowerPoint 解析，上传前先在前端过滤，避免每次都收到上游错误
export const UNSUPPORTED_KNOWLEDGE_UPLOAD_MESSAGE = '暂不支持 PPT/PPTX 格式上传，请转换为 PDF 或 DOCX 后再上传'

function isUnsupportedKnowledgeUpload(fileName) {
  const extension = String(fileName || '').split('.').pop().toLowerCase()
  return extension === 'ppt' || extension === 'pptx'
}

export function splitUnsupportedUploads(items) {
  const supported = []
  let skipped = 0
  for (const item of items) {
    if (isUnsupportedKnowledgeUpload(item?.file?.name)) skipped += 1
    else supported.push(item)
  }
  return { supported, skipped }
}

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
    // 乐观行自己的上传阶段：queued 排队 / uploading 传输中 / saving 服务端处理中
    uploadState: 'queued',
    error: null,
    allowed_actions: [],
    original_available: false,
    optimistic: true,
    created_at: timestamp,
    updated_at: timestamp,
  }
}

// 每篇文档独立展示自己的上传进展，不跟同批其它文件共用进度。
export function optimisticUploadStatusText(document) {
  if (!document || !document.optimistic) return ''
  if (document.uploadState === 'saving') return '上传处理中'
  if (document.uploadState === 'queued') return '等待上传'
  const percent = Math.max(0, Math.min(100, Math.round(Number(document.progress || 0) * 100)))
  return percent >= 100 ? '上传处理中' : `上传中 ${percent}%`
}

export function markOptimisticUploadStarted(documents, optimisticId) {
  const row = documents.find(item => item.id === optimisticId)
  if (row) row.uploadState = 'uploading'
}

export function applyOptimisticUploadProgress(documents, optimisticId, value) {
  const row = documents.find(item => item.id === optimisticId)
  if (!row) return
  const percent = Math.max(0, Math.min(100, Number(value) || 0))
  row.progress = percent / 100
  row.uploadState = percent >= 100 ? 'saving' : 'uploading'
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
