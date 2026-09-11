import {
  handleStreamResponse,
  requestJson,
  streamHeaders,
  streamUrl,
} from './request.js'

export async function createSession(title, username = null) {
  const data = { title }
  if (username) data.username = username
  return requestJson(
    { url: '/agent/sessions', method: 'post', data },
    '创建会话失败'
  )
}

export async function listSessions(username = null) {
  return requestJson(
    {
      url: '/agent/sessions',
      method: 'get',
      params: username ? { username } : undefined,
    },
    '获取会话列表失败'
  )
}

export async function getSession(sessionId) {
  return requestJson(
    { url: `/agent/sessions/${sessionId}`, method: 'get' },
    '获取会话失败'
  )
}

export async function deleteSession(sessionId) {
  return requestJson(
    { url: `/agent/sessions/${sessionId}`, method: 'delete' },
    '删除会话失败'
  )
}

export async function renameSession(sessionId, title) {
  return requestJson(
    {
      url: `/agent/sessions/${sessionId}/title`,
      method: 'put',
      data: { title },
    },
    '重命名会话失败'
  )
}

export async function togglePinSession(sessionId) {
  return requestJson(
    { url: `/agent/sessions/${sessionId}/pin`, method: 'put' },
    '置顶操作失败'
  )
}

export async function getChatHistory(sessionId) {
  const data = await requestJson(
    { url: `/agent/sessions/${sessionId}`, method: 'get' },
    '获取聊天历史失败'
  )
  return {
    messages: data.messages || [],
    todos: data.todos || [],
    usage: data.usage || null,
    context_length: data.context_length || null,
  }
}

export async function getStreamStatus(sessionId) {
  return requestJson(
    {
      url: '/agent/chat/stream/status',
      method: 'get',
      params: { session_id: sessionId },
    },
    '查询流式状态失败'
  )
}

async function readSseStream(response, onChunk, abortSignal, controller) {
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      if (abortSignal && abortSignal.aborted) {
        controller.abort()
        return
      }

      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        try {
          const data = JSON.parse(line.slice(6))
          onChunk(data)
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

export async function attachStream(sessionId, onChunk, signal) {
  const controller = new AbortController()
  const abortSignal = signal || controller.signal
  const response = await handleStreamResponse(
    await fetch(streamUrl(`/agent/chat/stream/live?session_id=${encodeURIComponent(sessionId)}`), {
      signal: abortSignal,
      headers: streamHeaders(),
    })
  )
  await readSseStream(response, onChunk, abortSignal, controller)
}

export async function sendMessage(
  sessionId,
  message,
  onChunk,
  signal,
  enableDeepThink = true,
  files = [],
  model = null
) {
  const controller = new AbortController()
  const abortSignal = signal || controller.signal
  const payload = {
    session_id: sessionId,
    message,
    message_id: generateMessageId(),
    enable_deep_think: enableDeepThink,
    files,
  }
  if (model) payload.model = model

  const response = await handleStreamResponse(
    await fetch(streamUrl('/agent/chat/stream'), {
      method: 'POST',
      headers: streamHeaders(),
      body: JSON.stringify(payload),
      signal: abortSignal,
    })
  )
  await readSseStream(response, onChunk, abortSignal, controller)
}

export async function resumeStream(
  sessionId,
  threadId,
  decisions,
  onChunk,
  signal,
  messageId
) {
  const controller = new AbortController()
  const abortSignal = signal || controller.signal
  const response = await handleStreamResponse(
    await fetch(streamUrl('/agent/chat/resume'), {
      method: 'POST',
      headers: streamHeaders(),
      body: JSON.stringify({
        session_id: sessionId,
        thread_id: threadId,
        decisions,
        message_id: messageId || null,
      }),
      signal: abortSignal,
    })
  )
  await readSseStream(response, onChunk, abortSignal, controller)
}

function generateMessageId() {
  return `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`
}

export function createNewChat(sessionId, onChunk) {
  return sendMessage(sessionId, '', onChunk)
}
