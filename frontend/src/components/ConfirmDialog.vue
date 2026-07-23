<template>
  <Teleport to="body">
    <div v-if="visible" class="confirm-dialog-overlay" @click.self="handleCancel">
      <div class="confirm-dialog">
        <div class="confirm-icon" :class="type">
          <svg v-if="type === 'warning'" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path>
            <line x1="12" y1="9" x2="12" y2="13"></line>
            <line x1="12" y1="17" x2="12.01" y2="17"></line>
          </svg>
          <svg v-else xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="10"></circle>
            <line x1="12" y1="8" x2="12" y2="12"></line>
            <line x1="12" y1="16" x2="12.01" y2="16"></line>
          </svg>
        </div>
        <h3 class="confirm-title">{{ title }}</h3>
        <p class="confirm-message">{{ message }}</p>
        <div class="confirm-actions">
          <button class="btn-cancel" @click="handleCancel">{{ cancelText }}</button>
          <button class="btn-confirm" :class="type" @click="handleConfirm">{{ confirmText }}</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { ref } from 'vue'

const props = defineProps({
  title: {
    type: String,
    default: '确认'
  },
  message: {
    type: String,
    default: '确定要执行此操作吗？'
  },
  confirmText: {
    type: String,
    default: '确定'
  },
  cancelText: {
    type: String,
    default: '取消'
  },
  type: {
    type: String,
    default: 'warning'
  }
})

const emit = defineEmits(['confirm', 'cancel'])

const visible = ref(false)
let resolvePromise = null

function show() {
  visible.value = true
  return new Promise((resolve) => {
    resolvePromise = resolve
  })
}

function handleConfirm() {
  visible.value = false
  if (resolvePromise) {
    resolvePromise(true)
    resolvePromise = null
  }
  emit('confirm')
}

function handleCancel() {
  visible.value = false
  if (resolvePromise) {
    resolvePromise(false)
    resolvePromise = null
  }
  emit('cancel')
}

defineExpose({ show })
</script>

<style scoped>
.confirm-dialog-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9999;
}

.confirm-dialog {
  background: white;
  border-radius: 12px;
  padding: 24px;
  width: 360px;
  max-width: 90vw;
  box-shadow: 0 20px 40px rgba(0, 0, 0, 0.2);
  animation: dialog-enter 0.2s ease-out;
}

@keyframes dialog-enter {
  from {
    opacity: 0;
    transform: scale(0.9);
  }
  to {
    opacity: 1;
    transform: scale(1);
  }
}

.confirm-icon {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 16px;
}

.confirm-icon.warning {
  background: #fef3cd;
  color: #d4a700;
}

.confirm-icon.warning svg {
  color: #d4a700;
}

.confirm-icon.danger {
  background: #fee2e2;
  color: #ef4444;
}

.confirm-icon.danger svg {
  color: #ef4444;
}

.confirm-title {
  margin: 0 0 8px;
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
  text-align: center;
}

.confirm-message {
  margin: 0 0 24px;
  font-size: 14px;
  color: var(--text-secondary);
  text-align: center;
  line-height: 1.5;
}

.confirm-actions {
  display: flex;
  gap: 12px;
}

.btn-cancel,
.btn-confirm {
  flex: 1;
  padding: 10px 16px;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
  border: none;
}

.btn-cancel {
  background: var(--bg-tertiary);
  color: var(--text-secondary);
}

.btn-cancel:hover {
  background: var(--bg-secondary);
}

.btn-confirm {
  color: white;
}

.btn-confirm.warning {
  background: #f59e0b;
}

.btn-confirm.warning:hover {
  background: #d97706;
}

.btn-confirm.danger {
  background: #ef4444;
}

.btn-confirm.danger:hover {
  background: #dc2626;
}
</style>
