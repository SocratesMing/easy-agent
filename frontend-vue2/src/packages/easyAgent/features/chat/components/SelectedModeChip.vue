<template>
  <div class="selected-mode-chip" role="status">
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M4 6h10"></path><path d="M18 6h2"></path><circle cx="16" cy="6" r="2"></circle>
      <path d="M4 12h2"></path><path d="M10 12h10"></path><circle cx="8" cy="12" r="2"></circle>
      <path d="M4 18h10"></path><path d="M18 18h2"></path><circle cx="16" cy="18" r="2"></circle>
    </svg>
    <span class="selected-mode-command">/{{ mode }}</span>
    <span class="selected-mode-label">{{ label }}</span>
    <button type="button" aria-label="退出当前模式" title="退出当前模式" @click="$emit('clear')">
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" aria-hidden="true">
        <line x1="18" y1="6" x2="6" y2="18"></line>
        <line x1="6" y1="6" x2="18" y2="18"></line>
      </svg>
    </button>
  </div>
</template>

<script>
import { interactionModeMeta, normalizeInteractionMode } from '../../../shared/chat/planInteraction.js'

export default {
  name: 'SelectedModeChip',
  props: {
    mode: { type: String, default: 'normal' },
  },
  computed: {
    label() {
      const meta = interactionModeMeta(normalizeInteractionMode(this.mode))
      return meta ? meta.label : ''
    },
  },
}
</script>

<style scoped>
.selected-mode-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: calc(100% - 24px);
  margin: 8px 12px 0;
  padding: 4px 6px 4px 8px;
  border: 1px solid rgba(14, 165, 233, 0.28);
  border-radius: 999px;
  background: rgba(14, 165, 233, 0.08);
  color: var(--primary-color, #0ea5e9);
  font-size: 12px;
}

.selected-mode-chip > svg {
  width: 14px;
  height: 14px;
  flex: 0 0 auto;
}

.selected-mode-command {
  font-weight: 700;
}

.selected-mode-label {
  color: var(--text-secondary, #64748b);
}

.selected-mode-chip button {
  display: grid;
  place-items: center;
  width: 18px;
  height: 18px;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: currentColor;
  cursor: pointer;
}

.selected-mode-chip button:hover,
.selected-mode-chip button:focus-visible {
  background: rgba(14, 165, 233, 0.14);
  outline: none;
}

.selected-mode-chip button svg {
  width: 11px;
  height: 11px;
}
</style>
