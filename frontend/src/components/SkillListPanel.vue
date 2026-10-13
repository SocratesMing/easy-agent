<!--
  技能列表（技能 Tab 主视图）：
  - 工具条：搜索 / 已安装(n) 筛选 / 添加技能（下拉：上传技能 = zip 导入、创建技能占位）
  - 分类 chips：全部 + 后端返回的用户类别（可「管理分类」新建/重命名/删除/排序）
  - 列表：不分组的平铺网格（分类只体现在 chips 筛选与卡片上的归类标签）
  - 卡片：图标 / 名称 / 状态标签 / 右侧操作（未添加显示 +，已添加显示对勾标记；
    切到「已安装」筛选后已添加的显示启用开关）/ 单行描述 / 底部真实指标
    + 底部分类标签：点开可把技能改到别的类别或恢复自动归类
  - 点卡片名称 → 详情页（SkillDetailPanel）
-->
<template>
  <!-- 点击空白处收起卡片的归类下拉与「添加技能」菜单 -->
  <div class="skill-panel" @click="closeOverlays">
    <SkillDetailPanel
      v-if="detailTarget"
      :dir-name="detailTarget.dirName"
      :scope="detailTarget.scope"
      @back="detailTarget = null"
      @refresh="refresh"
      @close="$emit('close')"
    />

    <template v-else>
      <div class="skill-toolbar">
        <div class="search-box">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
            <circle cx="11" cy="11" r="8" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
          <input
            v-model="keyword"
            type="text"
            placeholder="搜索技能"
            aria-label="搜索技能"
            spellcheck="false"
            autocomplete="off"
          />
          <button v-if="keyword" class="search-clear" aria-label="清空搜索" @click="keyword = ''">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        <button
          class="installed-filter"
          :class="{ active: installedOnly }"
          :aria-pressed="installedOnly ? 'true' : 'false'"
          title="只看已安装的技能；启用 / 停用开关在此视图显示"
          @click="installedOnly = !installedOnly"
        >
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <rect x="3" y="3" width="18" height="18" rx="3" />
            <polyline points="8 12.5 11 15.5 16 9" />
          </svg>
          已安装<span class="filter-count">{{ installedCount }}</span>
        </button>

        <div class="add-skill-menu">
          <button
            class="action-btn-primary"
            :disabled="importing"
            aria-haspopup="menu"
            :aria-expanded="addMenuOpen ? 'true' : 'false'"
            @click.stop="addMenuOpen = !addMenuOpen"
          >
            <div v-if="importing" class="btn-spinner-sm"></div>
            <svg v-else xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
              <line x1="12" y1="5" x2="12" y2="19" />
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
            {{ importing ? '导入中…' : '添加技能' }}
          </button>

          <div v-if="addMenuOpen" class="add-skill-menu-list" role="menu">
            <button class="add-menu-item" type="button" role="menuitem" @click.stop="handleUploadSkill">
              上传技能
            </button>
            <button class="add-menu-item" type="button" role="menuitem" @click.stop="handleCreateSkill">
              创建技能
            </button>
          </div>
        </div>
        <input ref="fileInputRef" type="file" accept=".zip" class="hidden-file-input" @change="handleImportFile" />
      </div>

      <div class="category-chips" role="group" aria-label="按分类筛选">
        <button
          v-for="item in chipCategories"
          :key="item.id"
          class="chip"
          :class="{ active: category === item.id }"
          :aria-pressed="category === item.id ? 'true' : 'false'"
          @click="category = item.id"
        >
          {{ item.name }}
          <span v-if="categoryCounts[item.id]" class="chip-count">{{ categoryCounts[item.id] }}</span>
        </button>
        <button class="chip chip-manage" type="button" @click="openCategoryManager">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
            <line x1="4" y1="21" x2="4" y2="14" />
            <line x1="4" y1="10" x2="4" y2="3" />
            <line x1="12" y1="21" x2="12" y2="12" />
            <line x1="12" y1="8" x2="12" y2="3" />
            <line x1="20" y1="21" x2="20" y2="16" />
            <line x1="20" y1="12" x2="20" y2="3" />
            <line x1="1" y1="14" x2="7" y2="14" />
            <line x1="9" y1="8" x2="15" y2="8" />
            <line x1="17" y1="16" x2="23" y2="16" />
          </svg>
          管理分类
        </button>
      </div>

      <div class="skill-center-content">
        <!-- 加载态：骨架卡片，提前呈现最终布局，避免内容跳动 -->
        <div v-if="loading" class="skills-grid" aria-busy="true" aria-live="polite">
          <div v-for="n in 6" :key="n" class="skill-card skeleton-card">
            <div class="skill-card-inner">
              <span class="skeleton-block skeleton-icon"></span>
              <span class="skeleton-lines">
                <span class="skeleton-block skeleton-line w-60"></span>
                <span class="skeleton-block skeleton-line w-90"></span>
              </span>
            </div>
            <div class="skill-card-footer">
              <span class="skeleton-block skeleton-line w-40"></span>
            </div>
          </div>
        </div>

        <div v-else-if="error" class="state-box">
          <span class="state-icon is-error">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true">
              <circle cx="12" cy="12" r="9" />
              <line x1="12" y1="8" x2="12" y2="13" />
              <line x1="12" y1="16.5" x2="12" y2="16.6" />
            </svg>
          </span>
          <p class="state-title">技能列表加载失败</p>
          <p class="state-sub">{{ error }}</p>
          <button class="action-btn-outline" @click="load">重试</button>
        </div>

        <div v-else-if="filteredCards.length === 0" class="state-box">
          <span class="state-icon">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true">
              <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
            </svg>
          </span>
          <p class="state-title">{{ emptyText }}</p>
          <p class="state-sub">可以从公共技能库添加，或用「添加技能」上传技能压缩包</p>
        </div>

        <div v-else class="skills-grid">
          <div
            v-for="card in filteredCards"
            :key="card.key"
            class="skill-card"
            :class="{ 'menu-open': pickerCard === card.key }"
          >
            <div class="skill-card-inner">
              <button class="skill-card-open" @click="openDetail(card)">
                <SkillIcon :icon="card.icon" :category="iconCategoryFor(card.categoryId)" />
                <span class="skill-card-body">
                  <span class="skill-card-name-row">
                    <span class="skill-card-name">{{ card.name }}</span>
                    <span v-if="card.source === 'local'" class="skill-tag">导入</span>
                    <span v-else-if="card.installed && !card.enabled" class="skill-tag off">已停用</span>
                  </span>
                  <span class="skill-card-desc">{{ card.description || '暂无描述' }}</span>
                </span>
              </button>

              <!-- 只有切到「已安装」筛选（即进入管理已装技能的语境）才给启用/停用开关，
                   默认浏览视图不显示开关 -->
              <ToggleSwitch
                v-if="card.installed && installedOnly"
                :model-value="card.enabled"
                :loading="togglingKey === card.key"
                :aria-label="`${card.enabled ? '停用' : '启用'}技能 ${card.name}`"
                @change="handleToggle(card, $event)"
              />
              <!-- 已添加：只给一个对勾状态标记（不写文字），停用时降为灰色 -->
              <span
                v-else-if="card.installed"
                class="skill-added-tag"
                :class="{ off: !card.enabled }"
                role="img"
                :aria-label="card.enabled ? '已添加到我的技能（已启用）' : '已添加到我的技能（已停用）'"
                :title="card.enabled ? '已添加到我的技能（已启用）' : '已添加到我的技能（已停用）'"
              >
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true">
                  <polyline points="20 6 9 17 4 12" />
                </svg>
              </span>
              <button
                v-else
                class="add-icon-btn"
                :disabled="addingKey === card.key"
                :aria-label="`添加技能 ${card.name}`"
                :title="'添加到我的技能'"
                @click="handleAdd(card)"
              >
                <div v-if="addingKey === card.key" class="btn-spinner-sm"></div>
                <svg v-else xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true">
                  <line x1="12" y1="5" x2="12" y2="19" />
                  <line x1="5" y1="12" x2="19" y2="12" />
                </svg>
              </button>
            </div>

            <div class="skill-card-footer">
              <span>{{ card.stats.file_count }} 个文件</span>
              <span class="dot">·</span>
              <span>{{ formatSize(card.stats.size_bytes) }}</span>
              <span class="dot">·</span>
              <span>{{ formatTime(card.stats.updated_at) }}</span>

              <div class="category-picker">
                <button
                  class="category-picker-btn"
                  type="button"
                  :aria-expanded="pickerCard === card.key ? 'true' : 'false'"
                  :title="'调整「' + card.name + '」的类别'"
                  @click.stop="toggleCategoryPicker(card)"
                >
                  {{ card.categoryName }}
                  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
                    <polyline points="6 9 12 15 18 9" />
                  </svg>
                </button>

                <div v-if="pickerCard === card.key" class="category-picker-menu" role="menu">
                  <button
                    v-for="option in pickerOptions(card)"
                    :key="option.id"
                    class="picker-item"
                    :class="{ active: option.active }"
                    type="button"
                    role="menuitem"
                    @click.stop="handleAssign(card, option.id)"
                  >
                    {{ option.name }}
                  </button>
                  <button
                    class="picker-item picker-manage"
                    type="button"
                    role="menuitem"
                    @click.stop="openCategoryManager"
                  >
                    管理分类…
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </template>

    <SkillCategoryManager
      v-if="categoryManagerOpen"
      :categories="categories"
      :default-category="defaultCategory"
      :counts="categoryCounts"
      @close="categoryManagerOpen = false"
      @changed="handleCategoriesChanged"
    />

    <Transition name="toast">
      <div v-if="toast.show" class="toast" :class="toast.type" role="status" aria-live="polite">
        {{ toast.message }}
      </div>
    </Transition>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import {
  getPublicSkills,
  getUserSkills,
  addSkillToUser,
  enableSkill,
  disableSkill,
  importSkill,
  getSkillCategories,
  assignSkillCategory,
} from '../api/skills.js'
import { DEFAULT_CATEGORY, iconCategoryFor } from '../utils/skillCategories.js'
import SkillIcon from './SkillIcon.vue'
import SkillDetailPanel from './SkillDetailPanel.vue'
import SkillCategoryManager from './SkillCategoryManager.vue'
import ToggleSwitch from './ToggleSwitch.vue'

const emit = defineEmits(['close', 'create-skill'])

const loading = ref(false)
const error = ref('')
const publicSkills = ref([])
const userSkills = ref([])
const keyword = ref('')
const category = ref('all')
const installedOnly = ref(false)
const addingKey = ref(null)
const togglingKey = ref(null)
const importing = ref(false)
const fileInputRef = ref(null)
const detailTarget = ref(null)
const toast = ref({ show: false, message: '', type: 'success' })

// 类别由后端管理（用户级持久化），默认兜底类别固定为「通用技能」
const categories = ref([])
const defaultCategory = ref(DEFAULT_CATEGORY)
// 卡片上的归类下拉：记录当前展开的卡片 key
const pickerCard = ref(null)
const categoryManagerOpen = ref(false)
// 「添加技能」下拉菜单（上传技能 / 创建技能）
const addMenuOpen = ref(false)

function showToast(message, type = 'success') {
  toast.value = { show: true, message, type }
  setTimeout(() => { toast.value = { ...toast.value, show: false } }, 2600)
}

function formatSize(bytes) {
  const value = Number(bytes || 0)
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

function formatTime(iso) {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  const diff = date.getTime() - Date.now()
  const day = 86400000
  const hour = 3600000
  const minute = 60000
  const rtf = new Intl.RelativeTimeFormat('zh-CN', { numeric: 'auto' })
  if (Math.abs(diff) >= day && Math.abs(diff) <= 30 * day) return rtf.format(Math.round(diff / day), 'day')
  if (Math.abs(diff) >= hour && Math.abs(diff) < day) return rtf.format(Math.round(diff / hour), 'hour')
  if (Math.abs(diff) < hour) return rtf.format(Math.round(diff / minute), 'minute')
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium' }).format(date)
}

/**
 * 卡片数据 = 公共技能目录（带安装/启用状态）+ 用户目录里不在目录中的技能（本地导入）。
 * 同一个 dir_name 只会出现一次，避免市场技能与其用户副本重复展示。
 */
const cards = computed(() => {
  const list = []
  const catalogNames = new Set(publicSkills.value.map((s) => s.dir_name))

  for (const skill of publicSkills.value) {
    list.push({
      key: `public:${skill.dir_name}`,
      dirName: skill.dir_name,
      scope: skill.added ? 'user' : 'public',
      source: 'catalog',
      name: skill.name,
      description: skill.description,
      icon: skill.icon,
      version: skill.version,
      categoryId: skill.category_id || DEFAULT_CATEGORY.id,
      categoryName: skill.category_name || DEFAULT_CATEGORY.name,
      installed: !!skill.added,
      enabled: !!skill.enabled,
      stats: skill.stats || { file_count: 0, size_bytes: 0, updated_at: null },
    })
  }

  for (const skill of userSkills.value) {
    if (catalogNames.has(skill.dir_name)) continue
    list.push({
      key: `user:${skill.dir_name}`,
      dirName: skill.dir_name,
      scope: 'user',
      source: 'local',
      name: skill.name,
      description: skill.description,
      icon: skill.icon,
      version: skill.version,
      categoryId: skill.category_id || DEFAULT_CATEGORY.id,
      categoryName: skill.category_name || DEFAULT_CATEGORY.name,
      installed: true,
      enabled: !!skill.enabled,
      stats: skill.stats || { file_count: 0, size_bytes: 0, updated_at: null },
    })
  }

  // 已安装的排前面，其次按名称（类别顺序在分组时处理）
  return list.sort((a, b) => (
    Number(b.installed) - Number(a.installed) ||
    a.name.localeCompare(b.name, 'zh-Hans-CN')
  ))
})

const installedCount = computed(() => cards.value.filter((c) => c.installed).length)

const categoryCounts = computed(() => {
  const counts = { all: cards.value.length }
  for (const card of cards.value) {
    counts[card.categoryId] = (counts[card.categoryId] || 0) + 1
  }
  return counts
})

/** 类别展示顺序：用户类别按 order，兜底类别固定最后 */
const orderedCategories = computed(() => [
  ...categories.value,
  { ...defaultCategory.value, builtin: false },
])

/** chips：全部 + 有技能的类别（空类别不进筛选条，可在「管理分类」里看到） */
const chipCategories = computed(() => [
  { id: 'all', name: '全部' },
  ...orderedCategories.value.filter((item) => categoryCounts.value[item.id]),
])

const filteredCards = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  return cards.value.filter((card) => {
    if (installedOnly.value && !card.installed) return false
    if (category.value !== 'all' && card.categoryId !== category.value) return false
    if (!kw) return true
    return (
      card.name.toLowerCase().includes(kw) ||
      (card.description || '').toLowerCase().includes(kw) ||
      card.dirName.toLowerCase().includes(kw)
    )
  })
})


const emptyText = computed(() => {
  if (keyword.value.trim()) return `没有匹配「${keyword.value.trim()}」的技能`
  if (installedOnly.value) return '还没有已安装的技能，点卡片上的 + 添加，或「添加技能」导入 zip'
  return '暂无技能'
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [publicData, userData, categoryData] = await Promise.all([
      getPublicSkills(),
      getUserSkills(),
      getSkillCategories(),
    ])
    applyData(publicData, userData, categoryData)
  } catch (e) {
    error.value = e.message || '获取技能列表失败'
  } finally {
    loading.value = false
  }
}

function applyData(publicData, userData, categoryData) {
  publicSkills.value = publicData.skills || []
  userSkills.value = userData.skills || []
  categories.value = categoryData.categories || []
  defaultCategory.value = categoryData.default_category || DEFAULT_CATEGORY
}

async function refresh() {
  // 详情页 / 归类操作后刷新：静默刷新列表，不闪 loading
  try {
    const [publicData, userData, categoryData] = await Promise.all([
      getPublicSkills(),
      getUserSkills(),
      getSkillCategories(),
    ])
    applyData(publicData, userData, categoryData)
  } catch (_) { /* 静默失败，保留旧数据 */ }
}

// ── 类别归类 ──────────────────────────────────────────────────────────

function toggleCategoryPicker(card) {
  pickerCard.value = pickerCard.value === card.key ? null : card.key
}

/** 归类下拉里的选项：全部类别 + 「自动归类」（清除显式归属） */
function pickerOptions(card) {
  return [
    ...orderedCategories.value.map((item) => ({
      id: item.id,
      name: item.name,
      active: item.id === card.categoryId,
    })),
    { id: '', name: '自动归类', active: false },
  ]
}

async function handleAssign(card, categoryId) {
  pickerCard.value = null
  if (categoryId === card.categoryId) return
  try {
    const result = await assignSkillCategory(card.dirName, categoryId)
    await refresh()
    showToast(`「${card.name}」已归入「${result.category_name || '通用技能'}」`)
  } catch (e) {
    showToast(e.message || '更新技能类别失败', 'error')
  }
}

function openCategoryManager() {
  pickerCard.value = null
  categoryManagerOpen.value = true
}

/** 类别管理面板改动后：刷新类别与分组 */
async function handleCategoriesChanged(message) {
  await refresh()
  if (message) showToast(message)
}

function openDetail(card) {
  detailTarget.value = { dirName: card.dirName, scope: card.scope }
}

async function handleAdd(card) {
  addingKey.value = card.key
  try {
    await addSkillToUser(card.dirName)
    await refresh()
    showToast(`已添加「${card.name}」`)
  } catch (e) {
    showToast(e.message || '添加失败', 'error')
  } finally {
    addingKey.value = null
  }
}

async function handleToggle(card, next) {
  togglingKey.value = card.key
  try {
    await (next ? enableSkill(card.dirName) : disableSkill(card.dirName))
    await refresh()
    showToast(next ? `已启用「${card.name}」，下次对话生效` : `已停用「${card.name}」，Agent 不再加载`)
  } catch (e) {
    showToast(e.message || '操作失败', 'error')
  } finally {
    togglingKey.value = null
  }
}

/** 收起所有浮层（点面板空白处时触发） */
function closeOverlays() {
  pickerCard.value = null
  addMenuOpen.value = false
}

/** 「添加技能」菜单：上传技能 = 原有的 zip 导入 */
function handleUploadSkill() {
  addMenuOpen.value = false
  triggerImport()
}

// 创建技能：跳转到新会话，并把 skill-creator 技能与提示词预填进输入框。
// 预填内容通过 sessionStorage 传递：技能中心关闭后 ChatInput 会重新挂载并读取。
const SKILL_DRAFT_KEY = 'easy_agent_input_draft'
const SKILL_PENDING_KEY = 'easy_agent_pending_skill'
const CREATE_SKILL_DIR = 'skill-creator'
const CREATE_SKILL_PROMPT = '请帮我创建一个可以实现「......」的skill'

/**
 * 「添加技能」菜单：创建技能
 *
 * 1. 确保 skill-creator 已添加到「我的技能」（未安装时 Agent 侧拿不到这个技能）；
 * 2. 写入预填草稿 + 待选技能，供 ChatInput 挂载时恢复；
 * 3. 上抛 create-skill，由 App 切换到新会话。
 */
async function handleCreateSkill() {
  addMenuOpen.value = false

  const card = cards.value.find((item) => item.dirName === CREATE_SKILL_DIR)
  if (!card || !card.installed) {
    try {
      await addSkillToUser(CREATE_SKILL_DIR)
    } catch (e) {
      // 已添加等情况直接忽略，只有真正的失败才提示
      if (!String(e?.message || '').includes('已添加')) {
        showToast(e.message || '准备创建技能失败', 'error')
      }
    }
  }

  try {
    sessionStorage.setItem(SKILL_DRAFT_KEY, CREATE_SKILL_PROMPT)
    sessionStorage.setItem(
      SKILL_PENDING_KEY,
      JSON.stringify({
        dir_name: CREATE_SKILL_DIR,
        name: card?.name || CREATE_SKILL_DIR,
        icon: card?.icon || '',
        category_id: card?.categoryId || 'default',
      })
    )
  } catch (_) { /* 隐私模式等场景忽略 */ }

  emit('create-skill')
}

function triggerImport() {
  fileInputRef.value?.click()
}

async function handleImportFile(event) {
  const file = event.target.files?.[0]
  if (!file) return
  importing.value = true
  try {
    const result = await importSkill(file)
    await refresh()
    showToast(result.message || '导入成功')
  } catch (e) {
    showToast(e.message || '导入失败', 'error')
  } finally {
    importing.value = false
    if (fileInputRef.value) fileInputRef.value.value = ''
  }
}

onMounted(load)
</script>

<style scoped>
.skill-panel {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  position: relative;
}

/* ── 工具条 ── */
.skill-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  row-gap: 10px;
  flex-wrap: wrap;
  padding: 18px 24px 0;
}

.search-box {
  flex: 1;
  min-width: 160px;
  max-width: 320px;
  height: 38px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 12px;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: var(--bg-surface);
  transition: border-color 0.18s ease, box-shadow 0.18s ease;
}

.search-box:hover {
  border-color: color-mix(in srgb, var(--accent-color) 35%, var(--border-color));
}

.search-box:focus-within {
  border-color: var(--accent-color);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent-color) 15%, transparent);
}

.search-box > svg {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
  color: var(--text-secondary);
}

.search-box input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  color: var(--text-primary);
  font-size: 13px;
}

.search-box input::placeholder {
  color: var(--text-secondary);
}

.search-clear {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: var(--bg-tertiary);
  color: var(--text-secondary);
  cursor: pointer;
}

.search-clear svg {
  width: 12px;
  height: 12px;
}

.search-clear:focus-visible {
  outline: 2px solid var(--accent-color);
  outline-offset: 1px;
}

.installed-filter {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 38px;
  padding: 0 13px;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: var(--bg-surface);
  font-size: 13px;
  color: var(--text-secondary);
  cursor: pointer;
  transition: background-color 0.18s ease, border-color 0.18s ease, color 0.18s ease;
}

.installed-filter:hover {
  background: var(--bg-tertiary);
  color: var(--text-primary);
}

.installed-filter.active {
  border-color: var(--accent-color);
  background: color-mix(in srgb, var(--accent-color) 12%, transparent);
  color: var(--accent-color);
}

.installed-filter:focus-visible {
  outline: 2px solid var(--accent-color);
  outline-offset: 2px;
}

.installed-filter svg {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
}

.filter-count {
  font-variant-numeric: tabular-nums;
}

.action-btn-primary {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
  height: 38px;
  padding: 0 16px;
  border: 1px solid var(--accent-color);
  border-radius: 10px;
  background: var(--accent-color);
  color: #fff;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.08);
  transition: background-color 0.18s ease, box-shadow 0.18s ease;
}

.action-btn-primary:hover:not(:disabled) {
  background: color-mix(in srgb, var(--accent-color) 84%, #000);
}

.action-btn-primary:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.action-btn-primary:focus-visible {
  outline: 2px solid var(--accent-color);
  outline-offset: 2px;
}

.action-btn-primary svg {
  width: 14px;
  height: 14px;
}

.action-btn-outline {
  display: inline-flex;
  align-items: center;
  height: 38px;
  padding: 0 16px;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: var(--bg-surface);
  color: var(--text-primary);
  font-size: 13px;
  cursor: pointer;
  transition: border-color 0.18s ease, color 0.18s ease;
}

.action-btn-outline:hover {
  border-color: var(--accent-color);
  color: var(--accent-color);
}

/* ── 「添加技能」下拉菜单 ── */
.add-skill-menu {
  position: relative;
}

.add-skill-menu-list {
  position: absolute;
  right: 0;
  top: calc(100% + 6px);
  z-index: 8;
  min-width: 132px;
  display: flex;
  flex-direction: column;
  padding: 6px;
  border: 1px solid var(--border-color);
  border-radius: 12px;
  background: var(--bg-surface);
  box-shadow: 0 12px 28px rgba(15, 23, 42, 0.16);
}

.add-menu-item {
  padding: 9px 10px;
  border: none;
  border-radius: 8px;
  background: transparent;
  text-align: left;
  font-size: 13px;
  color: var(--text-primary);
  cursor: pointer;
  transition: background-color 0.15s ease;
}

.add-menu-item:hover {
  background: var(--bg-tertiary);
}

.add-menu-item:focus-visible {
  outline: 2px solid var(--accent-color);
  outline-offset: -2px;
}

.hidden-file-input {
  display: none;
}

/* ── 分类 chips ── */
.category-chips {
  display: flex;
  align-items: center;
  gap: 8px;
  row-gap: 8px;
  flex-wrap: wrap;
  padding: 14px 24px 0;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 28px;
  padding: 0 12px;
  border: 1px solid var(--border-color);
  border-radius: 999px;
  background: var(--bg-surface);
  font-size: 12.5px;
  color: var(--text-secondary);
  cursor: pointer;
  transition: background-color 0.18s ease, border-color 0.18s ease, color 0.18s ease;
}

.chip:hover {
  border-color: color-mix(in srgb, var(--accent-color) 35%, var(--border-color));
  color: var(--text-primary);
}

.chip.active {
  border-color: var(--accent-color);
  background: color-mix(in srgb, var(--accent-color) 12%, var(--bg-surface));
  color: var(--accent-color);
  font-weight: 500;
}

.chip:focus-visible {
  outline: 2px solid var(--accent-color);
  outline-offset: 2px;
}

.chip-count {
  font-size: 11.5px;
  opacity: 0.7;
  font-variant-numeric: tabular-nums;
}

.chip.active .chip-count {
  opacity: 1;
}

/* 管理分类入口：推到 chips 行末尾，虚线区分「筛选」与「操作」 */
.chip-manage {
  margin-left: auto;
  gap: 6px;
  border-style: dashed;
}

.chip-manage:hover {
  border-style: solid;
}

.chip-manage svg {
  width: 13px;
  height: 13px;
}

/* ── 类别分组 ── */
/* ── 卡片上的归类入口 ── */
.category-picker {
  position: relative;
  margin-left: auto;
}

.category-picker-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 9px;
  border: 1px solid var(--border-color);
  border-radius: 999px;
  background: var(--bg-surface);
  font-size: 11.5px;
  color: var(--text-secondary);
  cursor: pointer;
  transition: border-color 0.18s ease, color 0.18s ease;
}

.category-picker-btn:hover {
  border-color: var(--accent-color);
  color: var(--accent-color);
}

.category-picker-btn:focus-visible {
  outline: 2px solid var(--accent-color);
  outline-offset: 2px;
}

.category-picker-btn svg {
  width: 11px;
  height: 11px;
}

/* 归类菜单：绝对定位浮层，避免撑高卡片、连带同行卡片出现空白下拉区 */
.category-picker-menu {
  position: absolute;
  right: 0;
  top: calc(100% + 6px);
  z-index: 6;
  min-width: 140px;
  max-height: 210px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  padding: 6px;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: var(--bg-surface);
  box-shadow: 0 12px 28px rgba(15, 23, 42, 0.16);
}

.picker-item {
  padding: 7px 9px;
  border: none;
  border-radius: 7px;
  background: transparent;
  text-align: left;
  font-size: 12.5px;
  color: var(--text-secondary);
  cursor: pointer;
  transition: background-color 0.15s ease, color 0.15s ease;
}

.picker-item:hover {
  background: var(--bg-tertiary);
  color: var(--text-primary);
}

.picker-item.active {
  color: var(--accent-color);
}

.picker-item:focus-visible {
  outline: 2px solid var(--accent-color);
  outline-offset: -2px;
}

.picker-manage {
  color: var(--accent-color);
}

/* ── 内容区 ── */
.skill-center-content {
  min-width: 0;
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 18px 24px 28px;
  position: relative;
  scrollbar-width: thin;
}

.state-box {
  height: 100%;
  min-height: 260px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 32px 24px;
  color: var(--text-secondary);
  font-size: 13.5px;
  line-height: 1.6;
  text-align: center;
}

.state-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 56px;
  height: 56px;
  margin-bottom: 4px;
  border: 1px solid var(--border-color);
  border-radius: 16px;
  background: var(--bg-surface);
  color: color-mix(in srgb, var(--text-secondary) 75%, transparent);
}

.state-icon.is-error {
  border-color: color-mix(in srgb, #ef4444 30%, var(--border-color));
  background: color-mix(in srgb, #ef4444 8%, var(--bg-surface));
  color: #ef4444;
}

.state-icon svg {
  width: 26px;
  height: 26px;
}

.state-title {
  margin: 0;
  font-size: 14.5px;
  font-weight: 600;
  color: var(--text-primary);
}

.state-sub {
  margin: 0;
  max-width: 340px;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--text-secondary);
}

.skills-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(272px, 100%), 1fr));
  gap: 14px;
}

.skill-card {
  /* 定位上下文：菜单打开时本卡片要抬到兄弟卡片之上 */
  position: relative;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--border-color);
  border-radius: 14px;
  background: var(--bg-surface);
  /* 不裁切子元素：归类菜单是绝对定位浮层，需要溢出于卡片底部 */
  overflow: visible;
  transition: border-color 0.18s ease, box-shadow 0.18s ease, transform 0.18s ease;
}

/* hover 的 transform 会让卡片自成层叠上下文（z-index: auto），DOM 靠后的兄弟卡片
   会整块盖住卡片内的绝对定位菜单。菜单打开时显式提升层级即可（兄弟卡片按 0 处理，
   取值保持低位，避免与 Tab 悬浮提示、添加技能下拉等浮层抢层级）。 */
.skill-card.menu-open {
  z-index: 2;
}

.skill-card:hover {
  border-color: color-mix(in srgb, var(--accent-color) 40%, var(--border-color));
  box-shadow: 0 6px 20px rgba(15, 23, 42, 0.08);
  transform: translateY(-1px);
}

.skill-card-inner {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 14px 14px 12px;
}

.skill-card-open {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 0;
  border: none;
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.skill-card-open:focus-visible {
  outline: 2px solid var(--accent-color);
  outline-offset: 2px;
  border-radius: 8px;
}

.skill-card-body {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.skill-card-name-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.skill-card-name {
  font-size: 14px;
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.skill-tag {
  flex-shrink: 0;
  padding: 2px 7px;
  border-radius: 6px;
  background: color-mix(in srgb, var(--accent-color) 14%, transparent);
  color: var(--accent-color);
  font-size: 10.5px;
  font-weight: 500;
  line-height: 1.4;
}

.skill-tag.off {
  background: var(--bg-tertiary);
  color: var(--text-secondary);
}

.skill-card-desc {
  font-size: 12.5px;
  line-height: 1.55;
  color: var(--text-secondary);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  overflow-wrap: anywhere;
}

.add-icon-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  border: 1px solid var(--border-color);
  border-radius: 9px;
  background: var(--bg-surface);
  color: var(--text-secondary);
  cursor: pointer;
  transition: background-color 0.18s ease, border-color 0.18s ease, color 0.18s ease;
}

.add-icon-btn:hover:not(:disabled) {
  border-color: var(--accent-color);
  background: color-mix(in srgb, var(--accent-color) 12%, transparent);
  color: var(--accent-color);
}

/* 默认视图里「已添加」的状态标记：只一个对勾，尺寸与 + 按钮一致以保持卡片对齐 */
.skill-added-tag {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  border: 1px solid color-mix(in srgb, var(--accent-color) 35%, var(--border-color));
  border-radius: 9px;
  background: color-mix(in srgb, var(--accent-color) 12%, transparent);
  color: var(--accent-color);
}

/* 已添加但被停用：降为中性灰，避免看起来仍是启用状态 */
.skill-added-tag.off {
  border-color: var(--border-color);
  background: var(--bg-tertiary);
  color: var(--text-secondary);
}

.skill-added-tag svg {
  width: 14px;
  height: 14px;
}

.add-icon-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.add-icon-btn:focus-visible {
  outline: 2px solid var(--accent-color);
  outline-offset: 2px;
}

.add-icon-btn svg {
  width: 14px;
  height: 14px;
}

.skill-card-footer {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 9px 14px;
  border-top: 1px solid var(--border-color);
  background: color-mix(in srgb, var(--bg-tertiary) 55%, var(--bg-surface));
  /* 卡片不再裁切子元素，这里自己贴合卡片圆角 */
  border-bottom-left-radius: 13px;
  border-bottom-right-radius: 13px;
  font-size: 11.5px;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}

.skill-card-footer .dot {
  opacity: 0.5;
}

.btn-spinner-sm {
  width: 12px;
  height: 12px;
  border: 2px solid color-mix(in srgb, var(--accent-color) 35%, transparent);
  border-top-color: var(--accent-color);
  border-radius: 50%;
  animation: skill-spin 0.7s linear infinite;
}

@keyframes skill-spin {
  to {
    transform: rotate(360deg);
  }
}

.toast {
  position: absolute;
  left: 50%;
  bottom: 20px;
  transform: translateX(-50%);
  padding: 9px 16px;
  border-radius: 8px;
  font-size: 13px;
  color: #fff;
  background: #1e293b;
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.28);
  z-index: 20;
}

.toast.error {
  background: #b91c1c;
}

.toast-enter-active,
.toast-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
}

.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translate(-50%, 6px);
}

@media (prefers-reduced-motion: reduce) {
  .btn-spinner-sm {
    animation: none;
  }

  .toast-enter-active,
  .toast-leave-active {
    transition: none;
  }
}

@media (max-width: 48rem) {
  .skill-toolbar,
  .category-chips,
  .skill-center-content {
    padding-left: 16px;
    padding-right: 16px;
  }

  .search-box {
    max-width: none;
  }

  .action-btn-primary {
    margin-left: 0;
  }
}
/* ── 加载骨架（列表加载中占位，形似最终卡片） ── */
.skeleton-card {
  pointer-events: none;
}

.skeleton-block {
  display: block;
  border-radius: 6px;
  background: linear-gradient(
    90deg,
    color-mix(in srgb, var(--border-color) 40%, transparent) 25%,
    color-mix(in srgb, var(--border-color) 80%, transparent) 37%,
    color-mix(in srgb, var(--border-color) 40%, transparent) 63%
  );
  background-size: 400% 100%;
  animation: skeleton-shimmer 1.4s ease infinite;
}

.skeleton-icon {
  width: 40px;
  height: 40px;
  border-radius: 12px;
  flex-shrink: 0;
}

.skeleton-lines {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-top: 5px;
}

.skeleton-line {
  height: 11px;
}

.skeleton-line.w-40 {
  width: 40%;
}

.skeleton-line.w-60 {
  width: 60%;
}

.skeleton-line.w-90 {
  width: 90%;
}

@keyframes skeleton-shimmer {
  0% {
    background-position: 100% 50%;
  }

  100% {
    background-position: 0 50%;
  }
}

@media (prefers-reduced-motion: reduce) {
  .skeleton-block {
    animation: none;
  }
}
</style>
