<template>
  <Teleport to="body">
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
  </Teleport>
</template>

<script setup>
import { ref } from "vue";
import SkillIcon from "../../../shared/components/SkillIcon.vue";

defineProps({
  open: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
  menuItems: { type: Array, default: () => [] },
  skillOptions: { type: Array, default: () => [] },
  pickerGroups: { type: Array, default: () => [] },
  activeIndex: { type: Number, default: 0 },
  pickerStyle: { type: Object, default: () => ({}) },
  iconCategoryFor: { type: Function, required: true },
});

defineEmits(["hover", "select"]);

const rootRef = ref(null);
const listRef = ref(null);

function scrollActiveIntoView(index) {
  const list = listRef.value;
  if (!list) return;
  const item = list.querySelectorAll(".skill-picker-item")[index];
  if (!item) return;
  const top = item.offsetTop;
  const bottom = top + item.offsetHeight;
  if (top < list.scrollTop) list.scrollTop = top;
  else if (bottom > list.scrollTop + list.clientHeight) {
    list.scrollTop = bottom - list.clientHeight;
  }
}

function containsTarget(target) {
  return Boolean(rootRef.value && target && rootRef.value.contains(target));
}

defineExpose({ scrollActiveIntoView, containsTarget });
</script>
