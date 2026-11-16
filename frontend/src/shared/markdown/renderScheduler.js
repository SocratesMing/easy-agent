// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/markdown/renderScheduler.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
/**
 * 流式 Markdown 的 trailing 节流器。
 *
 * 高频 token 只允许在固定间隔内渲染一次；后续 token 不断到来时**不能重置**已经排队的
 * 中间帧，否则持续输出会把首帧无限向后推。最终帧则从最后一次输入重新计时。
 */
export function createRenderScheduler({
  render,
  settle,
  frameMs = 80,
  settleMs = 150,
  now = () => Date.now(),
  schedule = (fn, delay) => setTimeout(fn, delay),
  cancel = (id) => clearTimeout(id),
} = {}) {
  let frameTimer = null
  let settleTimer = null
  let lastFrameAt = now()
  let disposed = false

  function cancelFrame() {
    if (frameTimer == null) return
    cancel(frameTimer)
    frameTimer = null
  }

  function cancelSettle() {
    if (settleTimer == null) return
    cancel(settleTimer)
    settleTimer = null
  }

  function queue() {
    if (disposed) return
    cancelSettle()
    if (frameTimer == null) {
      const delay = Math.max(0, frameMs - (now() - lastFrameAt))
      frameTimer = schedule(() => {
        frameTimer = null
        if (disposed) return
        lastFrameAt = now()
        render()
      }, delay)
    }
    settleTimer = schedule(() => {
      settleTimer = null
      cancelFrame()
      if (!disposed) settle()
    }, settleMs)
  }

  function dispose() {
    disposed = true
    cancelFrame()
    cancelSettle()
  }

  return {
    queue,
    cancel() {
      cancelFrame()
      cancelSettle()
    },
    dispose,
  }
}
