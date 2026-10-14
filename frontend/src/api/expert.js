/**
 * 专家 Agent 接口封装（Vue3 / vite 版）
 *
 * 对应后端 easy_agent/api/expert.py：
 *   POST /agent/add                创建专家
 *   POST /agent/update             修改专家（新版本目录）
 *   GET  /agent/list               我的专家列表
 *   GET  /agent/public-list        公共（预置）专家列表（只读目录 exports/）
 *   POST /agent/del                删除专家（默认软删）
 *   POST /agent/stream_export_run  选中专家执行（SSE）
 *
 * 与 api/chat.js 保持同一套请求约定（API_BASE_URL + authFetch），
 * SSE 事件格式与 /agent/chat/stream 完全一致。
 */
import { API_BASE_URL } from '../config.js'
import { authFetch } from './auth.js'

function generateMessageId() {
  return `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`
}

async function _errorOf(response, fallback) {
  const data = await response.json().catch(() => ({}))
  return new Error(data.detail || fallback)
}

/** 创建专家（分类标签 tag_id 由大模型自动生成，无需前端传入） */
export async function addExpert({ nameCn, prompt, sessionId = null, model = null }) {
  const response = await authFetch(`${API_BASE_URL}/agent/add`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      type: 'agent',
      name_cn: nameCn,
      prompt,
      session_id: sessionId,
      model,
    }),
  })
  if (!response.ok) throw await _errorOf(response, '创建专家失败')
  return await response.json()
}

/** 修改专家 */
export async function updateExpert({
  exportAgentId,
  nameCn = null,
  prompt = null,
  model = null,
}) {
  const response = await authFetch(`${API_BASE_URL}/agent/update`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      export_agent_id: exportAgentId,
      name_cn: nameCn,
      prompt,
      modify_type: 'edit',
      model,
    }),
  })
  if (!response.ok) throw await _errorOf(response, '修改专家失败')
  return await response.json()
}

/** 我的专家列表 */
export async function listExperts({
  keyword = '',
  status = '',
  page = 1,
  pageSize = 20,
  includeDisabled = false,
} = {}) {
  const params = new URLSearchParams({
    keyword,
    status,
    page: String(page),
    page_size: String(pageSize),
    include_disabled: String(includeDisabled),
  })
  const response = await authFetch(`${API_BASE_URL}/agent/list?${params.toString()}`)
  if (!response.ok) throw await _errorOf(response, '获取专家列表失败')
  return await response.json()
}

/**
 * 公共（预置）专家列表：随项目内置的专家（`exports/` 目录），只读，
 * 不区分用户。字段与 listExperts 的 items 一致（另带 `scope: 'public'`）。
 */
export async function listPublicExperts() {
  const response = await authFetch(`${API_BASE_URL}/agent/public-list`)
  if (!response.ok) throw await _errorOf(response, '获取公共专家列表失败')
  return await response.json()
}

/** 删除专家（hard=true 时物理删除） */
export async function deleteExpert(exportAgentId, hard = false) {
  const response = await authFetch(`${API_BASE_URL}/agent/del`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ export_agent_id: exportAgentId, hard }),
  })
  if (!response.ok) throw await _errorOf(response, '删除专家失败')
  return await response.json()
}

/**
 * 选中专家执行（SSE 流式）
 *
 * @param {Object} payload { message, sessionId, expertAgentId, model, enableDeepThink, enableWebSearch, files }
 * @param {Function} onChunk 每个 SSE 事件回调（与聊天完全相同的结构）
 * @param {AbortSignal} [signal] 用于取消
 */
export async function streamExportRun(payload, onChunk, signal) {
  const controller = new AbortController()
  const abortSignal = signal || controller.signal

  const body = {
    message: payload.message,
    session_id: payload.sessionId || null,
    message_id: payload.messageId || generateMessageId(),
    expert_agent_id: payload.expertAgentId || null,
    enable_deep_think: !!payload.enableDeepThink,
    enable_web_search: !!payload.enableWebSearch,
    files: payload.files || null,
  }
  if (payload.model) body.model = payload.model

  const response = await authFetch(`${API_BASE_URL}/agent/stream_export_run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: abortSignal,
  })
  if (!response.ok) throw await _errorOf(response, '专家执行失败')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      if (abortSignal?.aborted) {
        controller.abort()
        return
      }
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            onChunk(JSON.parse(line.slice(6)))
          } catch (e) {
            console.error('解析 SSE 数据失败:', e)
          }
        }
      }
    }
  } catch (e) {
    if (e.name === 'AbortError') return
    throw e
  }
}
