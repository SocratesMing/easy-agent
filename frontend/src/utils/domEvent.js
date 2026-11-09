/**
 * 事件的真实目标节点。
 *
 * 组件挂在 Shadow DOM 里（AI 侧边栏 SDK 嵌入第三方页面）时，document 上监听到的
 * event.target 会被重定向为影子宿主元素，`el.contains(event.target)` 永远为 false；
 * composedPath()[0] 才是实际点击的节点。普通 DOM 下两者一致。
 */
export function eventTarget(event) {
  const path = event && event.composedPath ? event.composedPath() : null
  return (path && path[0]) || (event && event.target) || null
}
