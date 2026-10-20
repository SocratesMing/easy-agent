<template>
  <div class="plus-subpanel-header">
  <div class="plus-subpanel-head">
    <span class="plus-subpanel-title">{{ title }}</span>
    <span class="plus-menu-count">{{ count }}</span>
    <!-- 连接器面板：搜索按钮（展开后按名称/描述过滤列表） -->
    <button
      v-if="isConnector"
      type="button"
      class="plus-subpanel-search-btn"
      :class="{ active: showSearch }"
      :aria-label="showSearch ? '收起搜索' : '搜索连接器'"
      :aria-expanded="showSearch ? 'true' : 'false'"
      @click="$emit('toggle-search')"
    >
      <svg v-if="!showSearch" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="11" cy="11" r="8"></circle>
        <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
      </svg>
      <svg v-else xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
        <line x1="18" y1="6" x2="6" y2="18" />
        <line x1="6" y1="6" x2="18" y2="18" />
      </svg>
    </button>
  </div>
  <div v-if="isConnector && showSearch" class="plus-sub-search">
    <svg class="plus-sub-search-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="11" cy="11" r="8"></circle>
      <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
    </svg>
    <input
      :value="keyword"
      type="text"
      placeholder="搜索连接器"
      class="plus-sub-search-input"
      @input="$emit('update:keyword', $event.target.value)"
    />
  </div>
  </div>
</template>

<script>
/**
 * 「+」子面板头部（2026-10-20 从 ChatInput.vue 抽出）：标题、计数、连接器搜索开关。
 * 列表主体由 ExpertSubpanelList / PlusList 渲染，这里只管头部。
 */
export default {
  name: 'PlusSubpanelHeader',
  props: {
    title: { type: String, default: '' },
    count: { type: [String, Number], default: '' },
    isConnector: { type: Boolean, default: false },
    showSearch: { type: Boolean, default: false },
    keyword: { type: String, default: '' },
  },
  emits: ['toggle-search', 'update:keyword'],
}
</script>

<style scoped>
.plus-subpanel-search-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  margin-left: auto;
  padding: 0;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
}

.plus-subpanel-search-btn.active {
  background: var(--bg-tertiary);
  color: var(--text-primary);
}

.plus-subpanel-search-btn svg {
  width: 14px;
  height: 14px;
}

.plus-sub-search {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 8px 6px;
  padding: 5px 8px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: var(--bg-primary, transparent);
}

.plus-sub-search-icon {
  width: 13px;
  height: 13px;
  flex-shrink: 0;
  color: var(--text-secondary);
}

.plus-sub-search-input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  color: var(--text-primary);
  font-size: 12.5px;
  line-height: 1.3;
}

.plus-subpanel-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 2px 4px 8px;
}

.plus-subpanel-title {
  flex: 1;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary, #1e293b);
}
</style>
