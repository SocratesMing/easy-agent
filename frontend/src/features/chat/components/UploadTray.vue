<template>
  <div class="uploaded-files">
    <!-- 已用额度（大小 + 数量）常驻展示；失败文件不占额度，括号内单独说明 -->
    <div v-if="limitsText" class="uploaded-meta">
      <span class="uploaded-limits" :class="{ full: limitsFull }">{{ limitsText }}</span>
    </div>
    <div class="uploaded-files-list">
      <div v-for="(file, index) in files" :key="index" class="uploaded-file">
        <div class="file-icon">
          <FileIcon :filename="file.filename" :size="26" />
        </div>
        <div class="file-info">
          <span class="file-name" :title="file.filename">{{ file.filename }}</span>
          <div class="file-meta">
            <span class="file-size">{{ formatSize(file.size) }}</span>
            <div v-if="file.uploadStatus === 'uploading'" class="upload-progress">
              <div class="progress-bar">
                <div class="progress-fill" :style="{ width: file.uploadProgress + '%' }"></div>
              </div>
              <span class="progress-text">{{ file.uploadProgress }}%</span>
            </div>
            <span v-else-if="file.uploadStatus === 'completed'" class="upload-status completed">上传完成</span>
            <span v-else-if="file.uploadStatus === 'pending'" class="upload-status pending">发送时上传</span>
            <template v-else-if="file.uploadStatus === 'error'">
              <span class="upload-status error" :title="file.errorMessage || '上传失败'">上传失败</span>
              <button
                v-if="file.retryable"
                type="button"
                class="retry-file-btn"
                :aria-label="`重新上传 ${file.filename}`"
                title="重新上传"
                @click="$emit('retry', index)"
              >重试</button>
            </template>
          </div>
        </div>
        <button
          type="button"
          class="remove-file-btn"
          :aria-label="`删除文件 ${file.filename}`"
          title="删除文件"
          @click="$emit('remove', index)"
        >
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="18" y1="6" x2="6" y2="18"></line>
            <line x1="6" y1="6" x2="18" y2="18"></line>
          </svg>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import FileIcon from '../../../shared/components/FileIcon.vue'
import { formatSize } from '../../../utils/format.js'

defineProps({
  files: { type: Array, default: () => [] },
  limitsText: { type: String, default: '' },
  limitsFull: { type: Boolean, default: false },
})

defineEmits(['remove', 'retry'])
</script>

<style scoped>
.uploaded-files {
  display: block;
  padding: 12px 16px;
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border-color);
}

.uploaded-meta {
  width: 100%;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
}

.uploaded-limits {
  font-size: 11.5px;
  color: var(--text-secondary);
}

.uploaded-limits.full {
  color: #b45309;
  color: var(--warning-text, #b45309);
}

.uploaded-files-list {
  width: 100%;
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}

.uploaded-file {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  /* 右侧 26px 预留给卡片内右上角的 × 按钮，防止文件名顶到 × 下面 */
  padding: 6px 26px 6px 10px;
  border-radius: 12px;
  /* 自适应等分：一行最多放 3 个（220px 起步、300px 封顶），宽度随容器伸缩 */
  flex: 1 1 220px;
  max-width: 300px;
  min-width: 0;
  box-sizing: border-box;
  height: 48px;
  overflow: hidden;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
  transition: all 0.2s ease;
}

.uploaded-file:hover {
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.08);
  border-color: var(--border-color);
}

.file-icon {
  width: 30px;
  height: 30px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.file-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.file-name {
  font-size: 12px;
  line-height: 16px;
  font-weight: 500;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.file-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 16px;
}

.file-size {
  font-size: 11px;
  line-height: 16px;
  color: var(--text-secondary);
}

.upload-progress {
  display: flex;
  align-items: center;
  gap: 6px;
}

.progress-bar {
  width: 60px;
  height: 4px;
  background: var(--bg-tertiary);
  border-radius: 2px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #0ea5e9, #06b6d4);
  border-radius: 2px;
  transition: width 0.3s ease;
}

.progress-text {
  font-size: 10px;
  line-height: 16px;
  color: var(--text-secondary);
  min-width: 35px;
}

.upload-status {
  font-size: 10px;
  line-height: 14px;
  font-weight: 500;
  padding: 1px 6px;
  border-radius: 8px;
}

.upload-status.completed {
  color: #15803d;
  background: #dcfce7;
}

.upload-status.pending {
  color: #64748b;
  background: #f1f5f9;
}

.upload-status.error {
  color: #b91c1c;
  background: #fee2e2;
}

.retry-file-btn {
  flex-shrink: 0;
  padding: 0 6px;
  border: 1px solid #fca5a5;
  border-radius: 8px;
  background: #ffffff;
  background: var(--bg-secondary);
  color: #b91c1c;
  font-size: 10px;
  line-height: 14px;
  cursor: pointer;
}

.retry-file-btn:hover {
  background: #fee2e2;
}

.remove-file-btn {
  position: absolute;
  top: 5px;
  right: 5px;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  padding: 0;
  background: #334155;
  border: 2px solid #ffffff;
  border: 2px solid var(--bg-primary);
  border-radius: 50%;
  cursor: pointer;
  transition: background 0.2s ease;
}

.remove-file-btn:hover {
  background: #0f172a;
}

.remove-file-btn svg {
  width: 9px;
  height: 9px;
  color: #ffffff;
}
</style>
