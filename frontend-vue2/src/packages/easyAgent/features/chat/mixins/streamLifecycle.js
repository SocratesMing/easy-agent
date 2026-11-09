import { createAutoDismiss } from '../../../utils/autoDismiss.js'

// 后端 / 系统报错提示（主页面 error-toast、侧边栏 ais-error）显示 5 秒后自动消失
const TRANSIENT_ERROR_TTL_MS = 5000

/**
 * 流式连接的生命周期（由 chatSession mixin 混入，主页面与 AI 侧边栏共用）。
 *
 * 后台流策略：
 * - 组件卸载（离开路由、销毁侧边栏）：只断开本实例的前端连接，不调用 /chat/cancel。后端任务
 *   与客户端断开解耦，会继续生成并落库；再次进入页面时按 sessionStorage 里的流式状态重新
 *   挂载（tryAttachLiveStream）。断开后各流的收尾逻辑看到 _chatDisposed 即跳过，不再改共享的
 *   chatStore —— 否则会把仍在后台运行的会话误标为已结束。
 * - keep-alive 缓存（deactivated）：实例仍存活，连接照常接收，界面隐藏，重新激活后直接继续。
 * - 用户点「停止」：与原行为一致，断开连接并通知后端取消（handleStop）。
 */
export default {
  created() {
    // 本实例发起的流式连接（发送 / 审批恢复 / 挂载）；非响应式
    this._streamControllers = new Set()
    this._chatDisposed = false
    // 后端 / 系统报错提示 5 秒后自动消失（非响应式定时器；主页面 / 侧边栏共用）
    this._dismissError = createAutoDismiss({
      read: () => this.error,
      clear: () => { this.error = null },
      ttlMs: TRANSIENT_ERROR_TTL_MS,
    })
  },
  watch: {
    // 期间出现新提示会取消旧定时器并重新计时
    error(value) {
      this._dismissError(value)
    },
  },
  beforeDestroy() {
    this._dismissError(null)
    this.disposeLocalStreams()
  },
  methods: {
    trackStream(controller) {
      this._streamControllers.add(controller)
      return controller
    },
    untrackStream(controller) {
      if (controller) this._streamControllers.delete(controller)
    },
    disposeLocalStreams() {
      this._chatDisposed = true
      for (const controller of this._streamControllers) {
        try { controller.abort() } catch (_) { /* ignore */ }
      }
      this._streamControllers.clear()
    },
  },
}
