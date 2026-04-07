<script setup>
import { computed } from 'vue'

const props = defineProps({
  size: {
    type: [Number, String],
    default: 36
  }
})

const iconSize = computed(() => {
  return typeof props.size === 'number' ? `${props.size}px` : props.size
})
</script>

<template>
  <div class="cq-logo" :style="{ width: iconSize, height: iconSize }">
    <svg viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="cqGradient" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" style="stop-color:#0ea5e9"/>
          <stop offset="100%" style="stop-color:#8b5cf6"/>
        </linearGradient>
      </defs>
      <rect x="4" y="4" width="40" height="40" rx="10" fill="url(#cqGradient)"/>
      <path d="M10 36L16 28L22 32L28 20L34 24L38 16" stroke="white" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
      <circle cx="16" cy="28" r="2" fill="white"/>
      <circle cx="22" cy="32" r="2" fill="white"/>
      <circle cx="28" cy="20" r="2" fill="white"/>
      <circle cx="34" cy="24" r="2" fill="white"/>
      <circle cx="38" cy="16" r="3" fill="#fef3c7" stroke="white" stroke-width="1.5"/>
      <path d="M36 10L38 8L40 10" stroke="white" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
      <path d="M38 8V14" stroke="white" stroke-width="1.5" stroke-linecap="round"/>
    </svg>
  </div>
</template>

<style scoped>
.cq-logo {
  display: flex;
  align-items: center;
  justify-content: center;
}

.cq-logo svg {
  width: 100%;
  height: 100%;
}
</style>
