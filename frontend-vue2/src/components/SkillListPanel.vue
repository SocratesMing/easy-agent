<!--
  技能列表（技能 Tab 主视图）：
  - 工具条：搜索 / 已安装(n) 筛选 / 添加技能（zip 导入）
  - 分类 chips：全部 + 5 类（沿用 skillCategories 映射）
  - 卡片：图标 / 名称 / 状态标签 / 启用开关（未安装则 +）/ 单行描述 / 底部真实指标
  - 点卡片名称 → 详情页（SkillDetailPanel）
-->
<template>
  <div class="skill-panel">
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
          @click="installedOnly = !installedOnly"
        >
          已安装<span class="filter-count">{{ installedCount }}</span>
        </button>

        <button class="action-btn-primary" :disabled="importing" @click="triggerImport">
          <div v-if="importing" class="btn-spinner-sm"></div>
          <svg v-else xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
            <line x1="12" y1="5" x2="12" y2="19" />
            <line x1="5" y1="12" x2="19" y2="12" />
          </svg>
          {{ importing ? '导入中…' : '添加技能' }}
        </button>
        <input ref="fileInputRef" type="file" accept=".zip" class="hidden-file-input" @change="handleImportFile" />
      </div>

      <div class="category-chips" role="group" aria-label="按分类筛选">
        <button
          v-for="item in categories"
          :key="item.key"
          class="chip"
          :class="{ active: category === item.key }"
          :aria-pressed="category === item.key ? 'true' : 'false'"
          @click="category = item.key"
        >
          {{ item.label }}
          <span v-if="categoryCounts[item.key]" class="chip-count">{{ categoryCounts[item.key] }}</span>
        </button>
      </div>

      <div class="skill-center-content">
        <div v-if="loading" class="state-box">
          <div class="spinner"></div>
          <span>加载中…</span>
        </div>

        <div v-else-if="error" class="state-box">
          <p>{{ error }}</p>
          <button class="action-btn-outline" @click="load">重试</button>
        </div>

        <div v-else-if="filteredCards.length === 0" class="state-box">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
            <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
          </svg>
          <p>{{ emptyText }}</p>
        </div>

        <div v-else class="skills-grid">
          <div v-for="card in filteredCards" :key="card.key" class="skill-card">
            <div class="skill-card-inner">
              <button class="skill-card-open" @click="openDetail(card)">
                <SkillIcon :icon="card.icon" :category="card.category" />
                <span class="skill-card-body">
                  <span class="skill-card-name-row">
                    <span class="skill-card-name">{{ card.name }}</span>
                    <span v-if="card.source === 'local'" class="skill-tag">导入</span>
                    <span v-else-if="card.installed && !card.enabled" class="skill-tag off">已停用</span>
                  </span>
                  <span class="skill-card-desc">{{ card.description || '暂无描述' }}</span>
                </span>
              </button>

              <ToggleSwitch
                v-if="card.installed"
                :model-value="card.enabled"
                :loading="togglingKey === card.key"
                :aria-label="`${card.enabled ? '停用' : '启用'}技能 ${card.name}`"
                @change="handleToggle(card, $event)"
              />
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
            </div>
          </div>
        </div>
      </div>
    </template>

    <Transition name="toast">
      <div v-if="toast.show" class="toast" :class="toast.type" role="status" aria-live="polite">
        {{ toast.message }}
      </div>
    </Transition>
  </div>
</template>

<script>
import {
  getPublicSkills,
  getUserSkills,
  addSkillToUser,
  enableSkill,
  disableSkill,
  importSkill,
} from '../api/skills.js'
import { CATEGORIES, getSkillCategory } from '../utils/skillCategories.js'
import SkillIcon from './SkillIcon.vue'
import SkillDetailPanel from './SkillDetailPanel.vue'
import ToggleSwitch from './ToggleSwitch.vue'

export default {
  components: { SkillIcon, SkillDetailPanel, ToggleSwitch },
  emits: ['close'],
  data() {
    return {
      categories: CATEGORIES,
      loading: false,
      error: '',
      publicSkills: [],
      userSkills: [],
      keyword: '',
      category: 'all',
      installedOnly: false,
      addingKey: null,
      togglingKey: null,
      importing: false,
      detailTarget: null,
      toast: { show: false, message: '', type: 'success' },
    }
  },
  computed: {
    /**
     * 卡片数据 = 公共技能目录（带安装/启用状态）+ 用户目录里不在目录中的技能（本地导入）。
     * 同一个 dir_name 只会出现一次，避免市场技能与其用户副本重复展示。
     */
    cards() {
      const list = []
      const catalogNames = new Set(this.publicSkills.map((s) => s.dir_name))

      for (const skill of this.publicSkills) {
        list.push({
          key: `public:${skill.dir_name}`,
          dirName: skill.dir_name,
          scope: skill.added ? 'user' : 'public',
          source: 'catalog',
          name: skill.name,
          description: skill.description,
          icon: skill.icon,
          version: skill.version,
          category: getSkillCategory(skill),
          installed: !!skill.added,
          enabled: !!skill.enabled,
          stats: skill.stats || { file_count: 0, size_bytes: 0, updated_at: null },
        })
      }

      for (const skill of this.userSkills) {
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
          category: getSkillCategory(skill),
          installed: true,
          enabled: !!skill.enabled,
          stats: skill.stats || { file_count: 0, size_bytes: 0, updated_at: null },
        })
      }

      // 已安装的排前面，其次按分类、名称
      return list.sort((a, b) => (
        Number(b.installed) - Number(a.installed) ||
        a.category.localeCompare(b.category) ||
        a.name.localeCompare(b.name, 'zh-Hans-CN')
      ))
    },

    installedCount() {
      return this.cards.filter((c) => c.installed).length
    },

    categoryCounts() {
      const counts = { all: this.cards.length }
      for (const card of this.cards) {
        counts[card.category] = (counts[card.category] || 0) + 1
      }
      return counts
    },

    filteredCards() {
      const kw = this.keyword.trim().toLowerCase()
      return this.cards.filter((card) => {
        if (this.installedOnly && !card.installed) return false
        if (this.category !== 'all' && card.category !== this.category) return false
        if (!kw) return true
        return (
          card.name.toLowerCase().includes(kw) ||
          (card.description || '').toLowerCase().includes(kw) ||
          card.dirName.toLowerCase().includes(kw)
        )
      })
    },

    emptyText() {
      if (this.keyword.trim()) return `没有匹配「${this.keyword.trim()}」的技能`
      if (this.installedOnly) return '还没有已安装的技能，点卡片上的 + 添加，或「添加技能」导入 zip'
      return '暂无技能'
    },
  },
  mounted() {
    this.load()
  },
  methods: {
    showToast(message, type = 'success') {
      this.toast = { show: true, message, type }
      setTimeout(() => { this.toast = { ...this.toast, show: false } }, 2600)
    },

    formatSize(bytes) {
      const value = Number(bytes || 0)
      if (value < 1024) return `${value} B`
      if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
      return `${(value / 1024 / 1024).toFixed(1)} MB`
    },

    formatTime(iso) {
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
    },

    async fetchSkills() {
      const [publicData, userData] = await Promise.all([getPublicSkills(), getUserSkills()])
      this.publicSkills = publicData.skills || []
      this.userSkills = userData.skills || []
    },

    async load() {
      this.loading = true
      this.error = ''
      try {
        await this.fetchSkills()
      } catch (e) {
        this.error = e.message || '获取技能列表失败'
      } finally {
        this.loading = false
      }
    },

    // 详情页里做过改动：静默刷新列表，不闪 loading
    async refresh() {
      try {
        await this.fetchSkills()
      } catch (_) { /* 静默失败，保留旧数据 */ }
    },

    openDetail(card) {
      this.detailTarget = { dirName: card.dirName, scope: card.scope }
    },

    async handleAdd(card) {
      this.addingKey = card.key
      try {
        await addSkillToUser(card.dirName)
        await this.refresh()
        this.showToast(`已添加「${card.name}」`)
      } catch (e) {
        this.showToast(e.message || '添加失败', 'error')
      } finally {
        this.addingKey = null
      }
    },

    async handleToggle(card, next) {
      this.togglingKey = card.key
      try {
        await (next ? enableSkill(card.dirName) : disableSkill(card.dirName))
        await this.refresh()
        this.showToast(next ? `已启用「${card.name}」，下次对话生效` : `已停用「${card.name}」，Agent 不再加载`)
      } catch (e) {
        this.showToast(e.message || '操作失败', 'error')
      } finally {
        this.togglingKey = null
      }
    },

    triggerImport() {
      const input = this.$refs.fileInputRef
      if (input) input.click()
    },

    async handleImportFile(event) {
      const file = event.target.files && event.target.files[0]
      if (!file) return
      this.importing = true
      try {
        const result = await importSkill(file)
        await this.refresh()
        this.showToast(result.message || '导入成功')
      } catch (e) {
        this.showToast(e.message || '导入失败', 'error')
      } finally {
        this.importing = false
        if (this.$refs.fileInputRef) this.$refs.fileInputRef.value = ''
      }
    },
  },
}
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
  flex-wrap: wrap;
  padding: 16px 24px 0;
}

.search-box {
  flex: 1;
  min-width: 160px;
  max-width: 320px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 12px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: var(--bg-secondary);
  transition: border-color 0.18s ease, box-shadow 0.18s ease;
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
  padding: 7px 12px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: transparent;
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

.filter-count {
  font-variant-numeric: tabular-nums;
}

.action-btn-primary {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
  padding: 8px 14px;
  border: 1px solid var(--accent-color);
  border-radius: 8px;
  background: var(--accent-color);
  color: #fff;
  font-size: 13px;
  cursor: pointer;
  transition: background-color 0.18s ease;
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
  padding: 8px 14px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: transparent;
  color: var(--text-primary);
  font-size: 13px;
  cursor: pointer;
}

.action-btn-outline:hover {
  border-color: var(--accent-color);
  color: var(--accent-color);
}

.hidden-file-input {
  display: none;
}

/* ── 分类 chips ── */
.category-chips {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 12px 24px 0;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 5px 12px;
  border: 1px solid var(--border-color);
  border-radius: 999px;
  background: transparent;
  font-size: 12.5px;
  color: var(--text-secondary);
  cursor: pointer;
  transition: background-color 0.18s ease, border-color 0.18s ease, color 0.18s ease;
}

.chip:hover {
  background: var(--bg-tertiary);
  color: var(--text-primary);
}

.chip.active {
  border-color: var(--accent-color);
  background: color-mix(in srgb, var(--accent-color) 12%, transparent);
  color: var(--accent-color);
}

.chip:focus-visible {
  outline: 2px solid var(--accent-color);
  outline-offset: 2px;
}

.chip-count {
  font-size: 11.5px;
  opacity: 0.8;
  font-variant-numeric: tabular-nums;
}

/* ── 内容区 ── */
.skill-center-content {
  min-width: 0;
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 16px 24px 24px;
  position: relative;
}

.state-box {
  height: 100%;
  min-height: 240px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: var(--text-secondary);
  font-size: 14px;
  text-align: center;
}

.state-box svg {
  width: 44px;
  height: 44px;
}

.skills-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(280px, 100%), 1fr));
  gap: 12px;
}

.skill-card {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: var(--bg-secondary);
  overflow: hidden;
  transition: border-color 0.18s ease, box-shadow 0.18s ease;
}

.skill-card:hover {
  border-color: color-mix(in srgb, var(--accent-color) 45%, var(--border-color));
  box-shadow: 0 4px 14px rgba(15, 23, 42, 0.07);
}

.skill-card-inner {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 12px 12px 10px;
}

.skill-card-open {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: flex-start;
  gap: 10px;
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
  gap: 3px;
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
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.skill-tag {
  flex-shrink: 0;
  padding: 1px 6px;
  border-radius: 5px;
  background: color-mix(in srgb, var(--accent-color) 14%, transparent);
  color: var(--accent-color);
  font-size: 11px;
}

.skill-tag.off {
  background: var(--bg-tertiary);
  color: var(--text-secondary);
}

.skill-card-desc {
  font-size: 12.5px;
  line-height: 1.5;
  color: var(--text-secondary);
  display: -webkit-box;
  -webkit-line-clamp: 1;
  line-clamp: 1;
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
  border-radius: 8px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  transition: background-color 0.18s ease, border-color 0.18s ease, color 0.18s ease;
}

.add-icon-btn:hover:not(:disabled) {
  border-color: var(--accent-color);
  background: color-mix(in srgb, var(--accent-color) 12%, transparent);
  color: var(--accent-color);
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
  padding: 8px 12px;
  border-top: 1px solid var(--border-color);
  background: var(--bg-tertiary);
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

.spinner {
  width: 22px;
  height: 22px;
  border: 2px solid var(--border-color);
  border-top-color: var(--accent-color);
  border-radius: 50%;
  animation: skill-spin 0.8s linear infinite;
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
  .spinner,
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
</style>
