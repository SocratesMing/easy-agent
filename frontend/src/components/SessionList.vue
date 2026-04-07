<template>
  <div class="session-list">
    <div class="session-header">
      <div class="header-left">
        <div class="logo">
          <WuKongLogo :size="28" />
        </div>
        <span class="logo-text">WuKong</span>
      </div>
      <button @click="$emit('toggleSidebar')" class="collapse-btn" title="收起侧边栏">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
          <line x1="15" y1="3" x2="15" y2="21"></line>
        </svg>
      </button>
    </div>

    <div class="action-buttons">
      <button @click="$emit('createSession')" class="action-btn new-chat">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <line x1="12" y1="5" x2="12" y2="19"></line>
          <line x1="5" y1="12" x2="19" y2="12"></line>
        </svg>
        <span>新建会话</span>
      </button>
      
      <button @click="$emit('showAssets')" class="action-btn assets">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path>
        </svg>
        <span>资产</span>
      </button>
    </div>

    <div class="divider"></div>

    <div class="session-items">
      <div class="sessions-header">
        <span>会话列表</span>
      </div>
      
      <div
        v-for="session in sessions"
        :key="session.session_id"
        class="session-item"
        :class="{ active: !showAssets && session.session_id === currentSessionId }"
        @click="$emit('selectSession', session.session_id)"
      >
        <div class="session-info">
          <div class="session-name">{{ session.title || '未命名会话' }}</div>
        </div>
        <div class="session-actions">
          <button @click="toggleMenu(session.session_id, $event)" class="menu-btn">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor">
              <circle cx="12" cy="5" r="2"></circle>
              <circle cx="12" cy="12" r="2"></circle>
              <circle cx="12" cy="19" r="2"></circle>
            </svg>
          </button>
          <div v-if="activeMenu === session.session_id" class="menu-dropdown">
            <button @click.stop="startRename(session)" class="menu-item">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path>
                <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path>
              </svg>
              重命名
            </button>
            <button @click.stop="handleDelete(session.session_id)" class="menu-item delete">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="3 6 5 6 21 6"></polyline>
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
              </svg>
              删除
            </button>
          </div>
        </div>
      </div>
      
      <div v-if="sessions.length === 0" class="empty-state">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
        </svg>
        <p>暂无会话</p>
      </div>
    </div>

    <div v-if="showRenameModal" class="modal-overlay" @click="cancelRename">
      <div class="modal-content" @click.stop>
        <h3>重命名会话</h3>
        <input
          v-model="newTitle"
          @keyup.enter="confirmRename"
          @keyup.escape="cancelRename"
          placeholder="请输入新名称"
          ref="renameInput"
        />
        <div class="modal-actions">
          <button @click="cancelRename" class="cancel-btn">取消</button>
          <button @click="confirmRename" class="confirm-btn">确认</button>
        </div>
      </div>
    </div>

    <div class="user-profile" @click="toggleUserMenu">
      <div class="user-avatar">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
          <circle cx="12" cy="7" r="4"></circle>
        </svg>
      </div>
      <div class="user-info">
        <div class="user-name">{{ username || '用户' }}</div>
        <div class="user-status">在线</div>
      </div>
      <svg class="user-arrow" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <polyline points="6 9 12 15 18 9"></polyline>
      </svg>
      
      <div v-if="showUserMenu" class="user-dropdown">
        <div class="user-dropdown-header">
          <div class="user-dropdown-avatar">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
              <circle cx="12" cy="7" r="4"></circle>
            </svg>
          </div>
          <div class="user-dropdown-info">
            <div class="user-dropdown-name">{{ username || '用户' }}</div>
            <div class="user-dropdown-email">{{ email || '未设置邮箱' }}</div>
          </div>
        </div>
        <div class="user-dropdown-divider"></div>
        <button class="user-dropdown-item" @click="showProfile">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
            <circle cx="12" cy="7" r="4"></circle>
          </svg>
          个人资料
        </button>
        <button class="user-dropdown-item logout-item" @click="handleLogout">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path>
            <polyline points="16 17 21 12 16 7"></polyline>
            <line x1="21" y1="12" x2="9" y2="12"></line>
          </svg>
          退出登录
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, nextTick, onMounted, onUnmounted } from 'vue'
import WuKongLogo from './WuKongLogo.vue'

const emit = defineEmits(['createSession', 'selectSession', 'deleteSession', 'renameSession', 'toggleSidebar', 'showAssets', 'showProfile', 'logout'])

const props = defineProps({
  sessions: {
    type: Array,
    default: () => []
  },
  currentSessionId: {
    type: String,
    default: null
  },
  username: {
    type: String,
    default: ''
  },
  email: {
    type: String,
    default: ''
  },
  showAssets: {
    type: Boolean,
    default: false
  }
})

const activeMenu = ref(null)
const showRenameModal = ref(false)
const newTitle = ref('')
const renamingSession = ref(null)
const renameInput = ref(null)
const showUserMenu = ref(false)

function toggleUserMenu(e) {
  e.stopPropagation()
  showUserMenu.value = !showUserMenu.value
}

function toggleMenu(sessionId, e) {
  if (e) {
    e.stopPropagation()
  }
  activeMenu.value = activeMenu.value === sessionId ? null : sessionId
}

function startRename(session) {
  renamingSession.value = session
  newTitle.value = session.title || ''
  activeMenu.value = null
  showRenameModal.value = true
  nextTick(() => {
    renameInput.value?.focus()
    renameInput.value?.select()
  })
}

function cancelRename() {
  showRenameModal.value = false
  renamingSession.value = null
  newTitle.value = ''
}

function confirmRename() {
  if (newTitle.value.trim() && renamingSession.value) {
    emit('renameSession', renamingSession.value.session_id, newTitle.value.trim())
    cancelRename()
  }
}

function handleDelete(sessionId) {
  activeMenu.value = null
  emit('deleteSession', sessionId)
}

function closeMenu() {
  activeMenu.value = null
}

onMounted(() => {
  document.addEventListener('click', () => {
    closeMenu()
    closeUserMenuSilent()
  })
})

function closeUserMenuSilent() {
  showUserMenu.value = false
}

function showProfile() {
  showUserMenu.value = false
  emit('showProfile')
}

function handleLogout() {
  showUserMenu.value = false
  emit('logout')
}
</script>

<style scoped>
.session-list {
  width: 280px;
  background: #ffffff;
  border-right: 1px solid #e2e8f0;
  display: flex;
  flex-direction: column;
  height: 100%;
  position: relative;
}

.session-header {
  padding: 12px 16px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 1px solid #f1f5f9;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}

.logo {
  display: flex;
  align-items: center;
  justify-content: center;
}

.logo-text {
  font-size: 16px;
  font-weight: 600;
  color: #1e293b;
}

.collapse-btn {
  width: 28px;
  height: 28px;
  border: none;
  background: transparent;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.2s;
}

.collapse-btn:hover {
  background: #f1f5f9;
}

.collapse-btn svg {
  width: 18px;
  height: 18px;
  color: #64748b;
}

.action-buttons {
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.action-btn {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border: 1px solid #e2e8f0;
  background: white;
  border-radius: 10px;
  font-size: 14px;
  color: #475569;
  cursor: pointer;
  transition: all 0.2s;
}

.action-btn:hover {
  background: #f8fafc;
  border-color: #cbd5e1;
}

.action-btn.active {
  background: #e0f2fe;
  border-color: #0ea5e9;
  color: #0ea5e9;
}

.action-btn svg {
  width: 18px;
  height: 18px;
}

.action-btn.new-chat:hover {
  background: #0ea5e9;
  border-color: #0ea5e9;
  color: white;
}

.divider {
  height: 1px;
  background: #e2e8f0;
  margin: 0 16px;
}

.session-items {
  flex: 1;
  overflow-y: auto;
  padding: 12px 8px;
  padding-bottom: 60px;
}

.sessions-header {
  padding: 0 8px 8px;
  font-size: 12px;
  font-weight: 500;
  color: #94a3b8;
}

.session-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 10px;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s;
  margin-bottom: 2px;
}

.session-item:hover {
  background: #f1f5f9;
}

.session-item.active {
  background: #e0f2fe;
}

.session-info {
  flex: 1;
  min-width: 0;
}

.session-name {
  font-size: 13px;
  font-weight: 500;
  color: #1e293b;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.session-actions {
  position: relative;
}

.menu-btn {
  width: 28px;
  height: 28px;
  border: none;
  background: transparent;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  opacity: 0;
  transition: all 0.2s;
}

.menu-btn svg {
  width: 18px;
  height: 18px;
  color: #64748b;
}

.session-item:hover .menu-btn {
  opacity: 1;
}

.menu-btn:hover {
  background: #e2e8f0;
}

.menu-dropdown {
  position: absolute;
  right: 0;
  top: 100%;
  background: white;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
  z-index: 100;
  min-width: 120px;
  overflow: hidden;
}

.menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 10px 14px;
  border: none;
  background: transparent;
  font-size: 13px;
  color: #475569;
  cursor: pointer;
  transition: all 0.2s;
}

.menu-item svg {
  width: 16px;
  height: 16px;
}

.menu-item:hover {
  background: #f1f5f9;
}

.menu-item.delete {
  color: #ef4444;
}

.menu-item.delete:hover {
  background: #fee2e2;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 20px;
  color: #94a3b8;
}

.empty-state svg {
  width: 48px;
  height: 48px;
  margin-bottom: 12px;
  opacity: 0.5;
}

.empty-state p {
  font-size: 14px;
  margin: 0;
}

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
  z-index: 1000;
}

.modal-content {
  background: white;
  padding: 24px;
  border-radius: 12px;
  width: 320px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
}

.modal-content h3 {
  margin: 0 0 16px 0;
  font-size: 16px;
  font-weight: 600;
  color: #1e293b;
}

.modal-content input {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  font-size: 14px;
  outline: none;
  transition: all 0.2s;
  box-sizing: border-box;
}

.modal-content input:focus {
  border-color: #0ea5e9;
  box-shadow: 0 0 0 3px rgba(14, 165, 233, 0.1);
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
}

.cancel-btn {
  padding: 8px 16px;
  border: 1px solid #e2e8f0;
  background: white;
  border-radius: 8px;
  font-size: 14px;
  color: #64748b;
  cursor: pointer;
  transition: all 0.2s;
}

.cancel-btn:hover {
  background: #f1f5f9;
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

.user-profile {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  background: #f8fafc;
  border-top: 1px solid #e2e8f0;
  cursor: pointer;
  transition: background 0.2s;
  position: relative;
}

.user-profile:hover {
  background: #f1f5f9;
}

.user-avatar {
  width: 36px;
  height: 36px;
  background: linear-gradient(135deg, #0ea5e9 0%, #8b5cf6 100%);
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.user-avatar svg {
  width: 20px;
  height: 20px;
  color: white;
}

.user-info {
  flex: 1;
  min-width: 0;
}

.user-name {
  font-size: 14px;
  font-weight: 500;
  color: #1e293b;
}

.user-status {
  font-size: 12px;
  color: #22c55e;
}

.user-arrow {
  width: 16px;
  height: 16px;
  color: #64748b;
}

.user-dropdown {
  position: absolute;
  bottom: 100%;
  left: 0;
  right: 0;
  background: white;
  border: 1px solid #e2e8f0;
  border-radius: 12px 12px 0 0;
  box-shadow: 0 -4px 12px rgba(0, 0, 0, 0.1);
  z-index: 100;
  margin-bottom: 8px;
}

.user-dropdown-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px;
}

.user-dropdown-avatar {
  width: 48px;
  height: 48px;
  background: linear-gradient(135deg, #0ea5e9 0%, #8b5cf6 100%);
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.user-dropdown-avatar svg {
  width: 24px;
  height: 24px;
  color: white;
}

.user-dropdown-info {
  flex: 1;
  min-width: 0;
}

.user-dropdown-name {
  font-size: 16px;
  font-weight: 600;
  color: #1e293b;
}

.user-dropdown-email {
  font-size: 13px;
  color: #64748b;
}

.user-dropdown-divider {
  height: 1px;
  background: #e2e8f0;
  margin: 0 8px;
}

.user-dropdown-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 12px 16px;
  border: none;
  background: transparent;
  font-size: 14px;
  color: #475569;
  cursor: pointer;
  transition: all 0.2s;
}

.user-dropdown-item svg {
  width: 18px;
  height: 18px;
}

.user-dropdown-item:hover {
  background: #f1f5f9;
}

.user-dropdown-item.logout-item {
  color: #ef4444;
}

.user-dropdown-item.logout-item:hover {
  background: #fee2e2;
}
</style>
