import { ref, computed, watch, onUnmounted } from 'vue'

export function useContextDuration(props, showTokenPopup) {
  const liveDuration = ref(0)
  let durationTimer = null
  const shouldCountLive = computed(
    () => showTokenPopup.value && (!props.sessionDuration || props.sessionDuration <= 0)
  )
  const displayDuration = computed(() =>
    shouldCountLive.value ? liveDuration.value : (props.sessionDuration || 0)
  )
  function startLiveDuration() {
    stopLiveDuration()
    liveDuration.value = 0
    durationTimer = setInterval(() => {
      liveDuration.value += 1
    }, 1000)
  }
  function stopLiveDuration() {
    if (durationTimer) {
      clearInterval(durationTimer)
      durationTimer = null
    }
  }
  watch(shouldCountLive, (on) => {
    if (on) startLiveDuration()
    else stopLiveDuration()
  })

  const formattedDuration = computed(() => {
    const total = Math.floor(displayDuration.value)
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
  })

  onUnmounted(stopLiveDuration)
  return formattedDuration
}

export function useContextRing(props) {
  const showTokenRing = computed(() => {
    // 首页（尚未进入任何会话）不展示会话级用量：登录/注册后会从全局配置把
    // context_length 写进 sessionUsage，但此时没有任何实际用量，
    // 不能凭这个就显示上下文占用环。
    if (!props.sessionId) return false
    return props.sessionUsage.input_tokens > 0 || props.sessionUsage.output_tokens > 0 || props.sessionUsage.reasoning_tokens > 0 || props.sessionUsage.context_tokens > 0 || props.sessionDuration > 0 || props.iterationCount > 0
  })

  const contextPercent = computed(() => {
    const u = props.sessionUsage
    if (!u.context_length || u.context_length <= 0) return 0
    // 分子使用「当前轮次的上下文窗口占用」(context_tokens)：即本轮喂给模型的输入 token 数，
    // 与 context_length（上下文窗口上限）对比（已不再统计会话累计总量）。
    const ctxTokens = u.context_tokens || 0
    return Math.min(100, Math.round(ctxTokens / u.context_length * 100))
  })

  const contextColor = computed(() => {
    const p = contextPercent.value
    if (p >= 80) return '#ef4444'
    if (p >= 50) return '#f59e0b'
    return '#22c55e'
  })

  return { showTokenRing, contextPercent, contextColor }
}
