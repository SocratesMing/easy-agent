export default {
  computed: {
    // 后台未返回耗时数据（sessionDuration<=0）且「会话信息」弹窗打开时，前端按 1s
    // 间隔本地计时，让会话耗时实时更新；后台有数据时直接用后台值。
    shouldCountLive() {
      return this.showTokenPopup && (!this.sessionDuration || this.sessionDuration <= 0)
    },
    displayDuration() {
      return this.shouldCountLive ? this.liveDuration : (this.sessionDuration || 0)
    },
    formattedDuration() {
      const total = Math.floor(this.displayDuration)
      const h = Math.floor(total / 3600)
      const m = Math.floor((total % 3600) / 60)
      const s = total % 60
      if (h > 0) {
        return `${h}小时${m}分${s}秒`
      }
      if (m > 0) {
        return `${m}分${s}秒`
      }
      return `${s}秒`
    },
    // 首页（尚未进入任何会话）不展示会话级用量：登录/注册后会从全局配置把
    // context_length 写进 sessionUsage，但此时没有任何实际用量，
    // 不能凭这个就显示上下文占用环。
    showTokenRing() {
      if (!this.sessionId) return false
      return this.sessionUsage.input_tokens > 0 || this.sessionUsage.output_tokens > 0 || this.sessionUsage.reasoning_tokens > 0 || this.sessionUsage.context_tokens > 0 || this.sessionDuration > 0 || this.iterationCount > 0
    },
    contextPercent() {
      const u = this.sessionUsage
      if (!u.context_length || u.context_length <= 0) return 0
      // 分子使用「当前轮次的上下文窗口占用」(context_tokens)：即本轮喂给模型的输入 token 数，
      // 与 context_length（上下文窗口上限）对比（已不再统计会话累计总量）。
      const ctxTokens = u.context_tokens || 0
      return Math.min(100, Math.round(ctxTokens / u.context_length * 100))
    },
    contextColor() {
      const p = this.contextPercent
      if (p >= 80) return '#ef4444'
      if (p >= 50) return '#f59e0b'
      return '#22c55e'
    },
    popupStyle() {
      const ring = this.$refs.ringRef
      if (!ring) return {}
      const rect = ring.getBoundingClientRect()
      return {
        position: 'fixed',
        bottom: `${window.innerHeight - rect.top + 10}px`,
        right: `${window.innerWidth - rect.right - 8}px`,
      }
    },
  },
}
