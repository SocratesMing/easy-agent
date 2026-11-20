<template>
  <nav
    v-if="items.length > 0"
    class="user-message-nav"
    :class="{ 'is-compact': items.length > 16 }"
    aria-label="用户消息快速定位"
    @mouseleave="clearHover"
  >
    <div ref="railRef" class="user-message-rail" @scroll="syncPreviewPosition">
      <button
        v-for="(item, idx) in items"
        :key="item.id"
        :ref="el => setTickRef(el, idx)"
        type="button"
        class="user-message-tick"
        :class="tickClass(idx)"
        :aria-label="`定位到第 ${item.turnNumber} 条提问：${item.prompt}`"
        :aria-current="idx === activeIndex ? 'location' : null"
        @mouseenter="onHover(idx)"
        @focus="onHover(idx)"
        @blur="onBlur(idx)"
        @click="onSelect(item, idx)"
      >
        <span class="user-message-tick-bar"></span>
      </button>
    </div>

    <div
      v-if="hoveredItem"
      class="user-message-preview"
      :style="previewStyle"
      role="tooltip"
    >
      <div class="user-message-preview-prompt">{{ hoveredItem.prompt }}</div>
      <div v-if="hoveredItem.reply" class="user-message-preview-reply">{{ hoveredItem.reply }}</div>
    </div>
  </nav>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { getTickLevel } from '../../../shared/chat/userMessageNav.js'

const props = defineProps({
  items: {
    type: Array,
    default: () => []
  },
  activeIndex: {
    type: Number,
    default: -1
  }
})

const emit = defineEmits(['select'])

const railRef = ref(null)
const tickRefs = ref({})
const hoveredIndex = ref(-1)
const previewTop = ref(0)

function setTickRef(el, idx) {
  if (el) {
    tickRefs.value[idx] = el
  } else {
    delete tickRefs.value[idx]
  }
}

const hoveredItem = computed(() => {
  if (hoveredIndex.value < 0 || hoveredIndex.value >= props.items.length) return null
  return props.items[hoveredIndex.value]
})

const previewStyle = computed(() => ({
  top: `${previewTop.value}px`
}))

watch(() => props.items.length, (len) => {
  if (hoveredIndex.value >= len) {
    hoveredIndex.value = -1
  }
})

function tickClass(idx) {
  return `is-${getTickLevel(idx, props.activeIndex, hoveredIndex.value)}`
}

function updatePreviewTop(idx) {
  const rail = railRef.value
  const el = tickRefs.value[idx]
  if (!rail || !el) return
  const rawTop = el.offsetTop + el.offsetHeight / 2 - rail.scrollTop
  const minTop = 16
  const maxTop = Math.max(minTop, rail.clientHeight - 16)
  previewTop.value = Math.max(minTop, Math.min(maxTop, rawTop))
}

function onHover(idx) {
  hoveredIndex.value = idx
  updatePreviewTop(idx)
}

function onBlur(idx) {
  if (hoveredIndex.value === idx) {
    hoveredIndex.value = -1
  }
}

function clearHover() {
  hoveredIndex.value = -1
}

function syncPreviewPosition() {
  if (hoveredIndex.value >= 0) {
    updatePreviewTop(hoveredIndex.value)
  }
}

function onSelect(item, idx) {
  emit('select', { messageIndex: item.messageIndex, navIndex: idx })
}
</script>

<style scoped>
.user-message-nav {
  position: absolute;
  left: 12px;
  top: 50%;
  transform: translateY(-50%);
  z-index: 10;
  display: flex;
  align-items: center;
  user-select: none;
}

.user-message-rail {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  max-height: min(56vh, 380px);
  overflow-y: auto;
  overflow-x: visible;
  padding: 6px 4px;
  scrollbar-width: none;
  -ms-overflow-style: none;
}

.user-message-rail::-webkit-scrollbar {
  display: none;
  width: 0;
  height: 0;
}

.user-message-tick {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  width: 28px;
  height: 15px;
  padding: 0;
  margin: 0;
  border: none;
  background: transparent;
  cursor: pointer;
  outline: none;
  flex: 0 0 auto;
}

.user-message-nav.is-compact .user-message-tick {
  height: 12px;
}

.user-message-tick-bar {
  display: block;
  width: 10px;
  height: 2px;
  border-radius: 999px;
  background: var(--user-nav-tick-default, #dcdfe6);
  transition: width 0.16s ease, height 0.16s ease, background-color 0.16s ease;
}

.user-message-tick.is-near-2 .user-message-tick-bar {
  width: 12px;
  background: var(--user-nav-tick-near-2, #cfd4dc);
}

.user-message-tick.is-near-1 .user-message-tick-bar {
  width: 15px;
  background: var(--user-nav-tick-near-1, #c0c6d0);
}

.user-message-tick.is-hovered .user-message-tick-bar,
.user-message-tick:focus-visible .user-message-tick-bar {
  width: 19px;
  height: 2px;
  background: var(--user-nav-tick-hovered, #6b7280);
}

.user-message-tick.is-active .user-message-tick-bar {
  width: 24px;
  height: 2.5px;
  background: var(--user-nav-tick-active, #111827);
}

.user-message-preview {
  position: absolute;
  left: calc(100% + 10px);
  transform: translateY(-50%);
  width: 300px;
  max-width: min(320px, 55vw);
  padding: 12px 14px;
  background: var(--user-nav-card-bg, #ffffff);
  border: 1px solid var(--user-nav-card-border, #e5e7eb);
  border-radius: 14px;
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.08), 0 2px 6px rgba(15, 23, 42, 0.04);
  pointer-events: none;
  box-sizing: border-box;
  z-index: 20;
}

.user-message-preview-prompt {
  font-size: 13.5px;
  font-weight: 500;
  line-height: 1.45;
  color: var(--user-nav-prompt-color, #111827);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.user-message-preview-reply {
  margin-top: 6px;
  font-size: 12.5px;
  line-height: 1.5;
  color: var(--user-nav-reply-color, #9ca3af);
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
}
</style>
