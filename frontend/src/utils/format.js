/**
 * 通用格式化工具（2026-10-20 从 ChatInput.vue 抽出，供上传区与额度文案共用）。
 */

/**
 * 字节数转可读大小（B / KB / MB，保留 1 位小数）。
 *
 * @param {number} bytes 字节数
 * @returns {string} 例：1.5 KB
 */
export function formatSize(bytes) {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}
