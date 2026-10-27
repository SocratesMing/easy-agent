import { API_BASE_URL } from '../config.js'
import { getAuthHeaders, authFetch } from './auth.js'
import { readSSE } from './sse.js'

export async function createSession(title, username = null) {
  const body = { title }
  if (username) {
    body.username = username
  }
  const response = await authFetch(`${API_BASE_URL}/agent/sessions`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body)
  })
  if (!response.ok) throw new Error('创建会话失败')
  return response.json()
}

export async function listSessions(username = null) {
  let url = `${API_BASE_URL}/agent/sessions`
  if (username) {
    url += `?username=${encodeURIComponent(username)}`
  }
  const response = await authFetch(url)
  if (!response.ok) throw new Error('获取会话列表失败')
  return response.json()
}

export async function getSession(sessionId) {
  const response = await authFetch(`${API_BASE_URL}/agent/sessions/${sessionId}`)
  if (!response.ok) throw new Error('获取会话失败')
  return response.json()
}

export async function deleteSession(sessionId) {
  const response = await authFetch(`${API_BASE_URL}/agent/sessions/${sessionId}`, {
    method: 'DELETE',
  })
  if (!response.ok) throw new Error('删除会话失败')
  return response.json()
}

export async function renameSession(sessionId, title) {
  const response = await authFetch(`${API_BASE_URL}/agent/sessions/${sessionId}/title`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ title })
  })
  if (!response.ok) throw new Error('重命名会话失败')
  return response.json()
}

export async function togglePinSession(sessionId) {
  const response = await authFetch(`${API_BASE_URL}/agent/sessions/${sessionId}/pin`, {
    method: 'PUT',
  })
  if (!response.ok) throw new Error('置顶操作失败')
  return response.json()
}

export async function getChatHistory(sessionId) {
  const response = await authFetch(`${API_BASE_URL}/agent/sessions/${sessionId}`)
  if (!response.ok) throw new Error('获取聊天历史失败')
  const data = await response.json()
  return {
    messages: data.messages || [],
    todos: data.todos || [],
    usage: data.usage || null,
    context_length: data.context_length || null,
  }
}

// 查询会话当前是否仍有进行中的流式任务（页面刷新后判断是否需要重新挂载）
export async function getStreamStatus(sessionId) {
  const response = await authFetch(
    `${API_BASE_URL}/agent/chat/stream/status?session_id=${encodeURIComponent(sessionId)}`
  )
  if (!response.ok) {
    throw new Error('查询流式状态失败')
  }
  return response.json()
}

// 挂载到进行中的流式任务：先回放已产生的全部事件，再持续接收后续事件
export async function attachStream(sessionId, onChunk, signal) {
  const controller = new AbortController()
  const abortSignal = signal || controller.signal

  const response = await authFetch(
    `${API_BASE_URL}/agent/chat/stream/live?session_id=${encodeURIComponent(sessionId)}`,
    { signal: abortSignal }
  )
  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail || '挂载流式输出失败')
  }

  await readSSE(response, onChunk, { signal: abortSignal })
}

export async function sendMessage(sessionId, message, onChunk, signal, enableDeepThink = true, files = [], model = null, enableWebSearch = false, skills = [], pendingExpertId = '', expertAgentId = '', extra = null) {
  const controller = new AbortController()
  const abortSignal = signal || controller.signal

  const payload = {
      session_id: sessionId,
      message,
      message_id: generateMessageId(),
      enable_deep_think: enableDeepThink,
      enable_web_search: enableWebSearch,
      files: files,
  }
  if (model) {
    payload.model = model
  }
  // 已选技能以结构化字段提交（技能目录名），`/目录名` 由后端拼进模型输入（不进落库文本）。
  // 连接器不随消息提交：本会话启用哪些连接器由 /agent/connectors/session 单独设置。
  if (skills && skills.length) payload.skills = skills
  // 待修改专家 ID（专家面板「编辑」带入，不进消息正文）：后端校验归属（只接受
  // 本人创建的专家）后绑定为 save_expert 的修改目标
  if (pendingExpertId) payload.pending_expert_id = pendingExpertId
  // 本轮使用的专家（我的专家 ID / 公共专家目录名）：专家对话只有一个入口，
  // 后端据此加载提示词、补装依赖技能、做职责范围预检（命中即短路拒答）
  if (expertAgentId) payload.expert_agent_id = expertAgentId
  // 知识库问答的元数据硬过滤（日期区间 / 标签 / 来源），由知识库工作台传入
  if (extra && extra.knowledgeMetadataFilter) {
    payload.knowledge_metadata_filter = extra.knowledgeMetadataFilter
  }
  // 新对话首条消息携带的知识库范围：后端建会话后先落 scope 再检索，
  // 避免“选知识库”阶段提前创建空会话（已有会话仍走 knowledge-scope 接口）
  if (extra && Array.isArray(extra.knowledgeBaseIds) && extra.knowledgeBaseIds.length) {
    payload.knowledge_base_ids = extra.knowledgeBaseIds
  }

  const response = await authFetch(`${API_BASE_URL}/agent/chat/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
    signal: abortSignal
  })

  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail || '发送消息失败')
  }

  await readSSE(
    response,
    (data) => {
      if (data.type === 'done') {
        console.log('[SSE] Received done event:', JSON.stringify(data).substring(0, 500))
      }
      onChunk(data)
    },
    { signal: abortSignal }
  )
}

export async function resumeStream(sessionId, threadId, decisions, onChunk, signal, messageId) {
  const controller = new AbortController()
  const abortSignal = signal || controller.signal

  const response = await authFetch(`${API_BASE_URL}/agent/chat/resume`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      session_id: sessionId,
      thread_id: threadId,
      decisions,
      message_id: messageId || null,
    }),
    signal: abortSignal,
  })

  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail || '恢复执行失败')
  }

  await readSSE(response, onChunk, { signal: abortSignal })
}

function generateMessageId() {
  return `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`
}

export function createNewChat(sessionId, onChunk) {
  return sendMessage(sessionId, '', onChunk)
}
