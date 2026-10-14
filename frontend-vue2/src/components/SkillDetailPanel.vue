<!--
  技能详情（点卡片进入）：
  - 头部：大图标 + 名称 + 分类/位置/状态 + 启用开关
  - 描述（超过 3 行可展开）
  - 操作：去试试（关闭面板并把提示词预填进输入框）、移除技能 / 添加到我的技能
  - 概述：SKILL.md 正文原文
  - 更多：版本 / 位置 / 文件数 / 大小 / 更新时间（本地真实数据）
-->
<template>
  <div class="skill-detail">
    <div class="detail-topbar">
      <button class="back-btn" @click="$emit('back')">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <line x1="19" y1="12" x2="5" y2="12" />
          <polyline points="12 19 5 12 12 5" />
        </svg>
        返回技能列表
      </button>
      <div class="topbar-right">
        <span v-if="detail && detail.version" class="version-chip">v{{ detail.version }}</span>
        <ToggleSwitch
          v-if="detail && detail.added"
          :value="!!detail.enabled"
          :loading="toggling"
          :aria-label="detail.enabled ? '停用技能' : '启用技能'"
          @input="handleToggle"
        />
      </div>
    </div>

    <div v-if="loading" class="state-box">
      <div class="spinner"></div>
      <span>加载中…</span>
    </div>

    <div v-else-if="error" class="state-box">
      <p>{{ error }}</p>
      <button class="action-btn-outline" @click="load">重试</button>
    </div>

    <div v-else-if="detail" class="detail-scroll">
      <div class="detail-hero">
        <SkillIcon :icon="detail.icon" :category="category" size="lg" />
        <div class="hero-text">
          <h3 class="detail-name">{{ detail.name }}</h3>
          <p class="detail-sub">
            <span>{{ categoryLabel }}</span>
            <span class="sep">·</span>
            <span>{{ detail.scope === 'user' ? '我的技能' : '公共技能库' }}</span>
            <template v-if="detail.added">
              <span class="sep">·</span>
              <span class="state-tag" :class="detail.enabled ? 'on' : 'off'">
                {{ detail.enabled ? '已启用' : '已停用' }}
              </span>
            </template>
          </p>
        </div>
      </div>

      <div class="detail-desc">
        <p :class="{ clamped: !descExpanded }">{{ detail.description || '这个技能还没有写描述' }}</p>
        <button v-if="descLong" class="link-btn" @click="descExpanded = !descExpanded">
          {{ descExpanded ? '收起' : '展开' }}
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
            <polyline v-if="descExpanded" points="18 15 12 9 6 15" />
            <polyline v-else points="6 9 12 15 18 9" />
          </svg>
        </button>
      </div>

      <div class="detail-actions">
        <button class="action-btn-primary" @click="handleTry">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
            <polygon points="5 3 19 12 5 21 5 3" />
          </svg>
          去试试
        </button>
        <button
          v-if="detail.added"
          class="action-btn-danger"
          :disabled="busy"
          @click="handleRemove"
        >
          {{ confirmingRemove ? '确认移除？' : '移除技能' }}
        </button>
        <button v-else class="action-btn-outline" :disabled="busy" @click="handleInstall">
          添加到我的技能
        </button>
      </div>

      <section class="detail-block">
        <h4 class="block-title">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <polyline points="14 2 14 8 20 8" />
          </svg>
          概述
          <span class="block-hint">{{ detail.doc_file || 'SKILL.md' }}</span>
        </h4>
        <pre class="skill-doc">{{ detail.content || '（这个技能没有可展示的正文）' }}</pre>
      </section>

      <section class="detail-block">
        <h4 class="block-title">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
            <line x1="8" y1="6" x2="21" y2="6" />
            <line x1="8" y1="12" x2="21" y2="12" />
            <line x1="8" y1="18" x2="21" y2="18" />
            <line x1="3" y1="6" x2="3.01" y2="6" />
            <line x1="3" y1="12" x2="3.01" y2="12" />
            <line x1="3" y1="18" x2="3.01" y2="18" />
          </svg>
          更多
        </h4>
        <dl class="more-list">
          <div class="more-row"><dt>版本</dt><dd>{{ detail.version || '未标注' }}</dd></div>
          <div class="more-row">
            <dt>位置</dt>
            <dd>{{ detail.scope === 'user' ? `我的技能（${detail.enabled ? '启用中' : '已停用'}）` : '公共技能库' }}</dd>
          </div>
          <div class="more-row"><dt>文件数</dt><dd>{{ stats.file_count }} 个</dd></div>
          <div class="more-row"><dt>大小</dt><dd>{{ formatSize(stats.size_bytes) }}</dd></div>
          <div class="more-row"><dt>更新时间</dt><dd>{{ formatTime(stats.updated_at) }}</dd></div>
        </dl>
      </section>
    </div>

    <Transition name="toast">
      <div v-if="toast.show" class="toast" :class="toast.type" role="status" aria-live="polite">
        {{ toast.message }}
      </div>
    </Transition>
  </div>
</template>

<script>
import { getSkillDetail, enableSkill, disableSkill, addSkillToUser, removeSkillFromUser } from '../api/skills.js'
import { DEFAULT_CATEGORY, iconCategoryFor } from '../utils/skillCategories.js'
import SkillIcon from './SkillIcon.vue'
import ToggleSwitch from './ToggleSwitch.vue'

export default {
  components: { SkillIcon, ToggleSwitch },
  props: {
    dirName: { type: String, required: true },
    scope: { type: String, default: 'public' },
  },
  emits: ['back', 'close', 'refresh'],
  data() {
    return {
      loading: false,
      error: '',
      detail: null,
      toggling: false,
      busy: false,
      descExpanded: false,
      confirmingRemove: false,
      toast: { show: false, message: '', type: 'success' },
    }
  },
  computed: {
    category() {
      return iconCategoryFor(this.detail && this.detail.category_id)
    },
    categoryLabel() {
      return (this.detail && this.detail.category_name) || DEFAULT_CATEGORY.name
    },
    stats() {
      return (this.detail && this.detail.stats) || { file_count: 0, size_bytes: 0, updated_at: null }
    },
    // 描述超过 3 行（约 120 字）才给「展开」
    descLong() {
      return ((this.detail && this.detail.description) || '').length > 120
    },
  },
  watch: {
    dirName() { this.load() },
    scope() { this.load() },
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

    // 相对时间（Intl，避免硬编码格式）；超过 30 天显示日期
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
    // 超过 30 天（或未来过远）直接给日期，避免出现「1,852小时前」这类难读数值
    return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium' }).format(date)
    },

    async load() {
      this.loading = true
      this.error = ''
      this.confirmingRemove = false
      try {
        this.detail = await getSkillDetail(this.dirName, this.scope)
      } catch (e) {
        this.error = e.message || '获取技能详情失败'
        this.detail = null
      } finally {
        this.loading = false
      }
    },

    async handleToggle(next) {
      this.toggling = true
      try {
        await (next ? enableSkill(this.dirName) : disableSkill(this.dirName))
        await this.load()
        this.$emit('refresh')
        this.showToast(next ? '已启用，下次对话开始生效' : '已停用，Agent 不再加载该技能')
      } catch (e) {
        this.showToast(e.message || '操作失败', 'error')
      } finally {
        this.toggling = false
      }
    },

    async handleInstall() {
      this.busy = true
      try {
        await addSkillToUser(this.dirName)
        await this.load()
        this.$emit('refresh')
        this.showToast('已添加到我的技能')
      } catch (e) {
        this.showToast(e.message || '添加失败', 'error')
      } finally {
        this.busy = false
      }
    },

    async handleRemove() {
      if (!this.confirmingRemove) {
        this.confirmingRemove = true
        setTimeout(() => { this.confirmingRemove = false }, 3000)
        return
      }
      this.busy = true
      try {
        await removeSkillFromUser(this.dirName)
        this.$emit('refresh')
        this.showToast('已移除')
        this.$emit('back')
      } catch (e) {
        this.showToast(e.message || '移除失败', 'error')
      } finally {
        this.busy = false
        this.confirmingRemove = false
      }
    },

    // 去试试：把提示词写进输入框草稿（ChatInput 挂载时会读取），并关闭面板回到对话
    handleTry() {
      try {
        const name = (this.detail && this.detail.name) || this.dirName
        sessionStorage.setItem('easy_agent_input_draft', `用「${name}」技能帮我：`)
      } catch (_) { /* sessionStorage 不可用时忽略 */ }
      this.$emit('close')
    },
  },
}
</script>

<style scoped>
.skill-detail {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  position: relative;
}

.detail-topbar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 24px;
  border-bottom: 1px solid var(--border-color);
}

.back-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 34px;
  padding: 0 12px;
  border: none;
  border-radius: 9px;
  background: transparent;
  font-size: 13px;
  color: var(--text-secondary);
  cursor: pointer;
  transition: background-color 0.18s ease, color 0.18s ease;
}

.back-btn:hover {
  background: var(--bg-tertiary);
  color: var(--text-primary);
}

.back-btn:focus-visible {
  outline: 2px solid var(--accent-color);
  outline-offset: 2px;
}

.back-btn svg {
  width: 16px;
  height: 16px;
}

.topbar-right {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 10px;
}

.version-chip {
  padding: 3px 8px;
  border-radius: 6px;
  background: var(--bg-tertiary);
  color: var(--text-secondary);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.state-box {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: var(--text-secondary);
  font-size: 14px;
}

.detail-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 20px 24px 32px;
}

.detail-hero {
  display: flex;
  align-items: center;
  gap: 14px;
}

.hero-text {
  min-width: 0;
}

.detail-name {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
  overflow-wrap: anywhere;
}

.detail-sub {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--text-secondary);
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.sep {
  opacity: 0.6;
}

.state-tag {
  padding: 2px 7px;
  border-radius: 6px;
  font-size: 12px;
}

.state-tag.on {
  background: #dcfce7;
  color: #15803d;
}

.state-tag.off {
  background: var(--bg-tertiary);
  color: var(--text-secondary);
}

.detail-desc {
  margin-top: 16px;
}

.detail-desc p {
  margin: 0;
  font-size: 14px;
  line-height: 1.7;
  color: var(--text-primary);
  overflow-wrap: anywhere;
}

.detail-desc p.clamped {
  display: -webkit-box;
  -webkit-line-clamp: 3;
  line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.link-btn {
  margin-top: 4px;
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 2px 4px;
  border: none;
  background: transparent;
  color: var(--accent-color);
  font-size: 13px;
  cursor: pointer;
}

.link-btn svg {
  width: 14px;
  height: 14px;
}

.link-btn:focus-visible {
  outline: 2px solid var(--accent-color);
  outline-offset: 2px;
  border-radius: 4px;
}

.detail-actions {
  margin-top: 18px;
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.detail-block {
  margin-top: 26px;
}

.block-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 10px;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.block-title svg {
  width: 16px;
  height: 16px;
  color: var(--text-secondary);
}

.block-hint {
  margin-left: auto;
  font-size: 12px;
  font-weight: 400;
  color: var(--text-secondary);
}

.skill-doc {
  margin: 0;
  padding: 16px;
  max-height: 46vh;
  overflow: auto;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: var(--bg-tertiary);
  color: var(--text-primary);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12.5px;
  line-height: 1.7;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.more-list {
  margin: 0;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  overflow: hidden;
}

.more-row {
  display: flex;
  gap: 12px;
  padding: 10px 14px;
  font-size: 13px;
  border-top: 1px solid var(--border-color);
}

.more-row:first-child {
  border-top: none;
}

.more-row dt {
  flex: 0 0 76px;
  color: var(--text-secondary);
}

.more-row dd {
  margin: 0;
  min-width: 0;
  color: var(--text-primary);
  overflow-wrap: anywhere;
}

.action-btn-primary,
.action-btn-outline,
.action-btn-danger {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 38px;
  padding: 0 16px;
  border-radius: 10px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: background-color 0.18s ease, border-color 0.18s ease, color 0.18s ease;
}

.action-btn-primary {
  border: 1px solid var(--accent-color);
  background: var(--accent-color);
  color: #fff;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.08);
}

.action-btn-primary:hover {
  background: color-mix(in srgb, var(--accent-color) 84%, #000);
}

.action-btn-outline {
  border: 1px solid var(--border-color);
  background: var(--bg-surface);
  color: var(--text-primary);
}

.action-btn-outline:hover {
  border-color: var(--accent-color);
  color: var(--accent-color);
}

.action-btn-danger {
  border: 1px solid #fecaca;
  background: transparent;
  color: #dc2626;
}

.action-btn-danger:hover {
  background: #fef2f2;
}

.action-btn-primary:disabled,
.action-btn-outline:disabled,
.action-btn-danger:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.action-btn-primary:focus-visible,
.action-btn-outline:focus-visible,
.action-btn-danger:focus-visible {
  outline: 2px solid var(--accent-color);
  outline-offset: 2px;
}

.action-btn-primary svg {
  width: 14px;
  height: 14px;
}

.spinner {
  width: 22px;
  height: 22px;
  border: 2px solid var(--border-color);
  border-top-color: var(--accent-color);
  border-radius: 50%;
  animation: detail-spin 0.8s linear infinite;
}

@keyframes detail-spin {
  to {
    transform: rotate(360deg);
  }
}

.toast {
  position: absolute;
  left: 50%;
  bottom: 24px;
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
  .spinner {
    animation: none;
  }

  .toast-enter-active,
  .toast-leave-active {
    transition: none;
  }
}

@media (max-width: 48rem) {
  .detail-topbar,
  .detail-scroll {
    padding-left: 16px;
    padding-right: 16px;
  }

  .more-row dt {
    flex-basis: 64px;
  }
}
</style>
