<template>
  <div class="enhancement-actions">
    <span v-if="state.pending" role="status" aria-live="polite" class="enhancement-progress">增强中…</span>
    <span v-else-if="state.notice" role="status" class="enhancement-notice">{{ state.notice }}</span>
    <button type="button" class="enhancement-button" :class="{ pending: state.pending }" title="增强提示词" aria-label="增强提示词"
      :disabled="!enabled" :aria-busy="state.pending ? 'true' : 'false'" @click="$emit('enhance')">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">
        <path d="m4 20 13-13 3 3L7 23M14 10l3 3M5 2v6M2 5h6M18 1v4M16 3h4M20 16v6M17 19h6" />
      </svg>
    </button>
  </div>
</template>
<script>
export default {
  props: { enabled: Boolean, state: { type: Object, required: true } },
}
</script>
<style scoped>
.enhancement-actions { display: flex; align-items: center; gap: 5px; }
.enhancement-button { display: flex; align-items: center; justify-content: center; width: 30px; height: 30px; padding: 5px; border: 0; border-radius: 7px; background: transparent; color: #64748b; cursor: pointer; }
.enhancement-button:hover:not(:disabled) { color: #4f46e5; background: #eef2ff; }
.enhancement-button:disabled { opacity: .4; cursor: default; }
.enhancement-button.pending:disabled { opacity: 1; color: #4f46e5; background: #eef2ff; animation: enhancement-breathe 1.5s ease-in-out infinite; }
.enhancement-progress { color: #6366f1; font-size: 11px; white-space: nowrap; }
@keyframes enhancement-breathe { 0%, 100% { background-color: #eef2ff; } 50% { background-color: #e0e7ff; } }
@media (prefers-reduced-motion: reduce) { .enhancement-button.pending:disabled { animation: none; } }
.enhancement-button:focus-visible { outline: 2px solid #6366f1; outline-offset: 2px; }
.enhancement-button svg { width: 20px; height: 20px; }
.enhancement-notice { max-width: 180px; font-size: 11px; color: #64748b; overflow-wrap: anywhere; }
@media (max-width: 480px) { .enhancement-notice { max-width: 90px; } }
</style>
