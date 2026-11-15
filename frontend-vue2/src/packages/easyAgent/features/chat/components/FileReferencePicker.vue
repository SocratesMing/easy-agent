<template>
  <div v-if="open" class="reference-picker" aria-label="当前会话文件引用" @mousedown.prevent @click.stop>
    <div v-if="path" class="reference-heading">
      <button type="button" class="reference-back" aria-label="返回上级目录" @click="goBack"><svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="m9.5 4-4 4 4 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg></button>
      <span class="reference-location" :title="path">{{ path }}/</span>
      <button type="button" class="reference-close" aria-label="关闭文件选择" @click="$emit('close')"><svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="m4 4 8 8m0-8-8 8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" /></svg></button>
    </div>
    <div v-if="loading" class="reference-status" role="status">正在加载…</div>
    <div v-else-if="error" class="reference-status reference-error" role="alert">{{ error }}</div>
    <div v-else-if="!filtered.length" class="reference-status" role="status">{{ sessionId ? '没有匹配的文件' : '当前会话暂无文件' }}</div>
    <div v-for="(item, index) in filtered" :key="item.path" class="reference-row" :class="{ active: index === active }" @mouseenter="active = index">
      <button type="button" class="reference-name" :title="item.path" :aria-label="'引用 ' + item.name" @click="$emit('select', item)">
        <FileReferenceIcon :path="item.path" :type="item.type" />
        <span class="reference-details"><span class="reference-filename">{{ item.name }}</span><span class="reference-parent">{{ parentPath(item.path) }}</span></span>
      </button>
      <button v-if="item.type === 'directory'" type="button" class="reference-enter" :aria-label="'进入 ' + item.name" @click="navigate(item.path)"><svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="m6 4 4 4-4 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg></button>
    </div>
  </div>
</template>
<script>
import FileReferenceIcon from './FileReferenceIcon.vue'
export default {
  components: { FileReferenceIcon },
  props: { loadTree: { type: Function, required: true }, open: Boolean, sessionId: { type: String, default: '' }, query: { type: String, default: '' } },
  data() { return { path: '', items: [], loading: false, error: '', active: 0, generation: 0 } },
  computed: { filtered() { const q = this.query.toLowerCase(); return this.items.filter(item => item.name.toLowerCase().includes(q)) } },
  watch: {
    open: { immediate: true, handler(value) { if (value) this.load(''); else { this.generation++; this.loading = false } } },
    sessionId() { this.generation++; this.items = []; this.path = ''; this.loading = false; if (this.open) this.load('') },
    query() { this.active = 0 }
  },
  methods: {
    parentPath(path) { return path.includes('/') ? path.slice(0, path.lastIndexOf('/') + 1) : '当前会话/' },
    async load(path) {
      const generation = ++this.generation
      this.path = path; this.items = []; this.error = ''; this.active = 0; this.loading = false
      if (!this.sessionId) return
      this.loading = true
      try {
        const result = await this.loadTree(path, this.sessionId)
        if (generation === this.generation) this.items = result.items || []
      } catch (error) { if (generation === this.generation) this.error = error.message || '加载失败' }
      finally { if (generation === this.generation) this.loading = false }
    },
    navigate(path) { this.$emit('navigate'); return this.load(path) },
    goBack() { return this.navigate(this.path.split('/').slice(0, -1).join('/')) },
    key(event) {
      if (event.key === 'Escape') { event.preventDefault(); this.$emit('close'); return true }
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault(); const n = this.filtered.length
        if (n) this.active = (this.active + (event.key === 'ArrowDown' ? 1 : -1) + n) % n
        this.$nextTick(() => {
          const row = this.$el.querySelector('.reference-row.active')
          if (row) row.scrollIntoView({ block: 'nearest' })
        })
        return true
      }
      if (event.key === 'ArrowRight' && this.filtered[this.active]?.type === 'directory') {
        event.preventDefault(); this.navigate(this.filtered[this.active].path); return true
      }
      if (event.key === 'ArrowLeft' && this.path) { event.preventDefault(); this.goBack(); return true }
      if ((event.key === 'Enter' || event.key === 'Tab') && !event.shiftKey) {
        event.preventDefault(); if (this.filtered[this.active]) this.$emit('select', this.filtered[this.active]); return true
      }
      return false
    }
  }
}
</script>
<style scoped>
.reference-picker {
  position: fixed; z-index: 1000; box-sizing: border-box; container-type: inline-size;
  max-height: 320px; overflow: auto; overscroll-behavior: contain; padding: 8px;
  background: var(--bg-surface, var(--bg-primary, #fff)); color: var(--text-primary, #1f2937);
  border: 1px solid #dceefe; border-color: color-mix(in srgb, #309ff7 19%, var(--bg-surface, var(--bg-primary, #fff)));
  border-radius: 18px; box-shadow: 0 8px 28px rgb(35 89 133 / 7%);
  scrollbar-width: thin; scrollbar-color: var(--border-color, #dce6ee) transparent;
}
.reference-picker button { font: inherit; border: 0; background: transparent; color: inherit; cursor: pointer; }
.reference-picker button:focus-visible { outline: 2px solid #269cf5; outline-offset: -2px; border-radius: 8px; }
.reference-heading { display: flex; align-items: center; gap: 6px; min-height: 30px; padding: 0 3px 5px; color: var(--text-secondary, #687382); }
.reference-heading svg, .reference-enter svg { display: block; width: 16px; height: 16px; }
.reference-back, .reference-close { display: grid; place-items: center; width: 26px; height: 26px; padding: 0; border-radius: 6px; }
.reference-back:hover, .reference-close:hover { background: var(--bg-secondary, #f4f8fc); }
.reference-picker .reference-location { color: var(--text-secondary, #687382) !important; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; }
.reference-row { display: flex; align-items: center; min-height: 44px; border-radius: 11px; }
.reference-row.active { background: #def1ff; background: color-mix(in srgb, #259fff 15%, var(--bg-surface, var(--bg-primary, #fff))); }
.reference-name { display: flex; align-items: center; flex: 1; min-width: 0; gap: 10px; min-height: 44px; padding: 9px 12px; text-align: left; }
.reference-details { display: flex; align-items: baseline; gap: 12px; flex: 1; min-width: 0; }
.reference-filename { flex: 0 1 auto; min-width: 0; max-width: 60%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 14px; line-height: 22px; font-weight: 500; }
.reference-picker .reference-parent { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-secondary, #687382) !important; font-size: 12px; line-height: 20px; }
.reference-enter { display: grid; place-items: center; flex: 0 0 30px; height: 30px; margin-right: 6px; border-radius: 7px; color: var(--text-secondary, #687382); }
.reference-enter:hover { background: rgb(37 159 255 / 12%); }
.reference-status { padding: 22px 12px; text-align: center; color: var(--text-secondary, #687382); font-size: 13px; }
.reference-error { color: var(--error-color, #d95858); }
/* 旧深色主题会强制统一 span 文字颜色，路径仍保留次级文字层次。 */
@container (max-width: 440px) {
  .reference-details { flex-direction: column; align-items: stretch; gap: 0; }
  .reference-filename { max-width: 100%; }
  .reference-parent { font-size: 11px; line-height: 16px; }
  .reference-name { padding: 8px 10px; }
}
</style>
