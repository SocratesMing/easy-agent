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
  <div class="easy-logo" :style="{ width: iconSize, height: iconSize }">
    <svg viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
      <!-- Background circle -->
      <circle cx="24" cy="24" r="22" fill="url(#gradient)" />
      
      <!-- E letter -->
      <path d="M14 16h14M14 24h12M14 32h14M14 16v16" stroke="white" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
      
      <!-- Dot -->
      <circle cx="34" cy="16" r="2" fill="white"/>
      
      <!-- Gradient definition -->
      <defs>
        <linearGradient id="gradient" x1="0" y1="0" x2="48" y2="48">
          <stop offset="0%" stop-color="#667eea"/>
          <stop offset="100%" stop-color="#764ba2"/>
        </linearGradient>
      </defs>
    </svg>
  </div>
</template>

<style scoped>
.easy-logo {
  display: flex;
  align-items: center;
  justify-content: center;
}

.easy-logo svg {
  width: 100%;
  height: 100%;
}
</style>
