import request from '../utils/request.js'

export async function getMemory() {
  return request(
    { url: '/agent/settings/memory', method: 'get' },
    '获取记忆失败'
  )
}

export async function updateMemory(content) {
  return request(
    { url: '/agent/settings/memory', method: 'put', data: { content } },
    '更新记忆失败'
  )
}

export async function getSystemPrompt() {
  return request(
    { url: '/agent/settings/system-prompt', method: 'get' },
    '获取系统提示词失败'
  )
}

export async function getSkills() {
  return request(
    { url: '/agent/settings/skills', method: 'get' },
    '获取 Skills 列表失败'
  )
}

export async function getModels() {
  return request(
    { url: '/agent/settings/models', method: 'get' },
    '获取模型列表失败'
  )
}

export async function getKnowledgeStatus() {
  return request(
    { url: '/agent/settings/knowledge', method: 'get' },
    '获取知识库状态失败'
  )
}

export async function getWebSearchStatus() {
  return request(
    { url: '/agent/settings/web-search', method: 'get' },
    '获取联网搜索状态失败'
  )
}

/**
 * 获取对话附件上传限额（后端 config.yaml 的 upload_limits 段）。
 * 返回 { enabled, max_file_size_mb, max_files_per_message, max_total_size_mb }。
 */
export async function getUploadLimits() {
  return request(
    { url: '/agent/settings/upload-limits', method: 'get' },
    '获取上传限额失败'
  )
}

/**
 * 获取当前用户的审批模式（设置 → 权限设置）。
 * 返回 { mode, available, locked_reason, auto_approved_tools, revision, updated_at, path }。
 * available=false 表示服务端 config.yaml 关了 hitl.enabled，两个选项都无效。
 */
export async function getApprovalSettings() {
  return request(
    { url: '/agent/settings/approval', method: 'get' },
    '获取权限设置失败'
  )
}

/**
 * 更新审批模式。mode 只接受 'manual' | 'auto'；服务端原子写文件，下一条消息装配 Agent 时生效
 * （保存时故意不驱逐缓存：清掉缓存会把正挂着审批卡片的会话一起丢掉，恢复即 409）。
 * 返回结构与 getApprovalSettings 一致（含保存后的 revision / updated_at）。
 */
export async function updateApprovalSettings(mode) {
  return request(
    { url: '/agent/settings/approval', method: 'put', data: { mode } },
    '保存权限设置失败'
  )
}
