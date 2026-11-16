/**
 * 遮罩点击关闭守卫（Vue2 版，行为同 frontend/src/utils/overlayClick.js）。
 *
 * 问题：在弹层的输入框/正文里从右向左（或跨容器）拖拽选择文本时，click 事件
 * 派发到 mousedown 与 mouseup 的共同祖先（遮罩），@click.self 命中导致弹层
 * 意外关闭。
 *
 * 判据：正常「点遮罩关闭」时 mousedown 也在遮罩上；拖选时 mousedown 在内容里。
 * mousedown 时用 markOverlayPress 记录，click 时 overlayClickCloses 校验。
 * 注意：<input> 里的文本选区不会反映到 window.getSelection()，纯选区判断不够。
 *
 * 用法（遮罩元素上）：
 *   @mousedown="overlayPress = markOverlayPress($event)"
 *   @click.self="overlayClickCloses($event, overlayPress, () => close())"
 */

export function markOverlayPress(event) {
  return event.target === event.currentTarget
    ? { x: event.clientX, y: event.clientY }
    : null
}

export function overlayClickCloses(event, press, close) {
  // mousedown 不在遮罩上：这次 click 是从内容里拖出来的（拖选结束），不关闭
  if (!press) return
  if (press && typeof event.clientX === 'number') {
    const dx = event.clientX - press.x
    const dy = event.clientY - press.y
    if (dx * dx + dy * dy > 25) return
  }
  const sel = window.getSelection()
  if (sel && String(sel).length > 0) return
  close()
}
