/**
 * 模块内部事件通道（替代原来的 window CustomEvent）。
 *
 * 嵌入宿主后不能再派发 window 上的 `auth-expired` / `user-activity`：宿主可能监听同名事件
 * 做整站登出或空闲计时。这里的事件只在 Easy Agent 模块内流转。
 *
 * - auth-expired { reason, detail }：Agent 登录态失效（reason: expired | kicked）
 * - user-activity：模块发起请求（标记用户仍在使用）
 */
const listeners = {}

/** 订阅事件，返回取消订阅函数（组件卸载时务必调用） */
export function onRuntimeEvent(type, handler) {
  if (!listeners[type]) listeners[type] = []
  listeners[type].push(handler)
  return () => {
    const list = listeners[type] || []
    const index = list.indexOf(handler)
    if (index !== -1) list.splice(index, 1)
  }
}

export function emitRuntimeEvent(type, payload) {
  for (const handler of (listeners[type] || []).slice()) {
    try {
      handler(payload)
    } catch (error) {
      console.error(`[EasyAgent] ${type} 监听器出错:`, error)
    }
  }
}

/** 当前某事件的监听数（测试 / 泄漏排查用） */
export function runtimeListenerCount(type) {
  return (listeners[type] || []).length
}
