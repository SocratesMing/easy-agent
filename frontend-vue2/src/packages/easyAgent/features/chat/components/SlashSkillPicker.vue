<template>
  <div
    v-if="open"
    ref="rootRef"
    class="skill-picker"
    :style="pickerStyle"
    @click.stop
  >
    <div class="skill-picker-header">
      <span class="skill-picker-hint">↑↓ 选择 · Enter 确认 · Esc 关闭</span>
    </div>

    <div v-if="loading" class="skill-picker-state">
      正在加载技能…
    </div>
    <div
      v-if="menuItems.length === 0 && !loading"
      class="skill-picker-state"
    >
      {{
        skillOptions.length === 0
          ? "没有匹配项，可到技能中心添加并启用技能"
          : "没有匹配的指令或技能"
      }}
    </div>

    <div
      v-if="menuItems.length"
      ref="listRef"
      class="skill-picker-list"
      role="listbox"
      aria-label="指令和技能"
    >
      <ul
        v-for="group in pickerGroups"
        :key="group.title"
        class="picker-group"
        role="group"
        :aria-label="group.title"
      >
        <li class="picker-group-title" role="presentation">
          {{ group.title }} ({{ group.rows.length }})
        </li>
        <li
          v-for="row in group.rows"
          :key="(row.skill.command ? 'command:' : 'skill:') + row.skill.dir_name"
          class="skill-picker-item"
          :class="{
            active: row.index === activeIndex,
            unavailable: row.skill.disabled,
          }"
          :aria-disabled="row.skill.disabled ? 'true' : 'false'"
          role="option"
          :aria-selected="row.index === activeIndex ? 'true' : 'false'"
          @mouseenter="$emit('hover', row.index)"
          @mousedown.prevent="$emit('select', row.skill)"
          @click="$emit('select', row.skill)"
        >
          <SkillIcon
            :icon="row.skill.icon"
            :category="iconCategoryFor(row.skill.category_id)"
            size="xs"
          />
          <span class="skill-picker-body">
            <span class="skill-picker-name"
              ><span
                v-for="(part, pi) in row.nameParts"
                :key="'n' + pi"
                :class="{ 'skill-picker-hit': part.hit }"
                >{{ part.text }}</span
              ></span
            >
            <span class="skill-picker-desc"
              ><span
                v-for="(part, pi) in row.descParts"
                :key="'d' + pi"
                :class="{ 'skill-picker-hit': part.hit }"
                >{{ part.text }}</span
              ></span
            >
          </span>
          <span class="skill-picker-slash">/{{ row.skill.dir_name }}</span>
        </li>
      </ul>
    </div>
  </div>
</template>

<script>
import SkillIcon from "../../../shared/components/SkillIcon.vue";

export default {
  name: "SlashSkillPicker",
  components: { SkillIcon },
  props: {
    open: { type: Boolean, default: false },
    loading: { type: Boolean, default: false },
    menuItems: { type: Array, default: () => [] },
    skillOptions: { type: Array, default: () => [] },
    pickerGroups: { type: Array, default: () => [] },
    activeIndex: { type: Number, default: 0 },
    pickerStyle: { type: Object, default: () => ({}) },
    iconCategoryFor: { type: Function, required: true },
  },
  methods: {
    scrollActiveIntoView(index) {
      const list = this.$refs.listRef;
      if (!list) return;
      const item = list.querySelectorAll(".skill-picker-item")[index];
      if (!item) return;
      const top = item.offsetTop;
      const bottom = top + item.offsetHeight;
      if (top < list.scrollTop) list.scrollTop = top;
      else if (bottom > list.scrollTop + list.clientHeight) {
        list.scrollTop = bottom - list.clientHeight;
      }
    },
    containsTarget(target) {
      const root = this.$refs.rootRef;
      return Boolean(root && target && root.contains(target));
    },
  },
};
</script>

<style scoped>
.skill-picker {
  z-index: 1200;
  display: flex;
  flex-direction: column;
  max-height: min(320px, 42vh);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  background: var(--bg-primary);
  box-shadow: 0 12px 32px rgba(15, 23, 42, 0.18);
  overflow: hidden;
}

.skill-picker-header {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border-color);
  background: var(--bg-secondary);
  font-size: 12px;
  color: var(--text-secondary);
}

.skill-picker-hint {
  font-size: 11px;
  opacity: 0.8;
}

.skill-picker-state {
  padding: 14px 12px;
  font-size: 12.5px;
  color: var(--text-secondary);
  text-align: center;
}

.skill-picker-list {
  flex: 1 1 auto;
  min-height: 0;
  position: relative;
  margin: 0;
  padding: 6px;
  list-style: none;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-width: thin;
}

.picker-group {
  margin: 0;
  padding: 0;
  list-style: none;
}

.picker-group + .picker-group {
  margin-top: 12px;
}

.picker-group-title {
  padding: 8px 10px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
}

.skill-picker-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 8px;
  border-radius: 8px;
  cursor: pointer;
}

.skill-picker-item.active {
  background: color-mix(in srgb, var(--accent-color) 12%, transparent);
}

.skill-picker-item.unavailable {
  opacity: 0.55;
  cursor: not-allowed;
}

.skill-picker-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: row;
  align-items: baseline;
  gap: 8px;
}

.skill-picker-hit {
  color: var(--accent-color);
}

.skill-picker-name .skill-picker-hit {
  font-weight: 600;
}

.skill-picker-name {
  flex-shrink: 0;
  font-size: 13px;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.skill-picker-desc {
  flex: 1;
  min-width: 0;
  font-size: 11.5px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.skill-picker-slash {
  flex-shrink: 0;
  font-size: 11.5px;
  color: var(--text-secondary);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

@media (max-width: 48rem) {
  .skill-picker {
    max-height: min(240px, 38vh);
  }

  .skill-picker-hint {
    display: none;
  }
}
</style>
