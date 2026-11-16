import request from '../utils/request.js'

export function enhancePrompt(prompt, signal) {
  return request({
    url: '/agent/prompts/enhance', method: 'post',
    data: { prompt }, signal, timeout: 35000,
  }, '提示词增强失败，请重试')
}
