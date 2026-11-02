/**
 * 提示自动消失的定时器封装（后端 / 系统报错 toast 等）。
 *
 * 与 Vue 解耦：调用方传入「读取当前提示内容」「清空提示」两个回调，定时器函数也可注入，
 * 便于用假定时器做单元测试。
 *
 * 行为：
 * - 收到非空提示后 ttlMs 毫秒自动清空；
 * - 期间出现新提示会取消旧定时器并重新计时；
 * - 触发时只清掉仍是这条提示的内容，避免误清随后出现的新提示。
 *
 * @param {{ read: () => unknown, clear: () => void, ttlMs?: number,
 *   setTimer?: (fn: () => void, ms: number) => unknown,
 *   clearTimer?: (timer: unknown) => void }} options
 * @returns {(value: unknown) => void} 有新提示时调用；传空值只取消未触发的定时器
 */
export function createAutoDismiss({
  read,
  clear,
  ttlMs = 5000,
  setTimer = setTimeout,
  clearTimer = clearTimeout,
}) {
  let timer = null
  return function schedule(value) {
    if (timer !== null) clearTimer(timer)
    timer = null
    if (!value) return
    timer = setTimer(() => {
      timer = null
      if (read() === value) clear()
    }, ttlMs)
  }
}
