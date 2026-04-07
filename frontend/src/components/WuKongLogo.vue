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
  <div class="wukong-logo" :style="{ width: iconSize, height: iconSize }">
    <svg viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
      <!-- Background -->
      <rect width="48" height="48" fill="transparent"/>
      
      <!-- WuKong text centered -->
      <text x="24" y="18" font-size="14" fill="#e74c3c" font-family="Arial, sans-serif" text-anchor="middle" font-weight="bold">Wu</text>
      
      <!-- Separator line -->
      <line x1="4" y1="24" x2="44" y2="24" stroke="#cccccc" stroke-width="1"/>
      
      <text x="24" y="38" font-size="14" fill="#27ae60" font-family="Arial, sans-serif" text-anchor="middle" font-weight="bold">Kong</text>
    </svg>
  </div>
</template>

<style scoped>
.wukong-logo {
  display: flex;
  align-items: center;
  justify-content: center;
}

.wukong-logo svg {
  width: 100%;
  height: 100%;
}
</style>
