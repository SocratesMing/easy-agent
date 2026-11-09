<template>
  <div class="sidebar-resizer" role="separator" tabindex="0" aria-label="调整会话侧栏宽度"
    aria-orientation="vertical" :aria-valuenow="value" :aria-valuemin="220" :aria-valuemax="440"
    @pointerdown.prevent="start" @keydown="onKey" />
</template>

<script>
const STORAGE_KEY = 'easyagent.layout.sidebar'
export default {
  props: { value: { type: Number, default: 280 } },
  data: () => ({ startX: null, startWidth: 280 }),
  mounted() {
    try {
      const saved = Number(localStorage.getItem(STORAGE_KEY))
      if (saved > 0) this.update(saved)
    } catch (_) { /* Storage may be disabled by browser policy. */ }
  },
  beforeDestroy() { this.finish() },
  methods: {
    update(width) { this.$emit('input', Math.max(220, Math.min(440, width))) },
    start(event) {
      if (event.button !== 0) return
      this.startX = event.clientX
      this.startWidth = this.value
      window.addEventListener('pointermove', this.move)
      window.addEventListener('pointerup', this.finish)
      window.addEventListener('pointercancel', this.finish)
    },
    move(event) { if (this.startX !== null) this.update(this.startWidth + event.clientX - this.startX) },
    finish() {
      if (this.startX !== null) this.save()
      this.startX = null
      window.removeEventListener('pointermove', this.move)
      window.removeEventListener('pointerup', this.finish)
      window.removeEventListener('pointercancel', this.finish)
    },
    save() { try { localStorage.setItem(STORAGE_KEY, String(this.value)) } catch (_) {} },
    onKey(event) {
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return
      event.preventDefault()
      this.update(this.value + (event.key === 'ArrowRight' ? 16 : -16))
      this.$nextTick(this.save)
    },
  },
}
</script>

<style scoped>
.sidebar-resizer { width: 6px; flex: 0 0 6px; cursor: col-resize; touch-action: none; background: var(--bg-primary, white); }
.sidebar-resizer:hover, .sidebar-resizer:focus { background: var(--primary-color, #0ea5e9); outline: none; }
</style>
