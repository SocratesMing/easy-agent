<template>
  <svg class="reference-icon" :class="kind" viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <template v-if="type === 'directory'">
      <path d="M2 6.5A2.5 2.5 0 0 1 4.5 4H9l2.2 2.5H20A2 2 0 0 1 22 8.5V18a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2Z" fill="currentColor" opacity=".65" />
      <path d="M2 9a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2Z" fill="currentColor" />
    </template>
    <template v-else>
      <path d="M6 2h8l5 5v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2Z" fill="currentColor" />
      <path d="M14 2v4a1 1 0 0 0 1 1h4Z" fill="white" opacity=".5" />
      <path v-if="kind === 'code'" d="m9 11-2 3 2 3m5-6 2 3-2 3" stroke="white" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" />
      <g v-else stroke="white" stroke-width="1.6" stroke-linecap="round">
        <path d="M8 12h7M8 15h7M8 18h4" />
      </g>
    </template>
  </svg>
</template>
<script>
export default {
  props: { path: { type: String, default: '' }, type: { type: String, default: 'file' } },
  computed: {
    kind() {
      if (this.type === 'directory') return 'folder'
      const ext = this.path.split('.').pop().toLowerCase()
      if (['html', 'htm', 'js', 'ts', 'vue', 'py', 'json', 'css', 'sh', 'sql', 'xml'].includes(ext)) return 'code'
      if (['xls', 'xlsx', 'csv'].includes(ext)) return 'sheet'
      if (ext === 'pdf') return 'pdf'
      if (['png', 'jpg', 'jpeg', 'webp', 'gif', 'svg'].includes(ext)) return 'image'
      return 'document'
    }
  }
}
</script>
<style scoped>
.reference-icon { width: 20px; height: 20px; flex: 0 0 auto; color: #2e8cff; }
.folder { color: #2b9ff6; }
.code { color: #9272f7; }
.sheet { color: #29a575; }
.pdf { color: #ec6868; }
.image { color: #ad78d8; }
</style>
