<template>
  <section ref="cardRef" class="pq-card" :class="{ 'has-error': touched && missing, multiple: question.multiple }" :aria-label="question.title" @keydown="onKeydown">
    <header class="pq-head">
      <h3 class="pq-title">{{ question.title }}</h3>
      <div class="pq-meta">
        <span class="pq-type">{{ question.multiple ? '多选' : '单选' }}</span>
        <span class="pq-required">{{ question.required ? '必选' : '可选' }}</span>
      </div>
    </header>
    <p v-if="question.description" class="pq-description">{{ question.description }}</p>
    <div class="pq-options">
      <label v-for="(option, optionIndex) in orderedOptions" :key="option.id" class="pq-option" :class="{ selected: isSelected(option.id) }">
        <input class="pq-native" :type="question.multiple ? 'checkbox' : 'radio'" :name="'pq-' + question.id" :value="option.id" :checked="isSelected(option.id)" :disabled="busy" @change="selectOption(option.id)" />
        <span class="pq-option-body" :title="option.description || option.label">
          <span class="pq-option-number" aria-hidden="true">{{ optionIndex + 1 }}</span>
          <span class="pq-option-content">
            <span class="pq-option-label">{{ option.label }}</span>
            <span v-if="option.description" class="pq-option-description">{{ option.description }}</span>
          </span>
          <span v-if="option.recommended" class="pq-recommend">推荐</span>
          <span class="pq-check" aria-hidden="true"></span>
        </span>
      </label>
    </div>
    <p v-if="touched && missing" class="pq-error" role="alert">请选择{{ question.multiple ? '至少一个候选项' : '一个候选项' }}</p>
    <label v-if="showNote" class="pq-note">
      <span>补充要求 <small>可选</small></span>
      <textarea v-model="note" maxlength="2000" rows="2" :disabled="busy" placeholder="例如：先做第一阶段，保留后续扩展空间"></textarea>
    </label>
    <footer class="pq-footer">
      <span class="pq-progress">1–9 选择 <span>· ↑↓ 移动 · Enter 确认</span></span>
      <button type="submit" class="pq-next" :disabled="busy" @click.prevent="submit">
        <span v-if="busy" class="pq-spinner" aria-hidden="true"></span>
        <span>{{ busy ? '正在继续…' : submitLabel }}</span>
      </button>
    </footer>
  </section>
</template>

<script>
export default {
  name: 'PlanQuestionCard',
  props: {
    question: { type: Object, required: true },
    index: { type: Number, default: 0 },
    total: { type: Number, default: 1 },
    initialAnswer: { type: [String, Array], default: null },
    initialNote: { type: String, default: '' },
    showNote: { type: Boolean, default: false },
    submitLabel: { type: String, default: '确认并继续' },
    busy: { type: Boolean, default: false },
  },
  data() {
    return { selected: null, note: '', touched: false }
  },
  computed: {
    orderedOptions() {
      const options = this.question.options || []
      return [
        ...options.filter(option => option.recommended),
        ...options.filter(option => !option.recommended),
      ]
    },
    missing() {
      if (!this.question.required) return false
      return this.question.multiple
        ? !Array.isArray(this.selected) || this.selected.length === 0
        : !this.selected
    },
  },
  watch: {
    'question.id': {
      immediate: true,
      handler() { this.reset() },
    },
  },
  mounted() { this.reset() },
  methods: {
    reset() {
      this.selected = this.question.multiple
        ? (Array.isArray(this.initialAnswer) ? [...this.initialAnswer] : [])
        : (this.initialAnswer || null)
      this.note = this.initialNote || ''
      this.touched = false
      this.$nextTick(() => {
        const first = this.$refs.cardRef && this.$refs.cardRef.querySelector('input')
        if (first) first.focus({ preventScroll: true })
      })
    },
    isSelected(optionId) {
      return this.question.multiple
        ? Array.isArray(this.selected) && this.selected.includes(optionId)
        : this.selected === optionId
    },
    selectOption(optionId) {
      if (this.busy) return
      if (!this.question.multiple) {
        this.selected = optionId
        return
      }
      const next = Array.isArray(this.selected) ? [...this.selected] : []
      const index = next.indexOf(optionId)
      if (index === -1) next.push(optionId)
      else next.splice(index, 1)
      this.selected = next
    },
    optionInputs() {
      return Array.from((this.$refs.cardRef && this.$refs.cardRef.querySelectorAll('.pq-native')) || [])
    },
    focusOption(index) {
      const inputs = this.optionInputs()
      if (!inputs.length) return
      const target = inputs[(index + inputs.length) % inputs.length]
      target.focus({ preventScroll: true })
      if (!this.question.multiple) this.selectOption(target.value)
    },
    onKeydown(event) {
      if (this.busy) return
      const target = event.target
      if (target && (target.tagName === 'TEXTAREA' || target.closest('button'))) return
      if (/^[1-9]$/.test(event.key)) {
        const option = this.orderedOptions[Number(event.key) - 1]
        if (option) {
          event.preventDefault()
          this.selectOption(option.id)
          this.focusOption(Number(event.key) - 1)
        }
        return
      }
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault()
        const inputs = this.optionInputs()
        const current = inputs.indexOf(document.activeElement)
        this.focusOption((current < 0 ? 0 : current) + (event.key === 'ArrowDown' ? 1 : -1))
        return
      }
      if (event.key === 'Enter') {
        event.preventDefault()
        this.submit()
        return
      }
      if (event.key === 'Escape') {
        event.preventDefault()
        this.$emit('back')
      }
    },
    submit() {
      this.touched = true
      if (this.missing || this.busy) return
      this.$emit('next', {
        answer: Array.isArray(this.selected) ? [...this.selected] : this.selected,
        note: this.note.trim(),
      })
    },
  },
}
</script>

<style scoped>
.pq-card { width: 100%; box-sizing: border-box; padding: 10px 11px 9px; border: 1px solid var(--border-color, #e2e8f0); border-radius: 8px; background: var(--bg-primary, #fff); color: var(--text-primary, #1e293b); }
.pq-card.has-error { border-color: #ef4444; }
.pq-head { display: flex; align-items: center; gap: 8px; }
.pq-meta { display: flex; align-items: center; flex: 0 0 auto; gap: 5px; }
.pq-title { flex: 1; min-width: 0; margin: 0; overflow: hidden; font-size: 14px; line-height: 1.35; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.pq-type, .pq-required { padding: 1px 5px; border-radius: 4px; font-size: 9px; font-weight: 650; line-height: 1.45; white-space: nowrap; }
.pq-type { background: rgba(14, 165, 233, 0.08); color: var(--primary-color, #0ea5e9); }
.pq-required { background: var(--bg-secondary, #f8fafc); color: var(--text-secondary, #64748b); }
.pq-description { margin: 4px 0 0; font-size: 11px; line-height: 1.45; color: var(--text-secondary, #64748b); white-space: pre-wrap; }
.pq-options { display: flex; flex-direction: column; gap: 2px; margin-top: 8px; }
.pq-option { position: relative; display: block; min-width: 0; cursor: pointer; }
.pq-native { position: absolute; opacity: 0; pointer-events: none; }
.pq-option-body { display: flex; align-items: center; gap: 7px; box-sizing: border-box; min-height: 30px; padding: 5px 7px; border: 0; border-radius: 6px; background: transparent; transition: background-color 0.15s; }
.pq-option-number { display: grid; place-items: center; flex: 0 0 16px; width: 16px; height: 16px; border: 0; border-radius: 4px; background: var(--bg-secondary, #f8fafc); color: var(--text-secondary, #64748b); font-size: 9px; font-weight: 650; font-variant-numeric: tabular-nums; }
.pq-option.selected .pq-option-number { background: rgba(14, 165, 233, 0.1); color: var(--primary-color, #0ea5e9); }
.pq-option-content { display: flex; align-items: center; flex: 1; min-width: 0; gap: 6px; overflow: hidden; }
.pq-option:hover .pq-option-body { background: var(--bg-secondary, #f8fafc); }
.pq-native:focus + .pq-option-body { outline: 2px solid var(--primary-color, #0ea5e9); outline-offset: -2px; }
.pq-option.selected .pq-option-body { background: rgba(14, 165, 233, 0.075); }
.pq-card.multiple .pq-check { border-radius: 4px; }
.pq-option-label, .pq-option-description { min-width: 0; overflow: hidden; line-height: 1.35; text-overflow: ellipsis; white-space: nowrap; }
.pq-option-label { flex: 0 1 auto; font-size: 12px; font-weight: 600; }
.pq-recommend { flex: 0 0 auto; padding: 1px 4px; border-radius: 3px; background: rgba(4, 120, 87, 0.08); color: #047857; font-size: 9px; font-weight: 650; }
.pq-check { display: grid; place-items: center; flex: 0 0 15px; width: 15px; height: 15px; box-sizing: border-box; border: 1px solid #cbd5e1; border-radius: 50%; background: var(--bg-primary, #fff); }
.pq-option.selected .pq-check { border-color: var(--primary-color, #0ea5e9); background: var(--primary-color, #0ea5e9); }
.pq-option.selected .pq-check::after { content: ''; width: 4px; height: 7px; border: solid #fff; border-width: 0 1.5px 1.5px 0; transform: translateY(-0.5px) rotate(45deg); }
.pq-option-description { flex: 1; font-size: 10px; color: var(--text-secondary, #64748b); }
.pq-error { margin: 6px 0 0; font-size: 11px; color: #b91c1c; }
.pq-note { display: block; margin-top: 9px; padding-top: 8px; border-top: 1px solid var(--border-color, #e2e8f0); }
.pq-note > span { display: block; margin-bottom: 4px; font-size: 10px; font-weight: 550; }
.pq-note small { color: var(--text-secondary, #64748b); font-weight: 400; }
.pq-note textarea { display: block; width: 100%; box-sizing: border-box; resize: vertical; min-height: 30px; padding: 6px 8px; border: 1px solid var(--border-color, #e2e8f0); border-radius: 6px; background: var(--bg-primary, #fff); color: var(--text-primary, #1e293b); font: inherit; font-size: 11px; line-height: 1.4; }
.pq-note textarea:focus { outline: 2px solid rgba(14, 165, 233, 0.4); outline-offset: 1px; border-color: var(--primary-color, #0ea5e9); }
.pq-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 9px; padding-top: 8px; border-top: 1px solid var(--border-color, #e2e8f0); }
.pq-progress { min-width: 0; overflow: hidden; font-size: 10px; color: var(--text-secondary, #64748b); text-overflow: ellipsis; white-space: nowrap; }
.pq-progress span { opacity: 0.76; }
.pq-next { display: inline-flex; align-items: center; justify-content: center; gap: 6px; min-width: 88px; min-height: 30px; padding: 5px 11px; border: 0; border-radius: 6px; background: var(--primary-color, #0ea5e9); color: #fff; font: inherit; font-size: 11px; font-weight: 600; cursor: pointer; }
.pq-next:hover:not(:disabled) { filter: brightness(0.94); }
.pq-next:focus { outline: 2px solid var(--text-primary, #1e293b); outline-offset: 2px; }
.pq-next:disabled { cursor: wait; opacity: 0.7; }
.pq-spinner { width: 11px; height: 11px; box-sizing: border-box; border: 1.5px solid rgba(255, 255, 255, 0.45); border-top-color: #fff; border-radius: 50%; animation: pq-spin 0.7s linear infinite; }
@keyframes pq-spin { to { transform: rotate(360deg); } }
@media (max-width: 640px) { .pq-card { padding: 10px; } .pq-title { white-space: normal; } .pq-option-description { display: none; } .pq-footer { align-items: stretch; gap: 8px; } .pq-progress span { display: none; } .pq-next { min-height: 32px; } }
@media (prefers-reduced-motion: reduce) { .pq-spinner { animation-duration: 1.6s; } .pq-option-body { transition: none; } }
.easy-agent-root[data-theme="dark"] .pq-recommend { background: rgba(4, 120, 87, 0.2); color: #6ee7b7; }
.easy-agent-root[data-theme="dark"] .pq-check { border-color: #475569; }
.easy-agent-root[data-theme="dark"] .pq-option:hover .pq-option-body { background: rgba(148, 163, 184, 0.08); }
.easy-agent-root[data-theme="dark"] .pq-option.selected .pq-option-body { background: rgba(14, 165, 233, 0.14); }
</style>
