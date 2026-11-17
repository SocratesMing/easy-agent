<template>
  <div class="version-panel">
    <div class="panel-header">
      <h3>版本信息</h3>
    </div>
    <div v-if="loading" class="loading-state"><div class="spinner"></div></div>
    <dl v-else class="version-list">
      <template v-for="row in rows" :key="row.key">
        <dt>{{ row.label }}</dt>
        <dd><code>{{ row.value }}</code></dd>
      </template>
    </dl>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { getVersionInfo } from '../services/version.js'

const loading = ref(true)
const info = ref({})

const rows = computed(() => {
  const value = info.value || {}
  return [
    { key: 'name', label: '名称', value: value.name },
    { key: 'version', label: '版本号', value: value.version },
  ].filter((row) => row.value)
})

onMounted(async () => {
  try {
    info.value = await getVersionInfo()
  } catch (error) {
    console.error('加载版本信息失败:', error)
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.panel-header h3 {
  margin: 0 0 16px 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px;
}

.spinner {
  width: 28px;
  height: 28px;
  border: 3px solid #e2e8f0;
  border-top-color: #0ea5e9;
  border-radius: 50%;
  animation: version-spin 1s linear infinite;
}

@keyframes version-spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.version-list {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 10px 24px;
  margin: 0;
  font-size: 14px;
}

.version-list dt {
  color: var(--text-secondary);
}

.version-list dd {
  margin: 0;
  word-break: break-all;
}
</style>
