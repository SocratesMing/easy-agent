<template>
  <div v-if="actions.length" class="plan-confirmation-stack">
    <PlanQuestionStack v-for="batch in batches" :key="'plan-pending-' + batch.key" :action="batch.action" @submit="submit(batch, $event)" />
  </div>
</template>

<script>
import PlanQuestionStack from './PlanQuestionStack.vue'
import { buildInteractionBatchResponses, buildInteractionBatches, pendingInteractionActions } from '../../../shared/chat/planInteraction.js'

export default {
  name: 'PlanInteractionPanel',
  components: { PlanQuestionStack },
  props: {
    message: { type: Object, required: true },
  },
  computed: {
    actions() {
      return this.message && this.message.role === 'assistant'
        ? pendingInteractionActions(this.message)
        : []
    },
    batches() {
      return buildInteractionBatches(this.actions)
    },
  },
  methods: {
    submit(batch, payload) {
      const responses = buildInteractionBatchResponses(batch, payload.response.answers, payload.response.note)
      const first = responses[0] || {}
      this.$emit('respond', {
        interruptId: first.interruptId || null,
        actionIndex: first.actionIndex != null ? first.actionIndex : 0,
        actionName: first.actionName || 'ask_user',
        response: first.response || { answers: {} },
        responses,
        confirmation: payload.confirmation,
      })
    },
  },
}
</script>

<style scoped>
.plan-confirmation-stack { width: 100%; display: flex; flex-direction: column; gap: 10px; }
</style>
