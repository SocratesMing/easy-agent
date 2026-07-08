import { getAuthHeaders, authFetch } from './auth.js'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

export async function createSession(title, username = null) {
  const body = { title }
  if (username) {
    body.username = username
  }
  const response = await authFetch(`${API_BASE_URL}/api/sessions`, {
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
  let url = `${API_BASE_URL}/api/sessions`
  if (username) {
    url += `?username=${encodeURIComponent(username)}`
  }
  const response = await authFetch(url)
  if (!response.ok) throw new Error('获取会话列表失败')
  return response.json()
}

export async function getSession(sessionId) {
  const response = await authFetch(`${API_BASE_URL}/api/sessions/${sessionId}`)
  if (!response.ok) throw new Error('获取会话失败')
  return response.json()
}

export async function deleteSession(sessionId) {
  const response = await authFetch(`${API_BASE_URL}/api/sessions/${sessionId}`, {
    method: 'DELETE',
  })
  if (!response.ok) throw new Error('删除会话失败')
  return response.json()
}

export async function renameSession(sessionId, title) {
  const response = await authFetch(`${API_BASE_URL}/api/sessions/${sessionId}/title`, {
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
  const response = await authFetch(`${API_BASE_URL}/api/sessions/${sessionId}/pin`, {
    method: 'PUT',
  })
  if (!response.ok) throw new Error('置顶操作失败')
  return response.json()
}

export async function getChatHistory(sessionId) {
  const response = await authFetch(`${API_BASE_URL}/api/sessions/${sessionId}`)
  if (!response.ok) throw new Error('获取聊天历史失败')
  const data = await response.json()
  return {
    messages: data.messages || [],
    todos: data.todos || [],
    usage: data.usage || null,
    max_input_tokens: data.max_input_tokens || null,
  }
}

export async function sendMessage(sessionId, message, onChunk, signal, enableDeepThink = true, files = [], useKnowledgeBase = false, model = null) {
  const controller = new AbortController()
  const abortSignal = signal || controller.signal

  const payload = {
      session_id: sessionId,
      message,
      message_id: generateMessageId(),
      enable_deep_think: enableDeepThink,
      files: files,
      use_knowledge_base: useKnowledgeBase
  }
  if (model) {
    payload.model = model
  }

  const response = await authFetch(`${API_BASE_URL}/api/chat/stream`, {
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
            const data = JSON.parse(line.slice(6))
            if (data.type === 'done') {
              console.log('[SSE] Received done event:', JSON.stringify(data).substring(0, 500))
            }
            onChunk(data)
          } catch (e) {
            console.error('解析 SSE 数据失败:', e)
          }
        }
      }
    }
  } catch (e) {
    if (e.name === 'AbortError') {
      return
    }
    throw e
  }
}

export async function resumeStream(sessionId, threadId, decisions, onChunk, signal) {
  const controller = new AbortController()
  const abortSignal = signal || controller.signal

  const response = await authFetch(`${API_BASE_URL}/api/chat/resume`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      session_id: sessionId,
      thread_id: threadId,
      decisions,
    }),
    signal: abortSignal,
  })

  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail || '恢复执行失败')
  }

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
            const data = JSON.parse(line.slice(6))
            onChunk(data)
          } catch (e) {
            console.error('解析 SSE 数据失败:', e)
          }
        }
      }
    }
  } catch (e) {
    if (e.name === 'AbortError') {
      return
    }
    throw e
  }
}

function generateMessageId() {
  return `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`
}

export function createNewChat(sessionId, onChunk) {
  return sendMessage(sessionId, '', onChunk)
}
