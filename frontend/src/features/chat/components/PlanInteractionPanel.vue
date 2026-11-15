<template>
  <div v-if="actions.length" class="plan-confirmation-stack">
    <PlanQuestionStack
      v-for="batch in batches"
      :key="'plan-pending-' + batch.key"
      :action="batch.action"
      @submit="submit(batch, $event)"
    />
  </div>
</template>

<script setup>
import { computed } from 'vue'
import PlanQuestionStack from './PlanQuestionStack.vue'
import {
  buildInteractionBatchResponses,
  buildInteractionBatches,
  pendingInteractionActions,
} from '../../../shared/chat/planInteraction.js'

const props = defineProps({ message: { type: Object, required: true } })
const emit = defineEmits(['respond'])
const actions = computed(() =>
  props.message && props.message.role === 'assistant'
    ? pendingInteractionActions(props.message)
    : []
)
const batches = computed(() => buildInteractionBatches(actions.value))

function submit (batch, payload) {
  const responses = buildInteractionBatchResponses(
    batch,
    payload.response.answers,
    payload.response.note,
  )
  const first = responses[0] || {}
  emit('respond', {
    interruptId: first.interruptId || null,
    actionIndex: first.actionIndex != null ? first.actionIndex : 0,
    actionName: first.actionName || 'ask_user',
    response: first.response || { answers: {} },
    responses,
    confirmation: payload.confirmation,
  })
}
</script>

<style scoped>
.plan-confirmation-stack { width: 100%; display: flex; flex-direction: column; gap: 10px; }
</style>
