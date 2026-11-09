<template>
  <div v-if="visible" class="message-error">
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="error-icon">
      <circle cx="12" cy="12" r="10"></circle>
      <line x1="12" y1="8" x2="12" y2="12"></line>
      <line x1="12" y1="16" x2="12.01" y2="16"></line>
    </svg>
    <span>{{ error }}</span>
  </div>
</template>

<script>
// 消息内的报错提示：显示 5 秒后自动消失（与页面 error-toast 一致）
const TRANSIENT_ERROR_TTL_MS = 5000

export default {
  name: 'MessageError',
  props: {
    error: { type: String, default: '' },
  },
  data() {
    return { visible: false, timer: null }
  },
  watch: {
    error: {
      immediate: true,
      handler(value) {
        clearTimeout(this.timer)
        this.timer = null
        this.visible = Boolean(value)
        if (!this.visible) return
        this.timer = setTimeout(() => {
          this.timer = null
          this.visible = false
        }, TRANSIENT_ERROR_TTL_MS)
      },
    },
  },
  beforeDestroy() {
    clearTimeout(this.timer)
  },
}
</script>

<style scoped>
.message-error {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px 14px;
  margin: 4px 16px;
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: 10px;
  color: #dc2626;
  font-size: 14px;
  line-height: 1.5;
  word-break: break-word;
}

.message-error .error-icon {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
  margin-top: 1px;
}
</style>
