<template>
  <div class="session-list">
    <div class="session-header">
      <div class="header-left">
        <div class="logo">
          <EasyLogo :size="28" />
        </div>
        <span class="logo-text">Easy Agent</span>
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

      <button @click="$emit('showSkillCenter')" class="action-btn skill-center">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>
        </svg>
        <span>技能中心</span>
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
          <div class="session-name">
            <svg v-if="session.pinned" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="#f59e0b" stroke="#f59e0b" stroke-width="1" style="width:13px;height:13px;flex-shrink:0;margin-right:2px">
              <path d="M12 2L2 7l10 5 10-5-10-5z"></path>
              <path d="M2 17l10 5 10-5"></path>
              <path d="M2 12l10 5 10-5"></path>
            </svg>
            {{ session.title || '未命名会话' }}
          </div>
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
            <button @click.stop="handleTogglePin(session.session_id)" class="menu-item">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px">
                <path d="M12 2L2 7l10 5 10-5-10-5z"></path>
                <path d="M2 17l10 5 10-5"></path>
                <path d="M2 12l10 5 10-5"></path>
              </svg>
              {{ session.pinned ? '取消置顶' : '置顶' }}
            </button>
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
        <span class="user-initials">{{ userInitials }}</span>
      </div>
      <div class="user-info">
        <div class="user-name">{{ username || '用户' }}</div>
        <div v-if="organizationId" class="user-org">
          <svg class="user-org-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M3 21h18"></path>
            <path d="M5 21V7l8-4v18"></path>
            <path d="M19 21V11l-6-4"></path>
          </svg>
          <span>{{ organizationId }}</span>
        </div>
      </div>
      <svg class="user-more-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor">
        <circle cx="5" cy="12" r="2"></circle>
        <circle cx="12" cy="12" r="2"></circle>
        <circle cx="19" cy="12" r="2"></circle>
      </svg>
      
      <div v-if="showUserMenu" class="user-dropdown">
        <button class="user-dropdown-item" @click="showProfile">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
            <circle cx="12" cy="7" r="4"></circle>
          </svg>
          个人资料
        </button>
        <button class="user-dropdown-item" @click="showSettings">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="3"></circle>
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
          </svg>
          设置
        </button>
        <div class="user-dropdown-divider"></div>
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
import { ref, nextTick, onMounted, computed } from 'vue'
import EasyLogo from './EasyLogo.vue'

const emit = defineEmits(['createSession', 'selectSession', 'deleteSession', 'renameSession', 'toggleSidebar', 'showAssets', 'showSkillCenter', 'showProfile', 'showSettings', 'logout', 'togglePin'])

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
  organizationId: {
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

// 用户名简写：中文取每个字拼音首字母（简化为取前两字），英文取前两字母大写
const userInitials = computed(() => {
  const name = props.username || '用户'
  if (!name) return 'U'
  // 中文：取前两个字符
  const chineseChars = name.match(/[\u4e00-\u9fff]/g)
  if (chineseChars && chineseChars.length > 0) {
    // 简单取前两个中文字符（拼音首字母需引入拼音库，这里用字符本身的大写映射）
    // 常见姓氏首字母映射表（覆盖常见情况）
    const pinyinMap = {
      '张': 'Z', '王': 'W', '李': 'L', '刘': 'L', '陈': 'C', '杨': 'Y', '赵': 'Z', '黄': 'H',
      '周': 'Z', '吴': 'W', '徐': 'X', '孙': 'S', '胡': 'H', '朱': 'Z', '高': 'G', '林': 'L',
      '何': 'H', '郭': 'G', '马': 'M', '罗': 'L', '梁': 'L', '宋': 'S', '郑': 'Z', '谢': 'X',
      '韩': 'H', '唐': 'T', '冯': 'F', '于': 'Y', '董': 'D', '萧': 'X', '程': 'C', '曹': 'C',
      '袁': 'Y', '邓': 'D', '许': 'X', '傅': 'F', '沈': 'S', '曾': 'Z', '彭': 'P', '吕': 'L',
      '苏': 'S', '卢': 'L', '蒋': 'J', '蔡': 'C', '贾': 'J', '丁': 'D', '魏': 'W', '薛': 'X',
      '叶': 'Y', '阎': 'Y', '余': 'Y', '潘': 'P', '杜': 'D', '戴': 'D', '夏': 'X', '钟': 'Z',
      '汪': 'W', '田': 'T', '任': 'R', '姜': 'J', '范': 'F', '方': 'F', '石': 'S', '姚': 'Y',
      '谭': 'T', '廖': 'L', '邹': 'Z', '熊': 'X', '金': 'J', '陆': 'L', '郝': 'H', '孔': 'K',
      '白': 'B', '崔': 'C', '康': 'K', '毛': 'M', '邱': 'Q', '秦': 'Q', '江': 'J', '史': 'S',
      '顾': 'G', '侯': 'H', '邵': 'S', '孟': 'M', '龙': 'L', '万': 'W', '段': 'D', '雷': 'L',
      '钱': 'Q', '汤': 'T', '尹': 'Y', '黎': 'L', '易': 'Y', '常': 'C', '武': 'W', '乔': 'Q',
      '贺': 'H', '赖': 'L', '龚': 'G', '文': 'W', '用户': 'Y'
    }
    const chars = chineseChars.slice(0, 2)
    let initials = ''
    for (const ch of chars) {
      initials += pinyinMap[ch] || ch
    }
    return initials.toUpperCase() || name.substring(0, 2).toUpperCase()
  }
  // 英文/其他：取前两个字母大写
  const letters = name.replace(/[^a-zA-Z]/g, '')
  if (letters.length >= 2) {
    return letters.substring(0, 2).toUpperCase()
  }
  return name.substring(0, 2).toUpperCase()
})

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

function handleTogglePin(sessionId) {
  activeMenu.value = null
  emit('togglePin', sessionId)
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

function showSettings() {
  showUserMenu.value = false
  emit('showSettings')
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
  border: none;
  background: transparent;
  border-radius: 10px;
  font-size: 14px;
  color: #475569;
  cursor: pointer;
  transition: all 0.2s;
}

.action-btn:hover {
  background: #f1f5f9;
}

.action-btn.active {
  background: #e0f2fe;
  color: #0ea5e9;
}

.action-btn svg {
  width: 18px;
  height: 18px;
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
  display: flex;
  align-items: center;
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
  padding: 10px 14px;
  background: transparent;
  border-top: 1px solid #e2e8f0;
  cursor: pointer;
  transition: background 0.2s;
  position: relative;
}

.user-more-icon {
  width: 18px;
  height: 18px;
  color: #94a3b8;
  flex-shrink: 0;
  margin-left: auto;
  transition: color 0.2s;
}

.user-profile:hover .user-more-icon {
  color: #64748b;
}

.user-avatar {
  width: 32px;
  height: 32px;
  background: linear-gradient(135deg, #0ea5e9 0%, #8b5cf6 100%);
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  box-shadow: 0 2px 6px rgba(14, 165, 233, 0.25);
}

.user-avatar svg {
  width: 20px;
  height: 20px;
  color: white;
}

.user-avatar .user-initials {
  font-size: 13px;
  font-weight: 600;
  color: white;
  letter-spacing: 0.5px;
  line-height: 1;
}

.user-info {
  flex: 1;
  min-width: 0;
}

.user-name {
  font-size: 14px;
  font-weight: 600;
  color: #1e293b;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  line-height: 1.2;
}

.user-org {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 2px;
  font-size: 11px;
  color: #94a3b8;
  overflow: hidden;
}

.user-org span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-org-icon {
  width: 11px;
  height: 11px;
  flex-shrink: 0;
  opacity: 0.8;
}

.user-dropdown {
  position: absolute;
  bottom: 100%;
  left: 12px;
  right: 12px;
  background: white;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  box-shadow: 0 -4px 20px rgba(0, 0, 0, 0.12);
  z-index: 100;
  margin-bottom: 8px;
  overflow: hidden;
  padding: 6px;
}

.user-dropdown-divider {
  height: 1px;
  background: #e2e8f0;
  margin: 4px 8px;
}

.user-dropdown-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 10px 12px;
  border: none;
  background: transparent;
  font-size: 14px;
  color: #475569;
  cursor: pointer;
  transition: all 0.2s;
  border-radius: 8px;
}

.user-dropdown-item svg {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
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
