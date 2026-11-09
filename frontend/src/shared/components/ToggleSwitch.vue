<!--
  通用开关（技能启用/停用）。原生 button + role="switch"，键盘可操作，带忙碌态。
-->
<template>
  <button
    type="button"
    class="toggle-switch"
    :class="{ on: modelValue }"
    role="switch"
    :aria-checked="modelValue ? 'true' : 'false'"
    :aria-label="ariaLabel || (modelValue ? '停用' : '启用')"
    :disabled="disabled || loading"
    @click="$emit('change', !modelValue)"
  >
    <span class="toggle-knob">
      <span v-if="loading" class="toggle-spinner"></span>
    </span>
  </button>
</template>

<script setup>
defineProps({
  modelValue: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  ariaLabel: { type: String, default: '' },
})

defineEmits(['change'])
</script>

<style scoped>
.toggle-switch {
  position: relative;
  width: 38px;
  height: 22px;
  margin: 0;
  padding: 0;
  flex-shrink: 0;
  border: none;
  border-radius: 11px;
  background: #cbd5e1;
  cursor: pointer;
  transition: background-color 0.18s ease;
}

.toggle-switch.on {
  background: var(--accent-color, #0ea5e9);
}

.toggle-switch:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.toggle-switch:focus-visible {
  outline: 2px solid var(--accent-color, #0ea5e9);
  outline-offset: 2px;
}

.toggle-knob {
  position: absolute;
  top: 3px;
  left: 3px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.25);
  display: flex;
  align-items: center;
  justify-content: center;
  transition: transform 0.18s ease;
}

.toggle-switch.on .toggle-knob {
  transform: translateX(16px);
}

.toggle-spinner {
  width: 10px;
  height: 10px;
  border: 2px solid rgba(14, 165, 233, 0.35);
  border-top-color: var(--accent-color, #0ea5e9);
  border-radius: 50%;
  animation: toggle-spin 0.7s linear infinite;
}

@keyframes toggle-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .toggle-switch,
  .toggle-knob,
  .toggle-spinner {
    transition: none;
    animation: none;
  }
}

html[data-theme="dark"] .toggle-switch {
  background: #475569;
}
</style>
