<template>
  <div class="plus-subpanel-list">
    <div v-if="loading" class="plus-list-hint">加载中…</div>
    <div v-else-if="error" class="plus-list-hint error">{{ error }}</div>
    <div v-else-if="rows.length === 0" class="plus-list-hint">{{ emptyText }}</div>
    <div v-else-if="noMatch" class="plus-list-hint">未找到匹配的连接器</div>
  <!-- 技能行可点选（挂成输入框令牌，可连续挂多个）；
       连接器行不可点选，行尾是启用开关（改的是连接器自身的状态，
       与「专家·技能·连接器」面板双向同步，启用/停用都能点） -->
  <div
    v-else
    v-for="item in rows"
    :key="item.key"
    class="plus-list-item"
    :class="{
      selectable: selectable,
      selected: rowSelected(item),
      'is-disabled': rowDisabled(item),
      'connector-row': isConnector,
    }"
    :role="selectable ? 'button' : null"
    :tabindex="selectable && !rowDisabled(item) ? 0 : null"
    :title="rowTitle(item)"
    @click="$emit('select', item)"
    @keydown.enter.prevent="$emit('select', item)"
  >
    <span class="plus-list-icon" :class="{ initial: !item.icon }">{{ item.icon || item.initial }}</span>
    <span class="plus-list-body">
      <span class="plus-list-name-row">
        <span class="plus-list-name">{{ item.name }}</span>
        <span v-if="item.tag" class="plus-list-tag" :class="item.tagType">{{ item.tag }}</span>
      </span>
      <span class="plus-list-desc">{{ item.desc }}</span>
    </span>
    <!-- 连接器行：启用开关（与「专家·技能·连接器」同一份状态）；技能行：已选标记 -->
    <button
      v-if="isConnector"
      type="button"
      role="switch"
      class="connector-switch"
      :class="{
        on: connectorOn(item),
        'is-off': switchDisabled(item),
        'is-busy': connectorBusy(item),
      }"
      :disabled="switchDisabled(item)"
      :aria-checked="connectorOn(item) ? 'true' : 'false'"
      :aria-busy="connectorBusy(item) ? 'true' : 'false'"
      :aria-label="switchLabel(item)"
      :title="switchTitle(item)"
      @click.stop="$emit('toggle-connector', item)"
    >
      <span v-if="connectorBusy(item)" class="connector-switch-spinner"></span>
      <span class="connector-switch-knob"></span>
    </button>
    <span v-else-if="rowSelected(item)" class="plus-list-state">已选</span>
  </div>
  </div>
</template>

<script>
/**
 * 「+」→ 技能 / 连接器共用的条目列表（2026-10-20 从 ChatInput.vue 抽出）。
 *
 * 技能行：整行可点，挂/取消输入框令牌，行尾显示「已选」；
 * 连接器行：行尾是启用开关，改的是连接器自身状态（与专家·技能·连接器面板同步）。
 */
export default {
  name: 'PlusList',
  props: {
    loading: { type: Boolean, default: false },
    error: { type: String, default: '' },
    emptyText: { type: String, default: '暂无内容' },
    noMatch: { type: Boolean, default: false },
    rows: { type: Array, default: () => [] },
    isConnector: { type: Boolean, default: false },
    selectable: { type: Boolean, default: false },
    selectedKeys: { type: Array, default: () => [] },
    togglingKey: { type: String, default: '' },
  },
  emits: ['select', 'toggle-connector'],
  methods: {
    rowSelected(item) {
      return this.selectable && this.selectedKeys.includes(item.dir_name)
    },
    rowDisabled(item) {
      return this.selectable && !item.enabled
    },
    rowTitle(item) {
      if (!this.selectable) return ''
      return item.enabled ? `选择「${item.name}」` : '该技能已停用'
    },
    connectorOn(item) {
      return !!item && item.enabled !== false
    },
    switchDisabled(item) {
      const key = (item && item.connectorId) || ''
      if (!key) return true
      return this.togglingKey === key
    },
    connectorBusy(item) {
      const key = (item && item.connectorId) || ''
      return !!key && this.togglingKey === key
    },
    switchLabel(item) {
      if (this.connectorBusy(item)) return `正在保存连接器 ${item.name} 的开关`
      return `${this.connectorOn(item) ? '停用' : '启用'}连接器 ${item.name}`
    },
    switchTitle(item) {
      if (this.connectorBusy(item)) return '正在保存…'
      return this.connectorOn(item)
        ? '已启用（点击停用，「专家·技能·连接器」面板同步生效）'
        : '已停用（点击启用，「专家·技能·连接器」面板同步生效）'
    },
  },
}
</script>

<style scoped>
.plus-list-item.connector-row {
  cursor: default;
}

.connector-switch {
  position: relative;
  flex-shrink: 0;
  width: 34px;
  height: 18px;
  margin-left: 8px;
  padding: 0;
  border: 1px solid var(--border-color);
  border-radius: 999px;
  background: var(--bg-tertiary);
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease;
}

.connector-switch .connector-switch-knob {
  position: absolute;
  top: 1px;
  left: 1px;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: #ffffff;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.25);
  transition: transform 0.15s ease;
}

.connector-switch.on {
  background: var(--accent-color);
  border-color: var(--accent-color);
}

.connector-switch.on .connector-switch-knob {
  transform: translateX(16px);
}

.connector-switch:focus-visible {
  outline: 2px solid var(--accent-color);
  outline-offset: 2px;
}

.connector-switch:disabled {
  cursor: not-allowed;
}

.connector-switch.is-off:disabled {
  opacity: 0.55;
}

.connector-switch-spinner {
  position: absolute;
  top: 50%;
  left: 50%;
  width: 10px;
  height: 10px;
  margin: -5px 0 0 -5px;
  border: 1.5px solid var(--text-secondary);
  border-top-color: transparent;
  border-radius: 50%;
  animation: connector-switch-spin 0.6s linear infinite;
}

.connector-switch.is-busy .connector-switch-knob {
  opacity: 0.35;
}

.plus-list-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 8px;
  transition: background 0.15s ease;
}

.plus-list-item:hover {
  background: var(--bg-tertiary);
}

.plus-list-item.selectable {
  cursor: pointer;
}

.plus-list-item.selected {
  background: color-mix(in srgb, var(--accent-color) 14%, transparent);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent-color) 45%, transparent);
}

.plus-list-item.is-disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.plus-list-item.is-disabled:hover {
  background: transparent;
}

.plus-list-state {
  flex-shrink: 0;
  align-self: center;
  padding: 1px 6px;
  border-radius: 5px;
  font-size: 11px;
  font-weight: 500;
  background: var(--accent-color);
  color: #fff;
}

.plus-list-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  border-radius: 8px;
  font-size: 14px;
  background: color-mix(in srgb, var(--accent-color) 12%, var(--bg-tertiary));
}

.plus-list-icon.initial {
  font-size: 12px;
  font-weight: 700;
  color: var(--accent-color);
}

.plus-list-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.plus-list-name-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.plus-list-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.plus-list-tag {
  flex-shrink: 0;
  padding: 1px 6px;
  border-radius: 5px;
  font-size: 11px;
}

.plus-list-tag.on {
  background: #dcfce7;
  color: #16a34a;
}

.plus-list-tag.off {
  background: var(--bg-tertiary);
  color: var(--text-secondary);
}

.plus-list-tag.custom {
  background: #ede9fe;
  color: #7c3aed;
}

.plus-list-desc {
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-secondary);
  display: -webkit-box;
  -webkit-line-clamp: 1;
  line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
  overflow-wrap: anywhere;
}

.plus-list-hint {
  padding: 16px 10px;
  font-size: 13px;
  color: var(--text-secondary);
  text-align: center;
}

.plus-list-hint.error {
  color: #dc2626;
}
</style>
