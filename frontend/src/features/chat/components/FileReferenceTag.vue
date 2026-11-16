<template>
  <component :is="removable ? 'button' : 'span'" :type="removable ? 'button' : null" class="file-reference-tag" :class="{ removable }" :title="item.path + (item.type === 'directory' ? '/' : '')" :aria-label="removable ? '移除引用 ' + item.path : null" @click="$emit('remove')">
    <FileReferenceIcon :path="item.path" :type="item.type" />
    <span class="reference-label">{{ item.path.split('/').pop() }}</span>
    <svg v-if="removable" class="reference-remove" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="m5 5 6 6m0-6-6 6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" /></svg>
  </component>
</template>
<script>
import FileReferenceIcon from './FileReferenceIcon.vue'
export default {
  components: { FileReferenceIcon },
  props: { item: { type: Object, required: true }, removable: Boolean }
}
</script>
<style scoped>
.file-reference-tag { display: inline-flex; align-items: center; gap: 5px; box-sizing: border-box; max-width: 100%; padding: 4px 7px; border: 0; border-radius: 6px; background: #e5f3ff; background: color-mix(in srgb, #259fff 12%, var(--bg-primary, #fff)); color: var(--text-primary, #263b4e); font: inherit; font-size: 12px; line-height: 18px; vertical-align: middle; }
.file-reference-tag .reference-icon { width: 16px; height: 16px; }
.reference-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.removable { cursor: pointer; }
.removable:hover { background: color-mix(in srgb, #259fff 20%, var(--bg-primary, #fff)); }
.removable:focus-visible { outline: 2px solid #269cf5; outline-offset: 2px; }
.reference-remove { width: 14px; height: 14px; flex: 0 0 auto; color: var(--text-secondary, #6b7d8b); }
</style>
