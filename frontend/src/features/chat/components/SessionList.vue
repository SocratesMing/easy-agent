<template>
  <div class="session-list">
    <div class="session-header">
      <div class="header-left">
        <span class="logo-text">{{ APP_TITLE }}</span>
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
      
      <button v-if="knowledgeEnabled" @click="$emit('showKnowledge')" class="action-btn assets" :class="{ active: showKnowledge }">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path>
        </svg>
        <span>知识库</span>
      </button>

      <button @click="$emit('showSkillCenter')" class="action-btn skill-center">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>
        </svg>
        <span>专家 · 技能 · 连接器</span>
      </button>

      <button @click="$emit('showScheduledTasks')" class="action-btn scheduled-tasks">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="12" cy="12" r="10"></circle>
          <polyline points="12 6 12 12 16 14"></polyline>
        </svg>
        <span>定时任务</span>
      </button>
    </div>

    <div class="divider"></div>

    <div class="session-items" @scroll="onSessionsScroll">
      <div
        v-for="group in groupedSessions"
        :key="group.label"
        class="session-group"
      >
        <div class="session-group-header">
          <span class="session-group-label">{{ group.label }}</span>
          <span class="session-group-count">{{ group.sessions.length }}</span>
        </div>
        <div
          v-for="session in group.sessions"
          :key="session.session_id"
          class="session-item"
          :class="{ active: !showKnowledge && session.session_id === currentSessionId, streaming: streamingSessionIds.includes(session.session_id) }"
          @click="$emit('selectSession', session.session_id)"
        >
          <div class="session-info">
            <div class="session-name">
              <svg v-if="session.pinned" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="#f59e0b" stroke="#f59e0b" stroke-width="1" style="width:13px;height:13px;flex-shrink:0;margin-right:2px">
                <path d="M12 2L2 7l10 5 10-5-10-5z"></path>
                <path d="M2 17l10 5 10-5"></path>
                <path d="M2 12l10 5 10-5"></path>
              </svg>
              <span class="session-title-text">{{ sessionTitle(session) }}</span>
              <span v-if="streamingSessionIds.includes(session.session_id)" class="streaming-badge" title="进行中">
                <span class="streaming-dot"></span>
              </span>
              <span v-if="session.pending_approval || pendingApprovalSessionIds.includes(session.session_id)" class="approval-flag" title="该会话存在待审批操作，需处理后才能继续">待审批</span>
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
              <button :disabled="archiveBusy(session.session_id)" @click.stop="handleTogglePin(session.session_id)" class="menu-item">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px">
                  <path d="M12 2L2 7l10 5 10-5-10-5z"></path>
                  <path d="M2 17l10 5 10-5"></path>
                  <path d="M2 12l10 5 10-5"></path>
                </svg>
                {{ session.pinned ? '取消置顶' : '置顶' }}
              </button>
              <button @click.stop="archiveRow(session)" class="menu-item" :disabled="archiveBusy(session.session_id)"><svg aria-hidden="true" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3h18v4H3zM5 7v14h14V7M10 11h4"></path></svg>归档</button>
              <button :disabled="archiveBusy(session.session_id)" @click.stop="startRename(session)" class="menu-item">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path>
                  <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path>
                </svg>
                重命名
              </button>
              <button :disabled="archiveBusy(session.session_id)" @click.stop="handleDelete(session.session_id)" class="menu-item delete">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <polyline points="3 6 5 6 21 6"></polyline>
                  <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                </svg>
                删除
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- 分页：滚动到底续取下一页；窗口很高没有滚动条时可点这里 -->
      <button v-if="sessionsHasMore" class="session-more-hint" :disabled="sessionsLoading" @click="$emit('loadMoreSessions')">{{ sessionsLoading ? '加载中…' : '滚动到底加载更早的会话' }}</button>

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

    <div v-if="showDeleteConfirm" class="modal-overlay" @click="cancelDelete">
      <div class="modal-content" @click.stop>
        <h3>删除会话</h3>
        <p class="delete-confirm-text">确定删除会话「{{ deleteTargetTitle }}」吗？删除后消息与该会话文件将无法恢复。</p>
        <div class="modal-actions">
          <button @click="cancelDelete" class="cancel-btn">取消</button>
          <button @click="confirmDelete" class="confirm-btn danger">删除</button>
        </div>
      </div>
    </div>

    <ArchiveManager ref="archiveManager" />
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
      
      <!-- @click.stop：菜单项是 .user-profile 的子元素，不加 stop 会冒泡到父级的
           toggleUserMenu 把刚关闭的菜单又切回打开（且它的 stopPropagation 会挡掉
           document 上的关闭监听），导致点「设置」后菜单一直不消失 -->
      <div v-if="showUserMenu" class="user-dropdown" @click.stop>
        <button class="user-dropdown-item" @click="showSettings">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="3"></circle>
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
          </svg>
          设置
        </button>
        <button
          v-if="isSuperAdmin"
          class="user-dropdown-item"
          @click="showUserManagement"
        >
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path>
            <circle cx="9" cy="7" r="4"></circle>
            <path d="M23 21v-2a4 4 0 0 0-3-3.87"></path>
            <path d="M16 3.13a4 4 0 0 1 0 7.75"></path>
          </svg>
          用户管理
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
import ArchiveManager from './ArchiveManager.vue'
import { ref, nextTick, onMounted, computed } from 'vue'
import { APP_TITLE } from '../../../config.js'
import { ensureSkillDirectoryLoaded, skillFreeTitle } from '../../skills/store/skillDirectoryStore.js'

const emit = defineEmits(['createSession', 'selectSession', 'deleteSession', 'renameSession', 'toggleSidebar', 'showKnowledge', 'showSkillCenter', 'showScheduledTasks', 'showSettings', 'showUserManagement', 'logout', 'togglePin', 'loadMoreSessions'])

const props = defineProps({
  sessions: {
    type: Array,
    default: () => []
  },
  currentSessionId: {
    type: String,
    default: null
  },
  streamingSessionIds: {
    type: Array,
    default: () => []
  },
  pendingApprovalSessionIds: {
    type: Array,
    default: () => []
  },
  username: {
    type: String,
    default: ''
  },
  // 超级管理员账号（后端 config.yaml 下发，admin 恒在其中）
  superAdminUsernames: {
    type: Array,
    default: () => ['admin']
  },
  organizationId: {
    type: String,
    default: ''
  },
  email: {
    type: String,
    default: ''
  },
  knowledgeEnabled: {
    type: Boolean,
    default: false
  },
  showKnowledge: {
    type: Boolean,
    default: false
  },
  // 分页：还有更早的会话 / 正在续取
  sessionsHasMore: {
    type: Boolean,
    default: false
  },
  sessionsLoading: {
    type: Boolean,
    default: false
  }
})
const activeMenu = ref(null)
const archiveManager = ref(null)
function archiveRow(session) { activeMenu.value = null; archiveManager.value.archive(session) }
function archiveBusy(id) { return !!archiveManager.value?.busy[id] }

// 滚动到底部附近才续取下一页，避免预先加载全部会话
function onSessionsScroll(event) {
  if (!props.sessionsHasMore || props.sessionsLoading) return
  const el = event.target
  if (el.scrollHeight - el.scrollTop - el.clientHeight > 48) return
  emit('loadMoreSessions')
}
const showRenameModal = ref(false)
const newTitle = ref('')
const renamingSession = ref(null)
const renameInput = ref(null)
const showUserMenu = ref(false)
const showDeleteConfirm = ref(false)
const deleteTarget = ref(null)

// 与后端 is_super_admin 保持一致：账号列表由 config.yaml 的
// personnel.super_admin_usernames 下发（admin 恒为超级管理员）
const isSuperAdmin = computed(() => props.superAdminUsernames.includes(String(props.username || '')))

// 删除会话前必须二次确认（D3）：误点删除会同时清空当前聊天视图且不可恢复。
const deleteTargetTitle = computed(() => sessionTitle(deleteTarget.value))

// 按时间分组会话：置顶 / 今天 / 最近一周 / 更早
const groupedSessions = computed(() => {
  const now = new Date()
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const weekAgo = startOfToday - 7 * 24 * 60 * 60 * 1000

  const pinned = []
  const today = []
  const week = []
  const earlier = []

  for (const s of props.sessions) {
    if (s.pinned) {
      pinned.push(s)
      continue
    }
    const ts = new Date(s.updated_at || s.created_at || Date.now()).getTime()
    if (ts >= startOfToday) {
      today.push(s)
    } else if (ts >= weekAgo) {
      week.push(s)
    } else {
      earlier.push(s)
    }
  }

  const groups = []
  if (pinned.length) groups.push({ label: '置顶', sessions: pinned })
  if (today.length) groups.push({ label: '今天', sessions: today })
  if (week.length) groups.push({ label: '最近一周', sessions: week })
  if (earlier.length) groups.push({ label: '更早', sessions: earlier })
  return groups
})

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

/**
 * 侧边栏展示用标题：老会话标题是首条消息原文，可能带旧前端拼接的 `/技能名` 指令前缀，
 * 这里按技能目录反查还原（见 store/skillDirectoryStore.js）。新会话标题本来就是用户原话，
 * 目录名不匹配时原样返回。
 */
function sessionTitle(session) {
  return skillFreeTitle(session && session.title) || '未命名会话'
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
  const target = props.sessions.find(s => s.session_id === sessionId)
  deleteTarget.value = target || { session_id: sessionId, title: '' }
  showDeleteConfirm.value = true
}

function cancelDelete() {
  showDeleteConfirm.value = false
  deleteTarget.value = null
}

function confirmDelete() {
  const target = deleteTarget.value
  if (target && target.session_id) {
    emit('deleteSession', target.session_id)
  }
  cancelDelete()
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
  // 标题里的 /技能名 前缀要按技能目录反查，目录加载完成后标题会自动刷新
  ensureSkillDirectoryLoaded()
})

function closeUserMenuSilent() {
  showUserMenu.value = false
}

function showSettings() {
  showUserMenu.value = false
  emit('showSettings')
}

function showUserManagement() {
  showUserMenu.value = false
  emit('showUserManagement')
}

function handleLogout() {
  showUserMenu.value = false
  emit('logout')
}
</script>

<style scoped src="./SessionList.css"></style>

<style>
/* Dark theme overrides for session list (non-scoped for root HTML) */
html.dark .session-group-header .group-label {
  color: #94a3b8;
}

html.dark .session-group-header .group-count {
  color: var(--text-secondary);
  background: #334155;
}
</style>
