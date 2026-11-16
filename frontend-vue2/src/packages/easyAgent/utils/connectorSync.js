/**
 * 连接器启用状态的跨组件同步。
 *
 * 「+」菜单的连接器面板（ChatInput.vue）与「专家·技能·连接器」面板
 * （ConnectorListPanel.vue）操作的是同一份状态（用户 mcp.json 里每个 server 的
 * ``enabled``），两处开关必须实时一致，否则会出现「这边显示启用、那边显示停用」。
 *
 * 两处面板不在同一组件树分支上（一个在输入框浮层，一个在 SkillCenter 面板里），
 * 用 window 自定义事件做广播：改的一方派发事件，另一方收到后刷新。
 *
 * payload: { id, enabled, source } —— source 用于回声消除，派发方忽略自己发出的事件。
 */

const CONNECTORS_CHANGED_EVENT = 'easy-agent:connectors-changed'

/** 广播某个连接器的启用状态变化 */
export function emitConnectorsChanged(id, enabled, source) {
  if (!id) return
  try {
    window.dispatchEvent(
      new CustomEvent(CONNECTORS_CHANGED_EVENT, { detail: { id, enabled: !!enabled, source } })
    )
  } catch (e) {
    // CustomEvent 不可用（极老的运行时）时静默降级：两处面板各自打开时仍会重新拉取
  }
}

/** 订阅连接器启用状态变化，返回取消订阅函数 */
export function onConnectorsChanged(handler) {
  const listener = (event) => handler((event && event.detail) || {})
  window.addEventListener(CONNECTORS_CHANGED_EVENT, listener)
  return () => window.removeEventListener(CONNECTORS_CHANGED_EVENT, listener)
}
