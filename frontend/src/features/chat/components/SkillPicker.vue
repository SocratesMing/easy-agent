<template>
  <!-- 纯内容组件：外框（宽度/位置/高度/边框/热桥）由 ChatInput 的原生
       .skill-submenu.plus-subpanel 负责，与专家 / 连接器面板同一条链路。
       这样 syncSubpanelBox 量到的就是真实面板高度，搜索后能正常收缩。 -->
  <div class="skill-sub-inner">
                    <div class="plus-subpanel-head">
                      <span class="plus-subpanel-title">{{ title }}</span>
                      <span class="plus-menu-count">{{ count }}</span>
                    </div>
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
                        aria-label="搜索技能"
                        spellcheck="false"
                        autocomplete="off"
                        class="skill-sub-search-input"
                        @keydown.esc.stop.prevent="$emit('update:search', '')"
                      />
                      <button
                        v-if="search"
                        type="button"
                        class="skill-sub-search-clear"
                        aria-label="清空搜索"
                        @click="$emit('update:search', '')"
                      >
                        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
                          <line x1="18" y1="6" x2="6" y2="18" />
                          <line x1="6" y1="6" x2="18" y2="18" />
                        </svg>
                      </button>
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
                          <span class="skill-sub-name">{{ item.name }}</span>
                          <span v-if="!item.enabled" class="skill-sub-off">已停用</span>
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
  // 标题行与专家 / 连接器面板保持一致（标题 + 计数），由父组件传入
  title: { type: String, default: '我的技能' },
  count: { type: Number, default: 0 },
})

defineEmits(['update:search', 'select', 'import', 'manage'])
</script>

<style scoped>
.skill-sub-notice {
  margin: 6px 10px 0;
  padding: 6px 10px;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.5;
  background: var(--bg-tertiary);
  color: var(--text-secondary);
}

.skill-sub-notice.error {
  background: rgba(220, 38, 38, 0.08);
  color: #dc2626;
}

/* 内容容器：外框在 ChatInput 的 .plus-subpanel 上（flex column + 内联 height）。
   这里用 flex:1 撑满外框内容区，auto 高度时又只占内容高度 —— 两种状态都成立。 */
.skill-sub-inner {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
}

/* 标题行：与专家 / 连接器面板同款（三段面板的头部、搜索框、行、底栏规格必须一致） */
.plus-subpanel-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 2px 4px 8px;
  flex: 0 0 auto;
}

.plus-subpanel-title {
  flex: 1;
  min-width: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.plus-menu-count {
  font-size: 12px;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}

.skill-sub-search {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 0 0 auto;
  margin: 0 4px 8px;
  padding: 7px 10px;
  border-radius: 10px;
  background: var(--bg-tertiary);
  border: 1px solid transparent;
  transition: border-color 0.15s;
}

.skill-sub-search:focus-within {
  border-color: color-mix(in srgb, var(--accent-color) 45%, transparent);
}

.skill-sub-search-icon {
  width: 15px;
  height: 15px;
  flex-shrink: 0;
  color: var(--text-secondary);
}

.skill-sub-search-input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  font-size: 13px;
  line-height: 1.3;
  font-family: inherit;
  color: var(--text-primary);
}

.skill-sub-search-clear {
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

.skill-sub-search-clear:hover {
  color: var(--text-primary);
}

.skill-sub-search-clear svg {
  width: 12px;
  height: 12px;
}

.skill-sub-list {
  flex: 1 1 auto;
  /* 面板高度由父组件 syncSubpanelBox 内联给出（打开后只增不减），
     列表必须能被压缩（min-height:0），否则会顶破面板边界。 */
  min-height: 0;
  overflow-y: auto;
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  scrollbar-width: thin;
}

/* 空态 / 无结果提示：搜索后条数变少、面板高度保持不变，单条提示靠垂直居中，
   看起来才像「有意的空状态」而不是「内容没加载出来」。 */
.skill-sub-list > .skill-sub-hint:only-child {
  margin: auto 0;
}

.skill-sub-hint {
  padding: 16px 10px;
  font-size: 13px;
  color: var(--text-secondary);
  text-align: center;
}

.skill-sub-hint.error {
  color: #dc2626;
}

.skill-sub-list.plus-experts {
  max-height: 320px;
}

/* 行规格以连接器行为准（.plus-list-item）：内边距 / 间距 / 圆角 / 字号三者完全一致 */
.skill-sub-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 8px 10px;
  border: none;
  border-radius: 8px;
  background: transparent;
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition: background 0.15s;
}

.skill-sub-item:hover {
  background: var(--bg-tertiary);
}

.skill-sub-item.active {
  background: color-mix(in srgb, var(--accent-color) 14%, transparent);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent-color) 45%, transparent);
}

.skill-sub-item.disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.skill-sub-item.disabled:hover {
  background: transparent;
}

.skill-sub-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  border-radius: 8px;
  /* 与连接器行 .plus-list-icon（initial 变体）一致：12px / 700 */
  font-size: 12px;
  font-weight: 700;
}

.skill-sub-icon-emoji {
  font-size: 14px;
  line-height: 1;
}

/* 名称 / 标签 / 描述同排（与专家 / 连接器面板一致）：name 不收缩、desc 占满剩余并省略 */
.skill-sub-body {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.skill-sub-name {
  flex-shrink: 0;
  max-width: 46%;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.skill-sub-off {
  flex-shrink: 0;
  /* baseline 对齐时小标签会偏上，单独拉回垂直居中 */
  align-self: center;
  padding: 1px 6px;
  border-radius: 5px;
  font-size: 11px;
  background: var(--bg-tertiary);
  color: var(--text-secondary);
}

.skill-sub-desc {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.skill-sub-check {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
  color: var(--accent-color);
}

.skill-sub-footer {
  margin-top: 8px;
  padding-top: 6px;
  border-top: 1px solid var(--border-color);
  display: flex;
  flex-direction: column;
  flex: 0 0 auto;
}

.skill-sub-footer-btn {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border: none;
  border-radius: 8px;
  background: transparent;
  font-size: 13px;
  font-family: inherit;
  color: var(--text-primary);
  cursor: pointer;
  transition: background 0.15s;
}

.skill-sub-footer-btn:hover {
  background: var(--bg-tertiary);
}

.skill-sub-footer-icon {
  width: 15px;
  height: 15px;
  flex-shrink: 0;
  color: var(--text-secondary);
}
</style>
