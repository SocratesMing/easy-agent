<template>
  <div class="profile-overlay" @click="$emit('close')">
    <div class="profile-modal" @click.stop>
      <div class="profile-header">
        <h2>个人资料</h2>
        <button @click="$emit('close')" class="close-btn">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="18" y1="6" x2="6" y2="18"></line>
            <line x1="6" y1="6" x2="18" y2="18"></line>
          </svg>
        </button>
      </div>

      <div class="profile-content">
        <div v-if="loading" class="loading-state">
          <div class="spinner"></div>
          <span>加载中...</span>
        </div>

        <div v-else class="profile-info">
          <div class="avatar-section">
            <div class="avatar">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
                <circle cx="12" cy="7" r="4"></circle>
              </svg>
            </div>
            <div class="user-display-name">{{ profile.username || '-' }}</div>
          </div>

          <div class="info-list">
            <div class="info-item">
              <div class="info-label">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path>
                  <polyline points="9 22 9 12 15 12 15 22"></polyline>
                </svg>
                机构ID
              </div>
              <div class="info-value">{{ profile.organization_id || '-' }}</div>
            </div>

            <div class="info-item">
              <div class="info-label">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"></path>
                  <polyline points="22,6 12,13 2,6"></polyline>
                </svg>
                用户邮箱
              </div>
              <div class="info-value">{{ profile.email || '-' }}</div>
            </div>
          </div>

          <div v-if="error" class="error-message">
            {{ error }}
          </div>

          <div class="form-actions">
            <button class="logout-btn" @click="handleLogout">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path>
                <polyline points="16 17 21 12 16 7"></polyline>
                <line x1="21" y1="12" x2="9" y2="12"></line>
              </svg>
              退出登录
            </button>
            <button class="unregister-btn" @click="showUnregisterDialog">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="3 6 5 6 21 6"></polyline>
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
              </svg>
              注销账号
            </button>
          </div>
        </div>
      </div>

      <ConfirmDialog
        ref="unregisterDialog"
        title="注销账号"
        :message="'注销后您的所有数据将被删除，包括上传的文件和会话记录。此操作不可恢复，确定要注销吗？'"
        confirm-text="注销"
        cancel-text="取消"
        type="danger"
        @confirm="handleUnregister"
      />
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { getUserProfile } from '../api/files.js'
import { unregister } from '../api/auth.js'
import ConfirmDialog from './ConfirmDialog.vue'

const emit = defineEmits(['close', 'logout', 'switch-user', 'unregister'])

const loading = ref(true)
const error = ref('')
const unregisterDialog = ref(null)

const profile = ref({
  username: '',
  organization_id: '',
  email: ''
})

async function loadProfile() {
  loading.value = true
  error.value = ''
  try {
    const data = await getUserProfile()
    profile.value = {
      username: data.username || '',
      organization_id: data.organization_id || '',
      email: data.email || ''
    }
  } catch (e) {
    error.value = e.message || '加载用户资料失败'
  } finally {
    loading.value = false
  }
}

function handleLogout() {
  emit('logout')
}

function handleSwitchUser() {
  emit('switch-user')
}

async function showUnregisterDialog() {
  const confirmed = await unregisterDialog.value.show()
  if (confirmed) {
    await handleUnregister()
  }
}

async function handleUnregister() {
  try {
    await unregister()
    emit('unregister')
    emit('close')
  } catch (e) {
    error.value = e.message || '注销失败'
  }
}

onMounted(() => {
  loadProfile()
})
</script>

<style scoped>
.profile-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.profile-modal {
  background: var(--bg-secondary);
  border-radius: 16px;
  width: 420px;
  max-width: 90vw;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
  overflow: hidden;
}

.profile-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 20px 24px;
  border-bottom: 1px solid var(--border-color);
}

.profile-header h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
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
  transition: all 0.2s;
}

.close-btn:hover {
  background: var(--bg-tertiary);
}

.close-btn svg {
  width: 20px;
  height: 20px;
  color: var(--text-secondary);
}

.profile-content {
  padding: 24px;
}

.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 20px;
  color: #94a3b8;
  gap: 12px;
}

.spinner {
  width: 32px;
  height: 32px;
  border: 3px solid #e2e8f0;
  border-top-color: #0ea5e9;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.profile-info {
  display: flex;
  flex-direction: column;
}

.avatar-section {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding-bottom: 24px;
  border-bottom: 1px solid var(--border-color);
  margin-bottom: 24px;
}

.avatar {
  width: 80px;
  height: 80px;
  background: linear-gradient(135deg, #0ea5e9 0%, #8b5cf6 100%);
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 12px;
}

.avatar svg {
  width: 40px;
  height: 40px;
  color: white;
}

.user-display-name {
  font-size: 20px;
  font-weight: 600;
  color: var(--text-primary);
}

.info-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.info-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  background: var(--bg-tertiary);
  border-radius: 10px;
}

.info-label {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 14px;
  font-weight: 500;
  color: var(--text-secondary);
}

.info-label svg {
  width: 18px;
  height: 18px;
}

.info-value {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
}

.error-message {
  padding: 12px 14px;
  background: #fee2e2;
  color: #dc2626;
  border-radius: 8px;
  font-size: 14px;
  margin-top: 16px;
}

.form-actions {
  display: flex;
  gap: 12px;
  margin-top: 24px;
}

.logout-btn,
.unregister-btn {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 12px 16px;
  border-radius: 10px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
}

.logout-btn {
  border: 1px solid #fee2e2;
  background: var(--bg-secondary);
  color: #dc2626;
}

.logout-btn:hover {
  background: #fee2e2;
  border-color: #fecaca;
}

.logout-btn svg {
  width: 18px;
  height: 18px;
}

.unregister-btn {
  border: 1px solid var(--border-color);
  background: var(--bg-secondary);
  color: #dc2626;
}

.unregister-btn:hover {
  background: #fee2e2;
  border-color: #fecaca;
}

.unregister-btn svg {
  width: 18px;
  height: 18px;
}

.switch-btn svg {
  width: 18px;
  height: 18px;
}

.close-action-btn {
  width: 100%;
  padding: 12px 48px;
  border: 1px solid var(--border-color);
  background: var(--bg-secondary);
  border-radius: 10px;
  font-size: 14px;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.2s;
  margin-top: 12px;
}

.close-action-btn:hover {
  background: var(--bg-tertiary);
  border-color: var(--border-color);
}
</style>
