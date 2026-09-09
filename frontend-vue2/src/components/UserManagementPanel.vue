<template>
  <div class="user-management-overlay" @click="$emit('close')">
    <div class="user-management-modal" @click.stop>
      <div class="panel-header">
        <div>
          <h2>用户管理</h2>
          <p>当前共 {{ total }} 个用户，重置后的默认密码为 123456</p>
        </div>
        <button class="close-btn" @click="$emit('close')" title="关闭">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="18" y1="6" x2="6" y2="18"></line>
            <line x1="6" y1="6" x2="18" y2="18"></line>
          </svg>
        </button>
      </div>

      <div class="panel-content">
        <div v-if="loading" class="loading-state"><div class="spinner"></div></div>

        <div v-else-if="error" class="state-message error">
          {{ error }}
          <button class="text-btn" @click="loadUsers">重试</button>
        </div>

        <template v-else>
          <div v-if="message" class="state-message success">{{ message }}</div>
          <div v-if="users.length === 0" class="state-message empty">暂无用户</div>
          <div v-else class="user-table">
            <div class="table-head">
              <span>账号</span>
              <span>操作</span>
            </div>
            <div v-for="user in users" :key="user.username" class="table-row">
              <span class="username">{{ user.username }}</span>
              <button
                class="reset-btn"
                :disabled="resettingUsername === user.username"
                @click="resetPassword(user.username)"
              >
                {{ resettingUsername === user.username ? '重置中...' : '重置密码' }}
              </button>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, onMounted } from 'vue'
import { MessageBox } from 'element-ui'
import { listUsers, resetUserPassword } from '../api/auth.js'
export default {
  setup(props, { emit }) {
const users = ref([])
const total = ref(0)
const loading = ref(true)
const error = ref('')
const message = ref('')
const resettingUsername = ref('')

async function loadUsers() {
  loading.value = true
  error.value = ''
  message.value = ''
  try {
    const data = await listUsers()
    users.value = data.users || []
    total.value = data.total ?? users.value.length
  } catch (e) {
    error.value = e.message || '获取用户列表失败'
  } finally {
    loading.value = false
  }
}

async function resetPassword(username) {
  // 用 element-ui 的确认框替代原生 confirm
  try {
    await MessageBox.confirm(`确认将用户 ${username} 的密码重置为 123456？`, '重置密码确认', {
      type: 'warning',
      confirmButtonText: '重置',
      cancelButtonText: '取消',
    })
  } catch {
    return // 用户取消
  }

  resettingUsername.value = username
  error.value = ''
  message.value = ''
  try {
    await resetUserPassword(username)
    message.value = `用户 ${username} 的密码已重置为 123456`
  } catch (e) {
    error.value = e.message || '密码重置失败'
  } finally {
    resettingUsername.value = ''
  }
}

onMounted(loadUsers)

    return {
      error,
      listUsers,
      loading,
      loadUsers,
      message,
      onMounted,
      ref,
      resetPassword,
      resettingUsername,
      resetUserPassword,
      total,
      users,
    }
  },
}
</script>

<style scoped>
.user-management-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.user-management-modal {
  width: 480px;
  max-width: 92vw;
  max-height: 80vh;
  background: var(--bg-secondary);
  border-radius: 16px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  padding: 20px 24px;
  border-bottom: 1px solid var(--border-color);
}

.panel-header h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
}

.panel-header p {
  margin: 6px 0 0;
  font-size: 13px;
  color: var(--text-secondary);
}

.close-btn {
  width: 32px;
  height: 32px;
  border: none;
  background: transparent;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}

.close-btn:hover {
  background: var(--bg-tertiary);
}

.close-btn svg {
  width: 20px;
  height: 20px;
  color: var(--text-secondary);
}

.panel-content {
  padding: 20px 24px 24px;
  overflow-y: auto;
}

.loading-state {
  display: flex;
  justify-content: center;
  padding: 40px 0;
}

.spinner {
  width: 32px;
  height: 32px;
  border: 3px solid var(--border-color);
  border-top-color: #0ea5e9;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.state-message {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border-radius: 10px;
  padding: 10px 12px;
  margin-bottom: 14px;
  font-size: 13px;
}

.state-message.error {
  background: rgba(239, 68, 68, 0.1);
  color: #dc2626;
}

.state-message.success {
  background: rgba(16, 185, 129, 0.1);
  color: #059669;
}

.state-message.empty {
  background: var(--bg-tertiary);
  color: var(--text-secondary);
  justify-content: center;
}

.text-btn {
  border: none;
  background: transparent;
  color: #dc2626;
  cursor: pointer;
  font-size: 13px;
}

.user-table {
  border: 1px solid var(--border-color);
  border-radius: 12px;
  overflow: hidden;
}

.table-head,
.table-row {
  display: grid;
  grid-template-columns: 1fr 110px;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
}

.table-head {
  background: var(--bg-tertiary);
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 600;
}

.table-row {
  border-top: 1px solid var(--border-color);
  color: var(--text-primary);
  font-size: 14px;
}

.username {
  overflow-wrap: anywhere;
}

.reset-btn {
  border: 1px solid rgba(14, 165, 233, 0.45);
  background: rgba(14, 165, 233, 0.1);
  color: #0284c7;
  border-radius: 8px;
  padding: 7px 10px;
  font-size: 13px;
  cursor: pointer;
}

.reset-btn:hover:not(:disabled) {
  background: rgba(14, 165, 233, 0.18);
}

.reset-btn:disabled {
  opacity: 0.65;
  cursor: not-allowed;
}
</style>
