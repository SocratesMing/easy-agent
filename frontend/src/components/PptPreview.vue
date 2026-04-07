<template>
  <div class="office-preview-container">
    <div class="office-placeholder">
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
        <polyline points="14 2 14 8 20 8"></polyline>
        <rect x="8" y="12" width="8" height="6" rx="1" fill="#f97316" stroke="none"/>
      </svg>
      <span class="office-title">{{ fileType }} 文件</span>
      <span class="office-desc">暂不支持在线预览</span>
      <a :href="fileUrl" download class="download-btn">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
          <polyline points="7 10 12 15 17 10"></polyline>
          <line x1="12" y1="15" x2="12" y2="3"></line>
        </svg>
        下载文件
      </a>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  fileUrl: {
    type: String,
    default: ''
  },
  fileType: {
    type: String,
    default: 'PPT'
  }
})
</script>

<style scoped>
.office-preview-container {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #fafafa;
}

.office-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 32px;
}

.office-placeholder svg {
  width: 64px;
  height: 64px;
  color: #f97316;
}

.office-title {
  font-size: 16px;
  font-weight: 500;
  color: #374151;
}

.office-desc {
  font-size: 13px;
  color: #9ca3af;
}

.download-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 20px;
  background: #166534;
  color: white;
  border-radius: 6px;
  text-decoration: none;
  font-size: 13px;
  transition: background 0.15s ease;
  margin-top: 8px;
}

.download-btn:hover {
  background: #15803d;
}

.download-btn svg {
  width: 16px;
  height: 16px;
}
</style>
