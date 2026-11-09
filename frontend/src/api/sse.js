/**
 * 统一 SSE 读取器（ARCH-05）。
 *
 * 后端事件格式：`data: {json}\n\n`。本模块是全前端唯一的 SSE 解析实现，
 * chat / expert / knowledge 等消费端只注册 onEvent 回调，避免 7 份重复解析器漂移。
 *
 * @param {Response} response fetch 响应（body 可读）
 * @param {(event: any) => void} onEvent 事件回调；回调抛错会中止读取并向上抛出
 * @param {{signal?: AbortSignal}} [options]
 */
export async function readSSE(response, onEvent, options = {}) {
  if (!response || !response.body) {
    throw new Error('SSE 响应不可读（response.body 为空）')
  }
  const { signal } = options
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  const emitLine = (rawLine) => {
    const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine
    if (!line.startsWith('data: ')) return
    let event
    try {
      event = JSON.parse(line.slice(6))
    } catch (error) {
      console.error('解析 SSE 数据失败:', error)
      return
    }
    // 解析错误只跳过该事件；handler 抛错则向上传播（调用方/上层 catch 处理）
    onEvent(event)
  }

  try {
    while (true) {
      if (signal?.aborted) {
        try { await reader.cancel() } catch (_) { /* ignore */ }
        return
      }
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let index = buffer.indexOf('\n')
      while (index >= 0) {
        emitLine(buffer.slice(0, index))
        buffer = buffer.slice(index + 1)
        index = buffer.indexOf('\n')
      }
    }
    // 末尾没有换行符的边界事件
    const tail = buffer.trim()
    if (tail.startsWith('data: ')) emitLine(tail)
  } catch (error) {
    if (error && error.name === 'AbortError') return
    throw error
  } finally {
    try { reader.releaseLock?.() } catch (_) { /* ignore */ }
  }
}
