<template>
  <section v-if="model" class="pqs-root" :aria-label="model.title">
    <header class="pqs-header">
      <div class="pqs-heading">
        <h2 class="pqs-title">{{ model.title }}</h2>
        <p v-if="model.description" class="pqs-description" :title="model.description">{{ model.description }}</p>
      </div>
      <div class="pqs-tools">
        <button v-if="currentIndex > 0" type="button" class="pqs-back" :disabled="busy" :title="previousAnswer ? `上一步：${previousAnswer}` : '返回上一项'" aria-label="返回上一项" @click="goBack">上一步</button>
        <div class="pqs-step-track" role="list" aria-label="确认进度">
          <button v-for="(question, index) in model.questions" :key="question.id" type="button" class="pqs-step" :class="{ done: index < currentIndex, current: index === currentIndex }" :disabled="busy || index > currentIndex" :aria-current="index === currentIndex ? 'step' : undefined" :aria-label="`${index + 1}. ${question.title}${index < currentIndex ? '（已确认，点击修改）' : ''}`" @click="goTo(index)"><span>{{ index < currentIndex ? '✓' : index + 1 }}</span></button>
        </div>
      </div>
    </header>
    <div class="pqs-carousel" aria-live="polite">
      <div v-if="model.questions.length > 1" class="pqs-layer pqs-layer-two" aria-hidden="true"></div>
      <div v-if="model.questions.length > 1" class="pqs-layer pqs-layer-one" aria-hidden="true"></div>
      <transition :name="'pqs-slide-' + direction" mode="out-in">
        <div :key="currentQuestion.id" class="pqs-stage" role="group" :aria-label="`第 ${currentIndex + 1} 项，共 ${model.questions.length} 项`">
          <PlanQuestionCard :question="currentQuestion" :index="currentIndex" :total="model.questions.length" :initial-answer="answers[currentQuestion.id]" :initial-note="note" :show-note="model.allowNote && isLast" :submit-label="isLast ? model.submitLabel : '确认，继续下一项'" :busy="busy" @next="accept" @back="goBack" />
        </div>
      </transition>
    </div>
  </section>
  <section v-else-if="action" class="pqs-root pqs-invalid" role="alert">
    <div class="pqs-heading">
      <h2 class="pqs-title">这次选项没有完整返回</h2>
      <p class="pqs-description">AI 生成的确认项缺少有效问题或至少两个候选项。为避免误选，流程尚未继续。</p>
    </div>
    <button type="button" class="pqs-retry" :disabled="busy" @click="retryInvalid">{{ busy ? '正在继续…' : '让 AI 重新整理确认项' }}</button>
  </section>
</template>

<script>
import PlanQuestionCard from './PlanQuestionCard.vue'
import { answerLabelsForQuestion, buildConfirmationMessage, normalizeInteractionRequest } from '../../../shared/chat/planInteraction.js'

export default {
  name: 'PlanQuestionStack',
  components: { PlanQuestionCard },
  props: {
    action: { type: Object, required: true },
    busy: { type: Boolean, default: false },
  },
  data() { return { currentIndex: 0, direction: 'next', answers: {}, note: '' } },
  computed: {
    questionLimit() { return Number(this.action.questionLimit) || 4 },
    model() { return normalizeInteractionRequest(this.action.arguments, { maxQuestions: this.questionLimit }) },
    currentQuestion() { return this.model ? this.model.questions[this.currentIndex] : null },
    isLast() { return this.currentIndex === (this.model ? this.model.questions.length : 1) - 1 },
    previousAnswer() {
      if (this.currentIndex <= 0 || !this.model) return ''
      const question = this.model.questions[this.currentIndex - 1]
      return answerLabelsForQuestion(question, this.answers[question.id]).join('、')
    },
  },
  methods: {
    accept({ answer, note }) {
      if (!this.currentQuestion || this.busy) return
      this.$set(this.answers, this.currentQuestion.id, answer)
      if (this.isLast) this.note = note || ''
      if (!this.isLast) {
        this.direction = 'next'
        this.currentIndex += 1
        return
      }
      this.$emit('submit', {
        response: { answers: { ...this.answers }, note: this.note },
        confirmation: { id: `plan-confirm-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, text: buildConfirmationMessage(this.model, this.answers, this.note, { maxQuestions: this.questionLimit }) },
      })
    },
    goTo(index) { if (!this.busy && index >= 0 && index < this.currentIndex) { this.direction = 'back'; this.currentIndex = index } },
    goBack() { if (!this.busy && this.currentIndex > 0) { this.direction = 'back'; this.currentIndex -= 1 } },
    retryInvalid() {
      if (this.busy) return
      this.$emit('submit', {
        response: { answers: {}, note: '上一个确认请求格式不完整。请重新调用 ask_user：保留 1-4 个关键项目，每题提供 2-6 个具体候选项，并等待我确认后再继续。' },
        confirmation: { id: `plan-confirm-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, text: '请重新整理需要我确认的方案选项。' },
      })
    },
  },
}
</script>

<style scoped>
.pqs-root { width: 100%; box-sizing: border-box; padding: 0 2px; color: var(--text-primary, #1e293b); }
.pqs-header { display: flex; align-items: center; gap: 12px; min-height: 24px; margin-bottom: 8px; }
.pqs-heading { flex: 1; min-width: 0; }
.pqs-title { margin: 0; overflow: hidden; font-size: 13px; line-height: 1.35; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.pqs-description { display: -webkit-box; margin: 2px 0 0; overflow: hidden; color: var(--text-secondary, #64748b); font-size: 11px; line-height: 1.4; -webkit-box-orient: vertical; -webkit-line-clamp: 1; }
.pqs-tools { display: flex; align-items: center; flex: 0 0 auto; gap: 8px; }
.pqs-step-track { display: flex; align-items: center; gap: 4px; }
.pqs-step { display: grid; place-items: center; width: 18px; height: 18px; padding: 0; border: 1px solid var(--border-color, #e2e8f0); border-radius: 5px; background: var(--bg-primary, #fff); color: var(--text-secondary, #64748b); font: inherit; font-size: 9px; font-weight: 650; cursor: default; }
.pqs-step.done { border-color: rgba(4, 120, 87, 0.24); background: rgba(4, 120, 87, 0.06); color: #047857; cursor: pointer; }
.pqs-step.current { border-color: var(--primary-color, #0ea5e9); background: var(--primary-color, #0ea5e9); color: #fff; }
.pqs-step:disabled { opacity: 0.55; }
.pqs-step:focus { outline: 2px solid var(--primary-color, #0ea5e9); outline-offset: 1px; }
.pqs-back { padding: 1px 0; border: 0; background: transparent; color: var(--text-secondary, #64748b); font: inherit; font-size: 11px; cursor: pointer; }
.pqs-back:hover { color: var(--text-primary, #1e293b); }
.pqs-back:disabled { cursor: wait; opacity: 0.5; }
.pqs-back:focus { outline: 2px solid var(--primary-color, #0ea5e9); outline-offset: 2px; }
.pqs-carousel { position: relative; padding: 0 3px 7px; perspective: 900px; }
.pqs-layer { position: absolute; right: 11px; bottom: 0; left: 11px; height: 24px; border: 1px solid var(--border-color, #e2e8f0); border-radius: 8px; background: var(--bg-primary, #fff); transform-origin: top center; pointer-events: none; }
.pqs-layer-one { opacity: 0.72; transform: translateY(3px) scale(0.994); }
.pqs-layer-two { opacity: 0.3; transform: translateY(6px) scale(0.988); }
.pqs-stage { position: relative; z-index: 2; }
.pqs-invalid { box-sizing: border-box; padding: 12px; border: 1px solid rgba(245, 158, 11, 0.4); border-radius: 8px; background: var(--bg-primary, #fff); }
.pqs-retry { padding: 6px 10px; border: 1px solid var(--border-color, #e2e8f0); border-radius: 6px; background: var(--bg-secondary, #f8fafc); color: var(--text-primary, #1e293b); font: inherit; font-size: 12px; font-weight: 600; cursor: pointer; }
.pqs-retry:disabled { cursor: wait; opacity: 0.7; }
.pqs-retry:focus { outline: 2px solid var(--primary-color, #0ea5e9); outline-offset: 2px; }
.pqs-slide-next-enter-active, .pqs-slide-next-leave-active, .pqs-slide-back-enter-active, .pqs-slide-back-leave-active { transition: transform 0.16s ease, opacity 0.14s ease; }
.pqs-slide-next-enter, .pqs-slide-next-leave-to { opacity: 0; transform: translateX(18px); }
.pqs-slide-next-leave-to { transform: translateX(-18px); }
.pqs-slide-back-enter, .pqs-slide-back-leave-to { opacity: 0; transform: translateX(-18px); }
.pqs-slide-back-leave-to { transform: translateX(18px); }
@media (max-width: 640px) { .pqs-root { padding: 0; } .pqs-header { align-items: flex-start; } .pqs-title { white-space: normal; } .pqs-tools { gap: 6px; } .pqs-back { display: none; } }
@media (prefers-reduced-motion: reduce) { .pqs-slide-next-enter-active, .pqs-slide-next-leave-active, .pqs-slide-back-enter-active, .pqs-slide-back-leave-active { transition: none; } }
.easy-agent-root[data-theme="dark"] .pqs-step.done { background: rgba(4, 120, 87, 0.2); color: #6ee7b7; }
</style>
