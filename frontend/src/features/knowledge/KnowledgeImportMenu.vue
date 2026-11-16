<template>
  <div class="ke-import-wrap">
    <button type="button" class="ke-button primary upload" title="选择文件或文件夹导入">
      <IconUpload />导入资料<IconChevronDown class="ke-import-caret" />
    </button>
    <div class="ke-import-menu">
      <label class="ke-import-option"><IconFileText />选择文件<input type="file" multiple hidden @change="$emit('files', $event)" /></label>
      <label class="ke-import-option"><IconFolder />选择文件夹（含子目录）<input type="file" webkitdirectory hidden @change="$emit('folder', $event)" /></label>
    </div>
  </div>
</template>

<script>
import { IconChevronDown, IconFileText, IconFolder, IconUpload } from './icons.js'

export default {
  components: { IconChevronDown, IconFileText, IconFolder, IconUpload },
  emits: ['files', 'folder'],
}
</script>

<style scoped>
.ke-import-wrap{position:relative;display:inline-flex}
.ke-import-wrap button{font:inherit}
/* 与父级 .ke-shell svg 同规格：图标尺寸由外层统一控制，选项图标在其后覆盖 */
.ke-import-wrap svg{width:16px;height:16px;flex:none}
.ke-import-caret{width:13px!important;height:13px;transition:transform .15s ease}
.ke-import-wrap:hover .ke-import-caret,.ke-import-wrap:focus-within .ke-import-caret{transform:rotate(180deg)}
.ke-import-menu{position:absolute;top:calc(100% + 6px);right:0;z-index:60;min-width:180px;display:none;flex-direction:column;padding:5px;background:#fff;border:1px solid #e3e8e4;border-radius:11px;box-shadow:0 14px 36px rgba(23,35,27,.16)}
.ke-import-wrap:hover .ke-import-menu,.ke-import-wrap:focus-within .ke-import-menu{display:flex}
.ke-import-option{display:flex;align-items:center;gap:8px;padding:8px 10px;border-radius:8px;color:#3d4640;font-size:11px;cursor:pointer;white-space:nowrap}
.ke-import-option:hover{background:#f2f8f5;color:var(--ke-accent-dark)}
.ke-import-option svg{width:14px;height:14px;color:#69726c}
/* 按钮沿用宿主 .ke-button 的定义：scoped 样式不跨组件，这里复制同一份规则 */
.ke-button{height:34px;border:1px solid var(--border-color);background:var(--bg-secondary);border-radius:9px;padding:0 12px;display:inline-flex;align-items:center;justify-content:center;gap:6px;color:var(--text-secondary);font-size:12px;cursor:pointer;transition:.18s ease;box-sizing:border-box}
.ke-button:hover{background:var(--bg-tertiary);border-color:var(--border-color)}
.ke-button.primary{background:var(--ke-accent);border-color:var(--ke-accent);color:#fff;box-shadow:0 3px 10px color-mix(in srgb,var(--ke-accent) 22%,transparent)}
.ke-button.primary:hover{background:var(--ke-accent-dark);border-color:var(--ke-accent-dark);transform:translateY(-1px)}
</style>
