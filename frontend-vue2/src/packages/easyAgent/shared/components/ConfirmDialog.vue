<template>
  <div
    v-if="visible"
    class="modal-overlay"
    role="dialog"
    aria-modal="true"
    :aria-label="title"
    @click="handleCancel"
    @keydown.escape.stop="handleCancel"
  >
    <div class="modal-content" @click.stop>
      <h3>{{ title }}</h3>
      <p class="confirm-text">{{ message }}</p>
      <div class="modal-actions">
        <button type="button" class="cancel-btn" @click="handleCancel">{{ cancelText }}</button>
        <button
          type="button"
          class="confirm-btn"
          :class="{ danger: type === 'danger' }"
          @click="handleConfirm"
        >{{ confirmText }}</button>
      </div>
    </div>
  </div>
</template>

<script>
export default {
  name: 'ConfirmDialog',
  props: {
    modalAppendToBody: { type: Boolean, default: true },
    title: {
      type: String,
      default: '确认',
    },
    message: {
      type: String,
      default: '确定要执行此操作吗？',
    },
    confirmText: {
      type: String,
      default: '确定',
    },
    cancelText: {
      type: String,
      default: '取消',
    },
    type: {
      type: String,
      default: 'warning',
    },
  },
  data() {
    return {
      visible: false,
    }
  },
  beforeDestroy() {
    if (this._resolvePromise) {
      this._resolvePromise(false)
      this._resolvePromise = null
    }
  },
  methods: {
    show() {
      this.visible = true
      return new Promise((resolve) => {
        this._resolvePromise = resolve
      })
    },
    handleConfirm() {
      this.visible = false
      if (this._resolvePromise) {
        this._resolvePromise(true)
        this._resolvePromise = null
      }
      this.$emit('confirm')
    },
    handleCancel() {
      this.visible = false
      if (this._resolvePromise) {
        this._resolvePromise(false)
        this._resolvePromise = null
      }
      this.$emit('cancel')
    },
  },
}
</script>

<style scoped>
.modal-overlay {
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

.modal-content {
  background: var(--bg-secondary, #ffffff);
  padding: 24px;
  border-radius: 12px;
  width: 320px;
  max-width: 90vw;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
}

.modal-content h3 {
  margin: 0 0 16px 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary, #1e293b);
}

.confirm-text {
  margin: 0 0 4px 0;
  font-size: 14px;
  line-height: 1.6;
  color: var(--text-secondary, #64748b);
  word-break: break-word;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
}

.cancel-btn {
  padding: 8px 16px;
  border: 1px solid var(--border-color, #e2e8f0);
  background: var(--bg-secondary, #ffffff);
  border-radius: 8px;
  font-size: 14px;
  color: var(--text-secondary, #64748b);
  cursor: pointer;
  transition: all 0.2s;
}

.cancel-btn:hover {
  background: var(--bg-tertiary, #f1f5f9);
}

.confirm-btn {
  padding: 8px 16px;
  border: none;
  background: #0ea5e9;
  color: white;
  border-radius: 8px;
  font-size: 14px;
  cursor: pointer;
  transition: all 0.2s;
}

.confirm-btn:hover {
  background: #0284c7;
}

.confirm-btn.danger {
  background: #ef4444;
}

.confirm-btn.danger:hover {
  background: #dc2626;
}
</style>
