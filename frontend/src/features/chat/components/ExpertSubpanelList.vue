<template>
  <!-- 显隐条件（加载完成且无错误）由父组件 ChatInput.vue 控制，本组件只渲染列表。 -->
  <div class="skill-sub-list plus-experts" :class="{ stacked }" @scroll="$emit('scroll', $event)">
  <!-- 上区：我的专家（仅展示「可用」状态的专家，与专家 Tab 的召唤约束一致） -->
  <div class="plus-experts-section">
    <span class="plus-experts-section-label">我的专家</span>
    <span v-if="myExperts.length" class="plus-experts-section-count">{{ myExperts.length }}</span>
  </div>
  <button
    v-for="item in visibleMyExperts"
    :key="`mine:${item.export_agent_id}`"
    type="button"
    class="plus-expert-row"
    :class="{ selected: item.export_agent_id === activeExpertId }"
    :title="item.export_agent_id === activeExpertId ? '再次点击取消召唤' : `召唤「${item.export_name_cn}」`"
    @click="$emit('toggle', item)"
  >
    <span class="plus-expert-avatar" :class="expertAvatarClass(item)">{{ expertAvatarChar(item) }}</span>
    <span class="plus-expert-main">
      <span class="plus-expert-name-row">
        <span class="plus-expert-name">{{ item.export_name_cn }}</span>
        <span v-if="stacked && item.export_agent_id === activeExpertId" class="plus-expert-state">已选</span>
      </span>
      <span class="plus-expert-desc">{{ expertDesc(item) }}</span>
    </span>
    <span v-if="!stacked && item.export_agent_id === activeExpertId" class="plus-expert-state">已选</span>
  </button>
  <div v-if="!myExperts.length" class="plus-expert-empty">
    <span class="plus-expert-empty-text">您还没有创建自己的专家</span>
    <button type="button" class="plus-expert-create" @click="$emit('create')">
      「去创建」
    </button>
  </div>
  <!-- 兜底：内容未超出容器高度时不会产生滚动条，用可点击的「还有 N 位」保证可达 -->
  <button
    v-if="myExperts.length > visibleMyExperts.length"
    type="button"
    class="plus-expert-more"
    @click="$emit('more')"
  >
    还有 {{ myExperts.length - visibleMyExperts.length }} 位 · 点击加载
  </button>

  <div class="plus-experts-divider"></div>

  <!-- 下区：公共（预置）专家 -->
  <div class="plus-experts-section">
    <span class="plus-experts-section-label">公共专家</span>
    <span class="plus-experts-badge">预置</span>
    <span v-if="publicExperts.length" class="plus-experts-section-count">{{ publicExperts.length }}</span>
  </div>
  <button
    v-for="item in visiblePublicExperts"
    :key="`public:${item.export_agent_id}`"
    type="button"
    class="plus-expert-row"
    :class="{ selected: item.export_agent_id === activeExpertId }"
    :title="item.export_agent_id === activeExpertId ? '再次点击取消召唤' : `召唤「${item.export_name_cn}」`"
    @click="$emit('toggle', item)"
  >
    <span class="plus-expert-avatar" :class="expertAvatarClass(item)">{{ expertAvatarChar(item) }}</span>
    <span class="plus-expert-main">
      <span class="plus-expert-name-row">
        <span class="plus-expert-name">{{ item.export_name_cn }}</span>
        <span v-if="stacked && item.export_agent_id === activeExpertId" class="plus-expert-state">已选</span>
      </span>
      <span class="plus-expert-desc">{{ expertDesc(item) }}</span>
    </span>
    <span v-if="!stacked && item.export_agent_id === activeExpertId" class="plus-expert-state">已选</span>
  </button>
  <div v-if="!publicExperts.length" class="skill-sub-hint">公共专家目录为空</div>
  <button
    v-if="publicExperts.length > visiblePublicExperts.length"
    type="button"
    class="plus-expert-more"
    @click="$emit('more')"
  >
    还有 {{ publicExperts.length - visiblePublicExperts.length }} 位 · 点击加载
  </button>
  </div>
</template>

<script>
/**
 * 「+」→ 专家子面板列表（与 frontend-vue2 同名组件一致，从 ChatInput.vue 抽出）：
 * 上区「我的专家」、下区「公共专家」，点击行 = 召唤 / 取消召唤。
 * 标题、计数与搜索属于容器层，仍由父组件渲染。
 */
import { expertAvatarChar, expertAvatarClass } from '../../../utils/expertAvatar.js'

export default {
  name: 'ExpertSubpanelList',
  props: {
    myExperts: { type: Array, default: () => [] },
    visibleMyExperts: { type: Array, default: () => [] },
    publicExperts: { type: Array, default: () => [] },
    visiblePublicExperts: { type: Array, default: () => [] },
    activeExpertId: { type: String, default: '' },
    // 名称（含「已选」）一行、描述一行，与知识库面板同款；窄容器（AI 侧边栏）使用
    stacked: { type: Boolean, default: false },
  },
  emits: ['toggle', 'more', 'create', 'scroll'],
  methods: {
    expertAvatarChar,
    expertAvatarClass,
    /** 行右侧的描述：优先功能描述，回退专家描述 */
    expertDesc(item) {
      return (
        String((item && item.export_description) || '').replace(/\s*\n+\s*/g, ' ').trim() ||
        String((item && item.export_prompt) || '').replace(/\s*\n+\s*/g, ' ').trim() ||
        '暂无描述'
      )
    },
  },
}
</script>

<style scoped>
.skill-sub-list {
  flex: 1;
  max-height: 320px;
  overflow-y: auto;
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  scrollbar-width: thin;
}

.skill-sub-hint {
  padding: 24px 10px;
  font-size: 13px;
  color: var(--text-secondary);
  text-align: center;
}

.plus-experts-section {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px 6px;
}

.plus-experts-section-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary, #64748b);
}

.plus-experts-section-count {
  font-size: 11px;
  color: var(--text-secondary, #64748b);
  font-variant-numeric: tabular-nums;
}

.plus-experts-badge {
  padding: 1px 6px;
  border-radius: 5px;
  font-size: 10.5px;
  background: var(--bg-tertiary, #f1f5f9);
  color: var(--text-secondary, #64748b);
}

.plus-experts-divider {
  height: 1px;
  margin: 8px 4px;
  background: var(--border-color, #e2e8f0);
}

.plus-expert-row {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  /* 与技能面板 .plus-list-item 的行内边距保持一致 */
  padding: 8px 10px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  cursor: pointer;
  text-align: left;
  transition: background 0.15s ease, border-color 0.15s ease;
}

.plus-expert-row:hover {
  background: var(--bg-tertiary, #f1f5f9);
}

.plus-expert-row.selected {
  background: color-mix(in srgb, var(--accent-color) 14%, transparent);
  border-color: color-mix(in srgb, var(--accent-color) 45%, transparent);
}

.plus-expert-avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  /* 尺寸/字号/字重与技能面板 .plus-list-icon（initial 变体）一致 */
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  border-radius: 8px;
  font-size: 12px;
  font-weight: 700;
  /* 底色/字色由全局 .ea-0 ~ .ea-7 提供（按专家 ID 稳定取档）；此处不写死，
     否则会与全局色板同权重竞争、按注入顺序取胜而覆盖配色。 */
}

.plus-expert-main {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.plus-expert-name {
  flex-shrink: 0;
  max-width: 52%;
  /* 字体样式与技能面板 .plus-list-name 一致 */
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary, #1e293b);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.plus-expert-desc {
  flex: 1;
  min-width: 0;
  /* 字体样式与技能面板 .plus-list-desc 一致 */
  font-size: 12px;
  color: var(--text-secondary, #64748b);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.plus-expert-name-row {
  display: contents;
}

.stacked .plus-expert-row {
  align-items: flex-start;
}

.stacked .plus-expert-main {
  flex-direction: column;
  align-items: stretch;
  gap: 2px;
}

.stacked .plus-expert-name-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.stacked .plus-expert-name {
  flex: 1;
  max-width: none;
}

.stacked .plus-expert-desc {
  line-height: 1.5;
}

.plus-expert-state {
  flex-shrink: 0;
  padding: 1px 6px;
  border-radius: 5px;
  font-size: 11px;
  font-weight: 500;
  background: var(--accent-color, #0ea5e9);
  color: #fff;
}

.plus-expert-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 10px 8px;
  font-size: 12.5px;
  color: var(--text-secondary, #64748b);
}

.plus-expert-create {
  padding: 0;
  border: none;
  background: transparent;
  font-size: 12.5px;
  color: var(--text-secondary, #64748b);
  cursor: pointer;
}

.plus-expert-create:hover {
  color: var(--accent-color, #0ea5e9);
  text-decoration: underline;
}

.plus-expert-more {
  width: 100%;
  margin-top: 2px;
  padding: 5px 8px;
  border: none;
  border-radius: 8px;
  background: transparent;
  font-size: 11.5px;
  color: var(--text-secondary, #64748b);
  text-align: center;
  cursor: pointer;
}

.plus-expert-more:hover {
  background: var(--bg-tertiary, #f1f5f9);
  color: var(--accent-color, #0ea5e9);
}
</style>
