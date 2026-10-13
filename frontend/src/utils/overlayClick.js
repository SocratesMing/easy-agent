/**
 * 遮罩点击关闭守卫。
 *
 * 问题：在弹层的输入框/正文里从右向左（或跨容器）拖拽选择文本时，鼠标释放点
 * 常落在遮罩上，click 事件会派发到 mousedown 与 mouseup 的共同祖先（即遮罩），
 * @click.self 判断命中，导致弹层意外关闭。
 *
 * 判据：正常「点遮罩关闭」时 mousedown 与 click 都落在遮罩上；拖选时
 * mousedown 发生在内容里（输入框/正文）。因此：
 *   1. mousedown 时用 markOverlayPress 记录按下点是否在遮罩自身上；
 *   2. click 时 overlayClickCloses 校验该标记——mousedown 不在遮罩上（拖选起点
 *      在内容里）或有文本选区时，忽略本次点击、不关闭。
 *
 * 注意：<input> 里的文本选区不会反映到 window.getSelection()（对 input 做
 * selectNodeContents 得到空 range），所以纯选区判断不够，必须配 mousedown 判据。
 *
 * 用法（遮罩元素上）：
 *   @mousedown="overlayPress = markOverlayPress($event)"
 *   @click.self="overlayClickCloses($event, overlayPress, () => (visible = false))"
 */

export function markOverlayPress(event) {
  return event.target === event.currentTarget
    ? { x: event.clientX, y: event.clientY }
    : null
}

export function overlayClickCloses(event, press, close) {
  // mousedown 不在遮罩上：这次 click 是从内容里拖出来的（拖选结束），不关闭
  if (!press) return
  // 按下点与释放点距离过大也是拖拽（拖动遮罩外滚动等边缘情况）
  if (press && typeof event.clientX === 'number') {
    const dx = event.clientX - press.x
    const dy = event.clientY - press.y
    if (dx * dx + dy * dy > 25) return
  }
  const sel = window.getSelection()
  if (sel && String(sel).length > 0) return
  close()
}
