import { API_BASE_URL } from '../config.js'
import { authFetch } from './auth.js'

export async function enhancePrompt(prompt, signal) {
  const controller = new AbortController()
  const abort = () => controller.abort()
  if (signal?.aborted) abort()
  signal?.addEventListener('abort', abort, { once: true })
  let timedOut = false
  const timer = setTimeout(() => { timedOut = true; abort() }, 35000)
  try {
    const response = await authFetch(`${API_BASE_URL}/agent/prompts/enhance`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt }), signal: controller.signal,
    })
    if (!response.ok) {
      const error = new Error('提示词增强失败，请重试')
      error.status = response.status
      throw error
    }
    return await response.json()
  } catch (error) {
    if (timedOut) { const timeout = new Error('timeout'); timeout.name = 'TimeoutError'; throw timeout }
    throw error
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', abort)
  }
}
