/**
 * 专家 Agent 接口封装
 *
 * 对应后端 easy_agent/api/expert.py：
 *   POST /agent/add                创建专家
 *   POST /agent/update             修改专家（新版本目录）
 *   GET  /agent/list               我的专家列表
 *   POST /agent/del                删除专家（默认软删）
 *   POST /agent/stream_export_run  选中专家执行（SSE）
 *
 * 流式请求沿用 utils/request.js 的 streamUrl / streamHeaders /
 * handleStreamResponse，SSE 事件格式与 /agent/chat/stream 完全一致。
 */
import request, {
  handleStreamResponse,
  streamHeaders,
  streamUrl,
} from '../utils/request.js'

/** 创建专家 */
export async function addExpert({ nameCn, prompt, tagId = '', sessionId = null, model = null }) {
  return request(
    {
      url: '/agent/add',
      method: 'post',
      data: {
        type: 'agent',
        name_cn: nameCn,
        prompt,
        tag_id: tagId,
        session_id: sessionId,
        model,
      },
    },
    '创建专家失败'
  )
}

/** 修改专家 */
export async function updateExpert({
  exportAgentId,
  nameCn = null,
  prompt = null,
  tagId = null,
  model = null,
}) {
  return request(
    {
      url: '/agent/update',
      method: 'post',
      data: {
        export_agent_id: exportAgentId,
        name_cn: nameCn,
        prompt,
        tag_id: tagId,
        modify_type: 'edit',
        model,
      },
    },
    '修改专家失败'
  )
}

/** 我的专家列表 */
export async function listExperts({
  keyword = '',
  status = '',
  page = 1,
  pageSize = 20,
  includeDisabled = false,
} = {}) {
  return request(
    {
      url: '/agent/list',
      method: 'get',
      params: {
        keyword,
        status,
        page,
        page_size: pageSize,
        include_disabled: includeDisabled,
      },
    },
    '获取专家列表失败'
  )
}

/** 删除专家（hard=true 时物理删除） */
export async function deleteExpert(exportAgentId, hard = false) {
  return request(
    {
      url: '/agent/del',
      method: 'post',
      data: { export_agent_id: exportAgentId, hard },
    },
    '删除专家失败'
  )
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

  const response = await handleStreamResponse(
    await fetch(streamUrl('/agent/stream_export_run'), {
      method: 'POST',
      headers: streamHeaders(),
      body: JSON.stringify({
        message: payload.message,
        session_id: payload.sessionId || null,
        message_id: payload.messageId || null,
        expert_agent_id: payload.expertAgentId || null,
        model: payload.model || null,
        enable_deep_think: !!payload.enableDeepThink,
        enable_web_search: !!payload.enableWebSearch,
        files: payload.files || null,
      }),
      signal: abortSignal,
    })
  )

  await readSseStream(response, onChunk, abortSignal)
}

/** 本地 SSE 解析（与 chat.js 的 readSseStream 行为一致，避免改动既有模块） */
async function readSseStream(response, onChunk, abortSignal) {
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      if (abortSignal && abortSignal.aborted) return
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        try {
          onChunk(JSON.parse(line.slice(6)))
        } catch (error) {
          console.error('解析 SSE 数据失败:', error)
        }
      }
    }
  } catch (error) {
    if (error.name === 'AbortError') return
    throw error
  }
}
