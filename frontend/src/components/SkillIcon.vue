<!--
  技能图标：优先用 SKILL.md frontmatter 里的 icon（emoji），否则按分类渲染内置 SVG。
  分类配色集中在这里，避免列表页 / 详情页各写一份。
-->
<template>
  <div class="skill-icon" :class="[category, size]">
    <span v-if="icon" class="icon-emoji" aria-hidden="true">{{ icon }}</span>
    <svg
      v-else-if="category === 'doc'"
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      aria-hidden="true"
    >
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
      <line x1="16" y1="13" x2="8" y2="13" />
      <line x1="16" y1="17" x2="8" y2="17" />
    </svg>
    <svg
      v-else-if="category === 'code'"
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      aria-hidden="true"
    >
      <polyline points="16 18 22 12 16 6" />
      <polyline points="8 6 2 12 8 18" />
    </svg>
    <svg
      v-else-if="category === 'design'"
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="10" />
      <circle cx="12" cy="12" r="6" />
      <circle cx="12" cy="12" r="2" />
    </svg>
    <svg
      v-else-if="category === 'data'"
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      aria-hidden="true"
    >
      <line x1="18" y1="20" x2="18" y2="10" />
      <line x1="12" y1="20" x2="12" y2="4" />
      <line x1="6" y1="20" x2="6" y2="14" />
    </svg>
    <svg
      v-else-if="category === 'api'"
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      aria-hidden="true"
    >
      <path d="M18 20V10" />
      <path d="M12 20V4" />
      <path d="M6 20v-6" />
    </svg>
    <svg
      v-else
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      aria-hidden="true"
    >
      <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
    </svg>
  </div>
</template>

<script setup>
defineProps({
  /** frontmatter 里的 icon（emoji）；为空时按分类渲染 SVG */
  icon: { type: String, default: '' },
  /** 分类 key：doc / code / design / data / api / default */
  category: { type: String, default: 'default' },
  /** xs（输入框技能标签）/ sm（卡片）/ lg（详情页头部） */
  size: { type: String, default: 'sm' },
})
</script>

<style scoped>
.skill-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  border-radius: 10px;
}

.skill-icon.xs {
  width: 22px;
  height: 22px;
  border-radius: 6px;
}

.skill-icon.xs svg {
  width: 13px;
  height: 13px;
}

.skill-icon.xs .icon-emoji {
  font-size: 12px;
}

.skill-icon.sm {
  width: 40px;
  height: 40px;
}

.skill-icon.lg {
  width: 56px;
  height: 56px;
  border-radius: 14px;
}

.skill-icon svg {
  width: 20px;
  height: 20px;
}

.skill-icon.lg svg {
  width: 26px;
  height: 26px;
}

.icon-emoji {
  font-size: 20px;
  line-height: 1;
  font-family: "Apple Color Emoji", "Segoe UI Emoji", "Noto Color Emoji", sans-serif;
}

.skill-icon.lg .icon-emoji {
  font-size: 26px;
}

.skill-icon.doc {
  background: #fef3c7;
  color: #d97706;
}

.skill-icon.code {
  background: #ede9fe;
  color: #7c3aed;
}

.skill-icon.design {
  background: #fce7f3;
  color: #db2777;
}

.skill-icon.data {
  background: #d1fae5;
  color: #059669;
}

.skill-icon.api {
  background: #e0e7ff;
  color: #4f46e5;
}

.skill-icon.default {
  background: #f0f9ff;
  color: #0ea5e9;
}

/* 深色主题：底色降低明度、字色提亮，保持可读性 */
html[data-theme="dark"] .skill-icon.doc {
  background: #452c09;
  color: #fbbf24;
}

html[data-theme="dark"] .skill-icon.code {
  background: #2e1065;
  color: #c4b5fd;
}

html[data-theme="dark"] .skill-icon.design {
  background: #4c0519;
  color: #f9a8d4;
}

html[data-theme="dark"] .skill-icon.data {
  background: #064e3b;
  color: #6ee7b7;
}

html[data-theme="dark"] .skill-icon.api {
  background: #312e81;
  color: #a5b4fc;
}

html[data-theme="dark"] .skill-icon.default {
  background: #0c4a6e;
  color: #7dd3fc;
}
</style>
