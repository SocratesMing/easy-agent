<template>
  <!-- 用 element-ui 的 el-dialog 承载，保留 show() 的 Promise 用法，调用方零改动 -->
  <el-dialog
    :visible.sync="visible"
    :title="title"
    width="380px"
    :close-on-click-modal="true"
    custom-class="ea-confirm-dialog"
    @close="handleCancel"
  >
    <div class="confirm-body">
      <div class="confirm-icon" :class="type">
        <svg v-if="type === 'warning'" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="24" height="24">
          <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path>
          <line x1="12" y1="9" x2="12" y2="13"></line>
          <line x1="12" y1="17" x2="12.01" y2="17"></line>
        </svg>
        <svg v-else xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="24" height="24">
          <circle cx="12" cy="12" r="10"></circle>
          <line x1="12" y1="8" x2="12" y2="12"></line>
          <line x1="12" y1="16" x2="12.01" y2="16"></line>
        </svg>
      </div>
      <p class="confirm-message">{{ message }}</p>
    </div>

    <span slot="footer" class="confirm-actions">
      <el-button size="small" @click="handleCancel">{{ cancelText }}</el-button>
      <el-button
        size="small"
        :type="type === 'danger' ? 'danger' : 'warning'"
        @click="handleConfirm"
      >{{ confirmText }}</el-button>
    </span>
  </el-dialog>
</template>

<script>
export default {
  name: 'ConfirmDialog',
  props: {
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
.confirm-body {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.confirm-icon {
  flex-shrink: 0;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.confirm-icon.warning {
  background: #fef3cd;
  color: #d4a700;
}

.confirm-icon.danger {
  background: #fee2e2;
  color: #ef4444;
}

.confirm-message {
  margin: 0;
  font-size: 14px;
  color: var(--text-secondary);
  line-height: 1.6;
}

.confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
