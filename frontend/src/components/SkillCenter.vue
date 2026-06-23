<template>
  <div class="skill-center">
    <div class="skill-center-header">
      <h2>技能中心</h2>
      <button @click="$emit('close')" class="close-btn" title="关闭">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <line x1="18" y1="6" x2="6" y2="18"></line>
          <line x1="6" y1="6" x2="18" y2="18"></line>
        </svg>
      </button>
    </div>

    <div class="tabs">
      <button
        class="tab"
        :class="{ active: activeTab === 'public' }"
        @click="switchTab('public')"
      >
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:16px;height:16px">
          <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/>
        </svg>
        公共技能
        <span class="tab-count">{{ publicSkills.length }}</span>
      </button>
      <button
        class="tab"
        :class="{ active: activeTab === 'user' }"
        @click="switchTab('user')"
      >
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:16px;height:16px">
          <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
          <circle cx="12" cy="7" r="4"></circle>
        </svg>
        我的技能
        <span class="tab-count">{{ userSkills.length }}</span>
      </button>
    </div>

    <div class="skill-center-content">
      <div v-if="loading" class="loading-state">
        <div class="spinner"></div>
        <span>加载中...</span>
      </div>

      <div v-else-if="error" class="error-state">
        <p>{{ error }}</p>
        <button @click="refresh" class="retry-btn">重试</button>
      </div>

      <!-- 公共技能 -->
      <div v-else-if="activeTab === 'public'" class="skills-grid">
        <div v-if="publicSkills.length === 0" class="empty-state">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>
          </svg>
          <p>暂无公共技能</p>
        </div>
        <div
          v-for="skill in publicSkills"
          :key="skill.dir_name"
          class="skill-card"
          @click="openPopover(skill, $event)"
        >
          <div class="skill-card-inner">
            <div class="skill-card-icon" :class="getSkillCategory(skill)">
              <span v-if="skill.icon" class="icon-emoji">{{ skill.icon }}</span>
              <svg v-else-if="getSkillCategory(skill) === 'doc'" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>
              </svg>
              <svg v-else-if="getSkillCategory(skill) === 'code'" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>
              </svg>
              <svg v-else-if="getSkillCategory(skill) === 'design'" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>
              </svg>
              <svg v-else-if="getSkillCategory(skill) === 'data'" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>
              </svg>
              <svg v-else-if="getSkillCategory(skill) === 'api'" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M18 20V10"/><path d="M12 20V4"/><path d="M6 20v-6"/>
              </svg>
              <svg v-else xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>
              </svg>
            </div>
            <div class="skill-card-body">
              <div class="skill-card-name">{{ skill.name }}</div>
              <div class="skill-card-desc">{{ skill.description || '暂无描述' }}</div>
            </div>
            <button
              class="add-icon-btn"
              :class="{ added: skill.added }"
              :disabled="skill.added || addingSkill === skill.dir_name"
              @click.stop="handleAddSkill(skill)"
              :title="skill.added ? '已添加' : '添加到我的技能'"
            >
              <div v-if="addingSkill === skill.dir_name" class="btn-spinner-sm"></div>
              <svg v-else-if="skill.added" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                <polyline points="20 6 9 17 4 12"></polyline>
              </svg>
              <svg v-else xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                <line x1="12" y1="5" x2="12" y2="19"></line>
                <line x1="5" y1="12" x2="19" y2="12"></line>
              </svg>
            </button>
          </div>
        </div>
      </div>

      <!-- 我的技能 -->
      <div v-else class="skills-grid">
        <div v-if="userSkills.length === 0" class="empty-state">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
            <circle cx="12" cy="7" r="4"></circle>
          </svg>
          <p>暂无技能，从公共技能中添加</p>
        </div>
        <div
          v-for="skill in userSkills"
          :key="skill.dir_name"
          class="skill-card"
          @click="openPopover(skill, $event)"
        >
          <div class="skill-card-inner">
            <div class="skill-card-icon" :class="getSkillCategory(skill)">
              <span v-if="skill.icon" class="icon-emoji">{{ skill.icon }}</span>
              <svg v-else-if="getSkillCategory(skill) === 'doc'" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>
              </svg>
              <svg v-else-if="getSkillCategory(skill) === 'code'" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>
              </svg>
              <svg v-else-if="getSkillCategory(skill) === 'design'" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>
              </svg>
              <svg v-else-if="getSkillCategory(skill) === 'data'" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>
              </svg>
              <svg v-else xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>
              </svg>
            </div>
            <div class="skill-card-body">
              <div class="skill-card-name">{{ skill.name }}</div>
              <div class="skill-card-desc">{{ skill.description || '暂无描述' }}</div>
            </div>
            <button
              class="remove-icon-btn"
              :disabled="removingSkill === skill.dir_name"
              @click.stop="handleRemoveSkill(skill)"
              title="移除技能"
            >
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <line x1="18" y1="6" x2="6" y2="18"></line>
                <line x1="6" y1="6" x2="18" y2="18"></line>
              </svg>
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- 悬浮详情窗 -->
    <Teleport to="body">
      <div v-if="popover.visible" class="popover-overlay" @click.self="closePopover">
        <div
          class="popover-card"
          :style="popoverStyle"
          @click.stop
        >
          <div class="popover-header">
            <div class="popover-icon" :class="getSkillCategory(popover.skill)">
              <span v-if="popover.skill?.icon" class="icon-emoji">{{ popover.skill.icon }}</span>
              <svg v-else-if="getSkillCategory(popover.skill) === 'doc'" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>
              </svg>
              <svg v-else-if="getSkillCategory(popover.skill) === 'code'" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>
              </svg>
              <svg v-else-if="getSkillCategory(popover.skill) === 'design'" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>
              </svg>
              <svg v-else-if="getSkillCategory(popover.skill) === 'data'" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>
              </svg>
              <svg v-else-if="getSkillCategory(popover.skill) === 'api'" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M18 20V10"/><path d="M12 20V4"/><path d="M6 20v-6"/>
              </svg>
              <svg v-else xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>
              </svg>
            </div>
            <div class="popover-title-area">
              <div class="popover-name">{{ popover.skill?.name }}</div>
              <div class="popover-category">{{ getCategoryLabel(popover.skill) }}</div>
            </div>
            <button class="popover-close" @click="closePopover">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <line x1="18" y1="6" x2="6" y2="18"></line>
                <line x1="6" y1="6" x2="18" y2="18"></line>
              </svg>
            </button>
          </div>
          <div class="popover-body">
            <div class="popover-desc">{{ popover.skill?.description || '暂无详细说明' }}</div>
          </div>
          <div class="popover-footer">
            <template v-if="activeTab === 'public'">
              <button
                v-if="popover.skill?.added"
                class="popover-btn added"
                disabled
              >
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:16px;height:16px">
                  <polyline points="20 6 9 17 4 12"></polyline>
                </svg>
                已添加
              </button>
              <button
                v-else
                class="popover-btn"
                :disabled="addingSkill === popover.skill?.dir_name"
                @click.stop="handleAddSkill(popover.skill)"
              >
                <div v-if="addingSkill === popover.skill?.dir_name" class="btn-spinner"></div>
                <svg v-else xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:16px;height:16px">
                  <line x1="12" y1="5" x2="12" y2="19"></line>
                  <line x1="5" y1="12" x2="19" y2="12"></line>
                </svg>
                {{ addingSkill === popover.skill?.dir_name ? '添加中...' : '添加到我的技能' }}
              </button>
            </template>
            <template v-else>
              <button
                class="popover-btn remove"
                :disabled="removingSkill === popover.skill?.dir_name"
                @click.stop="handleRemoveSkill(popover.skill)"
              >
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:16px;height:16px">
                  <polyline points="3 6 5 6 21 6"></polyline>
                  <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                </svg>
                移除技能
              </button>
            </template>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Toast 提示 -->
    <Transition name="toast">
      <div v-if="toast.show" class="toast" :class="toast.type">
        {{ toast.message }}
      </div>
    </Transition>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { getPublicSkills, getUserSkills, addSkillToUser, removeSkillFromUser } from '../api/skills.js'

const emit = defineEmits(['close'])

const activeTab = ref('public')
const loading = ref(false)
const error = ref('')
const publicSkills = ref([])
const userSkills = ref([])
const addingSkill = ref(null)
const removingSkill = ref(null)
const toast = ref({ show: false, message: '', type: 'success' })

// 悬浮窗状态
const popover = ref({
  visible: false,
  skill: null,
  x: 0,
  y: 0,
})

const popoverStyle = computed(() => {
  const x = Math.min(popover.value.x, window.innerWidth - 380)
  const y = Math.min(popover.value.y, window.innerHeight - 300)
  return {
    left: `${x}px`,
    top: `${y}px`,
  }
})

// 技能分类映射
const SKILL_CATEGORIES = {
  doc: ['pdf', 'docx', 'pptx', 'xlsx', 'doc-coauthoring'],
  code: ['mcp-builder', 'skill-creator', 'claude-api'],
  design: ['frontend-design', 'canvas-design', 'theme-factory', 'brand-guidelines', 'algorithmic-art', 'web-artifacts-builder'],
  data: ['webapp-testing', 'strategy_fx'],
  api: ['internal-comms', 'slack-gif-creator'],
}

const CATEGORY_LABELS = {
  doc: '文档处理',
  code: '代码开发',
  design: '设计创意',
  data: '数据分析',
  api: 'API集成',
  default: '通用技能',
}

function getSkillCategory(skill) {
  if (!skill) return 'default'
  for (const [cat, names] of Object.entries(SKILL_CATEGORIES)) {
    if (names.includes(skill.dir_name)) return cat
  }
  return 'default'
}

function getCategoryLabel(skill) {
  return CATEGORY_LABELS[getSkillCategory(skill)] || '通用技能'
}

function openPopover(skill, event) {
  const rect = event.currentTarget.getBoundingClientRect()
  popover.value = {
    visible: true,
    skill,
    x: rect.left,
    y: rect.bottom + 8,
  }
}

function closePopover() {
  popover.value.visible = false
  popover.value.skill = null
}

function showToast(message, type = 'success') {
  toast.value = { show: true, message, type }
  setTimeout(() => { toast.value.show = false }, 2500)
}

function switchTab(tab) {
  activeTab.value = tab
  closePopover()
  refresh()
}

async function refresh() {
  loading.value = true
  error.value = ''
  try {
    if (activeTab.value === 'public') {
      const data = await getPublicSkills()
      publicSkills.value = data.skills || []
    } else {
      const data = await getUserSkills()
      userSkills.value = data.skills || []
    }
  } catch (e) {
    error.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

async function handleAddSkill(skill) {
  if (!skill) return
  addingSkill.value = skill.dir_name
  try {
    await addSkillToUser(skill.dir_name)
    skill.added = true
    showToast(`技能「${skill.name}」添加成功`)
  } catch (e) {
    showToast(e.message || '添加失败', 'error')
  } finally {
    addingSkill.value = null
  }
}

async function handleRemoveSkill(skill) {
  if (!skill) return
  removingSkill.value = skill.dir_name
  try {
    await removeSkillFromUser(skill.dir_name)
    userSkills.value = userSkills.value.filter(s => s.dir_name !== skill.dir_name)
    const publicSkill = publicSkills.value.find(s => s.dir_name === skill.dir_name)
    if (publicSkill) publicSkill.added = false
    closePopover()
    showToast(`技能「${skill.name}」已移除`)
  } catch (e) {
    showToast(e.message || '移除失败', 'error')
  } finally {
    removingSkill.value = null
  }
}

function handleKeydown(e) {
  if (e.key === 'Escape' && popover.value.visible) {
    closePopover()
  }
}

onMounted(() => {
  refresh()
  document.addEventListener('keydown', handleKeydown)
})

onBeforeUnmount(() => {
  document.removeEventListener('keydown', handleKeydown)
})
</script>

<style scoped>
.skill-center {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: #f8fafc;
  height: 100%;
  position: relative;
}

.skill-center-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 20px 24px;
  background: white;
  border-bottom: 1px solid #e2e8f0;
}

.skill-center-header h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: #1e293b;
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
  background: #f1f5f9;
}

.close-btn svg {
  width: 20px;
  height: 20px;
  color: #64748b;
}

.tabs {
  display: flex;
  gap: 8px;
  padding: 16px 24px;
  background: white;
  border-bottom: 1px solid #e2e8f0;
}

.tab {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 20px;
  border: 1px solid #e2e8f0;
  background: white;
  border-radius: 8px;
  font-size: 14px;
  color: #64748b;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
}

.tab:hover {
  background: #f8fafc;
  border-color: #cbd5e1;
}

.tab.active {
  background: #0ea5e9;
  border-color: #0ea5e9;
  color: white;
}

.tab-count {
  background: rgba(0, 0, 0, 0.1);
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 12px;
}

.tab.active .tab-count {
  background: rgba(255, 255, 255, 0.2);
}

.skill-center-content {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
}

.loading-state,
.error-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 20px;
  color: #94a3b8;
  gap: 16px;
}

.spinner {
  width: 40px;
  height: 40px;
  border: 3px solid #e2e8f0;
  border-top-color: #0ea5e9;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.retry-btn {
  padding: 8px 20px;
  border: 1px solid #e2e8f0;
  background: white;
  border-radius: 8px;
  font-size: 14px;
  color: #475569;
  cursor: pointer;
  transition: all 0.2s;
}

.retry-btn:hover {
  background: #f1f5f9;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 20px;
  color: #94a3b8;
}

.empty-state svg {
  width: 48px;
  height: 48px;
  margin-bottom: 12px;
  opacity: 0.5;
}

.empty-state p {
  margin: 0;
  font-size: 14px;
}

.skills-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
}

.skill-card {
  background: white;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  transition: all 0.2s;
  cursor: pointer;
}

.skill-card:hover {
  border-color: #cbd5e1;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.06);
}

.skill-card-inner {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
}

.skill-card-icon {
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  flex-shrink: 0;
  font-size: 18px;
}

.skill-card-icon svg {
  width: 20px;
  height: 20px;
}

/* 差异化图标颜色 */
.skill-card-icon.doc { background: #fef3c7; color: #d97706; }
.skill-card-icon.doc svg { color: #d97706; }
.skill-card-icon.code { background: #ede9fe; color: #7c3aed; }
.skill-card-icon.code svg { color: #7c3aed; }
.skill-card-icon.design { background: #fce7f3; color: #db2777; }
.skill-card-icon.design svg { color: #db2777; }
.skill-card-icon.data { background: #d1fae5; color: #059669; }
.skill-card-icon.data svg { color: #059669; }
.skill-card-icon.api { background: #e0e7ff; color: #4f46e5; }
.skill-card-icon.api svg { color: #4f46e5; }
.skill-card-icon.default { background: #f0f9ff; color: #0ea5e9; }
.skill-card-icon.default svg { color: #0ea5e9; }

.icon-emoji { line-height: 1; }

.skill-card-body {
  flex: 1;
  min-width: 0;
}

.skill-card-name {
  font-size: 14px;
  font-weight: 600;
  color: #1e293b;
  margin-bottom: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.skill-card-desc {
  font-size: 12px;
  color: #94a3b8;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
}

/* 右上角 + 按钮 */
.add-icon-btn {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid #e2e8f0;
  background: white;
  border-radius: 6px;
  cursor: pointer;
  flex-shrink: 0;
  transition: all 0.2s;
  color: #0ea5e9;
}

.add-icon-btn:hover:not(:disabled) {
  background: #f0f9ff;
  border-color: #0ea5e9;
}

.add-icon-btn:disabled {
  cursor: not-allowed;
}

.add-icon-btn.added {
  color: #94a3b8;
  border-color: #e2e8f0;
  background: #f8fafc;
}

.add-icon-btn svg {
  width: 14px;
  height: 14px;
}

.btn-spinner-sm {
  width: 12px;
  height: 12px;
  border: 2px solid #e2e8f0;
  border-top-color: #0ea5e9;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

/* 右上角 x 按钮（我的技能） */
.remove-icon-btn {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid transparent;
  background: transparent;
  border-radius: 6px;
  cursor: pointer;
  flex-shrink: 0;
  transition: all 0.2s;
  color: #cbd5e1;
}

.remove-icon-btn:hover:not(:disabled) {
  color: #ef4444;
  background: #fef2f2;
  border-color: #fecaca;
}

.remove-icon-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.remove-icon-btn svg {
  width: 14px;
  height: 14px;
}

/* 悬浮详情窗 */
.popover-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 9999;
  background: transparent;
}

.popover-card {
  position: fixed;
  width: 360px;
  background: white;
  border-radius: 16px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.15), 0 0 0 1px rgba(0, 0, 0, 0.05);
  overflow: hidden;
  animation: popover-in 0.15s ease-out;
  z-index: 10000;
}

@keyframes popover-in {
  from { opacity: 0; transform: translateY(-8px) scale(0.97); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}

.popover-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 20px 20px 0;
}

.popover-icon {
  width: 44px;
  height: 44px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 10px;
  flex-shrink: 0;
  font-size: 22px;
}

.popover-icon svg {
  width: 24px;
  height: 24px;
}

.popover-icon.doc { background: #fef3c7; color: #d97706; }
.popover-icon.doc svg { color: #d97706; }
.popover-icon.code { background: #ede9fe; color: #7c3aed; }
.popover-icon.code svg { color: #7c3aed; }
.popover-icon.design { background: #fce7f3; color: #db2777; }
.popover-icon.design svg { color: #db2777; }
.popover-icon.data { background: #d1fae5; color: #059669; }
.popover-icon.data svg { color: #059669; }
.popover-icon.api { background: #e0e7ff; color: #4f46e5; }
.popover-icon.api svg { color: #4f46e5; }
.popover-icon.default { background: #f0f9ff; color: #0ea5e9; }
.popover-icon.default svg { color: #0ea5e9; }

.popover-title-area {
  flex: 1;
  min-width: 0;
}

.popover-name {
  font-size: 16px;
  font-weight: 600;
  color: #1e293b;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.popover-category {
  font-size: 12px;
  color: #94a3b8;
  margin-top: 2px;
}

.popover-close {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: transparent;
  border-radius: 6px;
  cursor: pointer;
  color: #94a3b8;
  transition: all 0.2s;
  flex-shrink: 0;
}

.popover-close:hover {
  background: #f1f5f9;
  color: #475569;
}

.popover-close svg {
  width: 16px;
  height: 16px;
}

.popover-body {
  padding: 16px 20px;
}

.popover-desc {
  font-size: 13px;
  color: #475569;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 240px;
  overflow-y: auto;
}

.popover-footer {
  padding: 0 20px 20px;
  display: flex;
  justify-content: flex-end;
}

.popover-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 20px;
  border: 1px solid #0ea5e9;
  background: white;
  border-radius: 8px;
  font-size: 13px;
  color: #0ea5e9;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
  font-weight: 500;
}

.popover-btn:hover:not(:disabled) {
  background: #f0f9ff;
}

.popover-btn:disabled {
  cursor: not-allowed;
}

.popover-btn.added {
  border-color: #cbd5e1;
  color: #94a3b8;
  background: #f8fafc;
}

.popover-btn.remove {
  border-color: #fca5a5;
  color: #ef4444;
}

.popover-btn.remove:hover:not(:disabled) {
  background: #fef2f2;
}

.btn-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid #e2e8f0;
  border-top-color: #0ea5e9;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

/* Toast */
.toast {
  position: absolute;
  bottom: 24px;
  left: 50%;
  transform: translateX(-50%);
  padding: 10px 20px;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 500;
  z-index: 100;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}

.toast.success {
  background: #f0fdf4;
  color: #16a34a;
  border: 1px solid #bbf7d0;
}

.toast.error {
  background: #fef2f2;
  color: #dc2626;
  border: 1px solid #fecaca;
}

.toast-enter-active,
.toast-leave-active {
  transition: all 0.3s ease;
}
</style>
