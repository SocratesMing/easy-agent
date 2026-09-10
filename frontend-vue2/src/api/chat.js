import request, {
  handleStreamResponse,
  streamHeaders,
  streamUrl,
} from '@/utils/request'

/**
 * 创建新会话
 *
 * @param {string} title 会话标题
 * @param {string|null} [username=null] 归属用户名，不传时由后端按当前登录用户处理
 * @returns {Promise<{session_id: string}>} 新会话信息
 * @example
 * createSession('新会话', 'demo')
 */
export async function createSession(title, username = null) {
  const data = { title }
  if (username) data.username = username
  return request(
    {
      url: '/agent/sessions',
      method: 'post',
      data,
    },
    '创建会话失败'
  )
}

/**
 * 获取会话列表
 *
 * @param {string|null} [username=null] 用户名，不传时按当前登录用户
 * @returns {Promise<Array>} 会话列表
 * @example
 * listSessions('demo')
 */
export async function listSessions(username = null) {
  return request(
    {
      url: '/agent/sessions',
      method: 'get',
      params: username ? { username } : undefined,
    },
    '获取会话列表失败'
  )
}

/**
 * 获取单个会话详情
 *
 * @param {string} sessionId 会话 ID
 * @returns {Promise<Object>} 会话详情
 * @example
 * getSession('session-1')
 */
export async function getSession(sessionId) {
  return request(
    {
      url: `/agent/sessions/${sessionId}`,
      method: 'get',
    },
    '获取会话失败'
  )
}

/**
 * 删除会话
 *
 * @param {string} sessionId 会话 ID
 * @returns {Promise<Object>} 删除结果
 * @example
 * deleteSession('session-1')
 */
export async function deleteSession(sessionId) {
  return request(
    {
      url: `/agent/sessions/${sessionId}`,
      method: 'delete',
    },
    '删除会话失败'
  )
}

/**
 * 重命名会话
 *
 * @param {string} sessionId 会话 ID
 * @param {string} title 新的会话标题
 * @returns {Promise<Object>} 重命名结果
 * @example
 * renameSession('session-1', '需求讨论')
 */
export async function renameSession(sessionId, title) {
  return request(
    {
      url: `/agent/sessions/${sessionId}/title`,
      method: 'put',
      data: { title },
    },
    '重命名会话失败'
  )
}

/**
 * 切换会话置顶状态
 *
 * @param {string} sessionId 会话 ID
 * @returns {Promise<Object>} 操作结果
 * @example
 * togglePinSession('session-1')
 */
export async function togglePinSession(sessionId) {
  return request(
    {
      url: `/agent/sessions/${sessionId}/pin`,
      method: 'put',
    },
    '置顶操作失败'
  )
}

/**
 * 获取会话的聊天历史（含消息、执行计划、用量）
 *
 * @param {string} sessionId 会话 ID
 * @returns {Promise<{messages: Array, todos: Array, usage: Object|null, max_input_tokens: number|null}>} 历史数据
 * @example
 * getChatHistory('session-1')
 */
export async function getChatHistory(sessionId) {
  const data = await request(
    {
      url: `/agent/sessions/${sessionId}`,
      method: 'get',
    },
    '获取聊天历史失败'
  )
  return {
    messages: data.messages || [],
    todos: data.todos || [],
    usage: data.usage || null,
    max_input_tokens: data.max_input_tokens || null,
  }
}

/**
 * 查询会话的流式任务状态
 *
 * @param {string} sessionId 会话 ID
 * @returns {Promise<Object>} 流式状态
 * @example
 * getStreamStatus('session-1')
 */
export async function getStreamStatus(sessionId) {
  return request(
    {
      url: '/agent/chat/stream/status',
      method: 'get',
      params: { session_id: sessionId },
    },
    '查询流式状态失败'
  )
}

/**
 * 读取 SSE 流并逐块回调（内部方法）
 *
 * @param {Response} response fetch 流式响应
 * @param {Function} onChunk 每个 SSE 事件（已 JSON 解析）的回调
 * @param {AbortSignal} abortSignal 中止信号
 * @param {AbortController} controller 中止控制器
 * @returns {Promise<void>} 无返回值
 */
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

/**
 * 挂载（回放）指定会话正在进行的流式输出
 *
 * @param {string} sessionId 会话 ID
 * @param {Function} onChunk 每个流式事件的回调
 * @param {AbortSignal} [signal] 外部中止信号
 * @returns {Promise<void>} 无返回值
 * @example
 * attachStream('session-1', (evt) => console.log(evt))
 */
export async function attachStream(sessionId, onChunk, signal) {
  const controller = new AbortController()
  const abortSignal = signal || controller.signal
  const response = await handleStreamResponse(
    await fetch(
      streamUrl(`/agent/chat/stream/live?session_id=${encodeURIComponent(sessionId)}`),
      {
        signal: abortSignal,
        headers: streamHeaders(),
      }
    )
  )
  await readSseStream(response, onChunk, abortSignal, controller)
}

/**
 * 发送消息并以 SSE 方式接收流式回复
 *
 * @param {string} sessionId 会话 ID
 * @param {string} message 用户消息内容
 * @param {Function} onChunk 每个流式事件的回调
 * @param {AbortSignal} [signal] 外部中止信号（点停止时使用）
 * @param {boolean} [enableDeepThink=true] 是否开启深度思考
 * @param {Array} [files=[]] 随消息上传的文件
 * @param {string|null} [model=null] 指定模型，不传使用默认模型
 * @returns {Promise<void>} 无返回值
 * @example
 * sendMessage('session-1', '你好', (evt) => console.log(evt))
 */
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

/**
 * 恢复被中断（HITL 待审批）的流式会话
 *
 * @param {string} sessionId 会话 ID
 * @param {string} threadId 线程 ID
 * @param {Array} decisions 审批决策列表
 * @param {Function} onChunk 每个流式事件的回调
 * @param {AbortSignal} [signal] 外部中止信号
 * @param {string|null} [messageId=null] 关联的消息 ID
 * @returns {Promise<void>} 无返回值
 * @example
 * resumeStream('session-1', 'thread-1', [{ type: 'approve' }], onChunk)
 */
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

/**
 * 生成本条消息的唯一 ID（内部方法）
 *
 * @returns {string} 消息 ID
 */
function generateMessageId() {
  return `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`
}

/**
 * 发起一轮空消息以创建新会话的首轮回复
 *
 * @param {string} sessionId 会话 ID
 * @param {Function} onChunk 每个流式事件的回调
 * @returns {Promise<void>} 无返回值
 * @example
 * createNewChat('session-1', onChunk)
 */
export function createNewChat(sessionId, onChunk) {
  return sendMessage(sessionId, '', onChunk)
}
