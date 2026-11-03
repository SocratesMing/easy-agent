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

<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'

// 消息内的报错提示：显示 5 秒后自动消失（与页面 error-toast 一致）
const TRANSIENT_ERROR_TTL_MS = 5000

const props = defineProps({
  error: { type: String, default: '' },
})

const visible = ref(false)
let timer = null

watch(
  () => props.error,
  (value) => {
    clearTimeout(timer)
    timer = null
    visible.value = Boolean(value)
    if (!visible.value) return
    timer = setTimeout(() => {
      timer = null
      visible.value = false
    }, TRANSIENT_ERROR_TTL_MS)
  },
  { immediate: true }
)

onBeforeUnmount(() => clearTimeout(timer))
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
