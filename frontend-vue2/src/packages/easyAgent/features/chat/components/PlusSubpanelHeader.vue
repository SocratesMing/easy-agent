<template>
  <div class="plus-subpanel-header">
    <div class="plus-subpanel-head">
      <span class="plus-subpanel-title">{{ title }}</span>
      <span class="plus-menu-count">{{ count }}</span>
    </div>
    <!-- 搜索框常驻（专家 / 技能 / 连接器三个面板同一形态）：
         占满宽度、灰底胶囊，输入即过滤；有关键字时右侧出现清空按钮。 -->
    <div v-if="searchable" class="plus-sub-search">
      <svg class="plus-sub-search-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="11" cy="11" r="8"></circle>
        <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
      </svg>
      <input
        :value="keyword"
        type="text"
        :placeholder="placeholder"
        :aria-label="placeholder"
        class="plus-sub-search-input"
        spellcheck="false"
        autocomplete="off"
        @input="$emit('update:keyword', $event.target.value)"
        @keydown.esc.stop.prevent="$emit('update:keyword', '')"
      />
      <button
        v-if="keyword"
        type="button"
        class="plus-sub-search-clear"
        aria-label="清空搜索"
        @click="$emit('update:keyword', '')"
      >
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <line x1="18" y1="6" x2="6" y2="18" />
          <line x1="6" y1="6" x2="18" y2="18" />
        </svg>
      </button>
    </div>
  </div>
</template>

<script>
/**
 * 「+」子面板头部：标题、计数、常驻搜索框。
 *
 * 2026-10-23 起搜索框对专家 / 技能 / 连接器三个面板统一开放（原先只有连接器
 * 有一个「点图标展开」的搜索，另外两个没有）。列表主体由 ExpertSubpanelList /
 * PlusList 渲染，这里只管头部与搜索词上抛。
 */
export default {
  name: 'PlusSubpanelHeader',
  props: {
    title: { type: String, default: '' },
    count: { type: [String, Number], default: '' },
    keyword: { type: String, default: '' },
    placeholder: { type: String, default: '搜索' },
    /** 数据为空时不显示搜索框（免得对着空列表让人以为是搜不到） */
    searchable: { type: Boolean, default: true },
  },
  emits: ['update:keyword'],
}
</script>

<style scoped>
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

.plus-sub-search {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 4px 8px;
  padding: 7px 10px;
  border-radius: 10px;
  background: var(--bg-tertiary, #f1f5f9);
  border: 1px solid transparent;
  transition: border-color 0.15s ease;
}

.plus-sub-search:focus-within {
  border-color: color-mix(in srgb, var(--accent-color) 45%, transparent);
}

.plus-sub-search-icon {
  width: 15px;
  height: 15px;
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
  font-size: 13px;
  font-family: inherit;
  line-height: 1.3;
}

.plus-sub-search-input::placeholder {
  color: var(--text-secondary);
}

.plus-sub-search-clear {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 16px;
  height: 16px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
}

.plus-sub-search-clear:hover {
  color: var(--text-primary);
}

.plus-sub-search-clear svg {
  width: 12px;
  height: 12px;
}
</style>
