<template>
  <div
    class="skill-submenu"
    :style="panelStyle"
    @click.stop
    @mouseenter="$emit('hover-in')"
    @mouseleave="$emit('hover-out')"
  >
                    <div class="skill-sub-search">
                      <svg class="skill-sub-search-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <circle cx="11" cy="11" r="8"></circle>
                        <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
                      </svg>
                      <input
                        :value="search"
                    @input="$emit('update:search', $event.target.value)"
                        type="text"
                        placeholder="搜索技能"
                        class="skill-sub-search-input"
                      />
                    </div>
                    <div class="skill-sub-list">
                      <div v-if="loading" class="skill-sub-hint">加载中…</div>
                      <div v-else-if="error" class="skill-sub-hint error">{{ error }}</div>
                      <div v-else-if="items.length === 0" class="skill-sub-hint">
                        {{ search ? '未找到匹配的技能' : '暂无技能，可从本地添加或到技能中心安装' }}
                      </div>
                      <button
                        v-else
                        v-for="item in items"
                        :key="item.dir_name"
                        type="button"
                        class="skill-sub-item"
                        :class="{ active: selectedDirs.includes(item.dir_name), disabled: !item.enabled }"
                        :title="item.enabled ? `选择「${item.name}」` : '该技能已停用'"
                        @click="$emit('select', item)"
                      >
                        <span class="skill-sub-icon" :style="{ background: item.iconBg, color: item.iconFg }">
                          <span v-if="item.icon" class="skill-sub-icon-emoji">{{ item.icon }}</span>
                          <template v-else>{{ item.initial }}</template>
                        </span>
                        <span class="skill-sub-body">
                          <span class="skill-sub-name-row">
                            <span class="skill-sub-name">{{ item.name }}</span>
                            <span v-if="!item.enabled" class="skill-sub-off">已停用</span>
                          </span>
                          <span class="skill-sub-desc">{{ item.desc }}</span>
                        </span>
                        <svg v-if="selectedDirs.includes(item.dir_name)" class="skill-sub-check" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                          <polyline points="20 6 9 17 4 12"></polyline>
                        </svg>
                      </button>
                    </div>
                    <div class="skill-sub-footer">
                      <button type="button" class="skill-sub-footer-btn" @click="$emit('import')">
                        <svg class="skill-sub-footer-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                          <polyline points="17 8 12 3 7 8"></polyline>
                          <line x1="12" y1="3" x2="12" y2="15"></line>
                        </svg>
                        <span>从本地添加技能</span>
                      </button>
                      <button type="button" class="skill-sub-footer-btn" @click="$emit('manage')">
                        <svg class="skill-sub-footer-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                          <circle cx="12" cy="12" r="3"></circle>
                          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
                        </svg>
                        <span>管理技能</span>
                      </button>
                    </div>
  </div>
</template>

<script setup>
/**
 * 「+」菜单 → 技能子面板（2026-10-20 从 ChatInput.vue 抽出）。
 *
 * 纯展示 + 事件上抛：搜索词、列表数据、已选目录都由父组件持有，
 * 选中/导入/管理分别 emit，父组件仍负责技能令牌的插入与移除。
 */
defineProps({
  items: { type: Array, default: () => [] },
  selectedDirs: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  search: { type: String, default: '' },
  panelStyle: { type: Object, default: () => ({}) },
})

defineEmits(['update:search', 'select', 'import', 'manage', 'hover-in', 'hover-out'])
</script>

<style scoped src="./skillSubmenu.css"></style>
