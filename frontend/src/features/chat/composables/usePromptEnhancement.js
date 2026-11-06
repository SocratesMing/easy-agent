import { ref, computed, watch, nextTick, onUnmounted } from 'vue'
import { enhancePrompt } from '../../../api/promptEnhancement.js'
import { getStoredUsername } from '../../../api/auth.js'
import { chatStore, chatBus } from '../store/chatStore.js'
import * as tokens from '../../../utils/skillPillTokens.js'
import { createEnhancementController, enhancementSnapshot } from '../../../shared/promptEnhancement/controller.js'

export function usePromptEnhancement({ props, message, tokenMap, knownSkills, preselectedSkillDirs,
  pendingExpertEditId, preparingFiles, textareaRef, autoResize, syncMirror }) {
  const enhancement = ref({ pending: false, notice: '' })
  const composing = ref(false)
  const blocked = computed(() => !!(props.disabled || props.isStreaming || preparingFiles.value || composing.value ||
    (props.sessionId && chatStore.streamingSessions.includes(props.sessionId))))
  const canEnhance = computed(() => !blocked.value && !enhancement.value.pending && !!tokens.stripSkillTokens(message.value, tokenMap.value).trim())
  const controller = createEnhancementController({
    read: () => ({
      ...enhancementSnapshot(message.value, tokenMap.value, tokens), blocked: blocked.value,
      scope: [props.sessionId, props.draftKey, props.activeExpert, props.prefillText, pendingExpertEditId.value,
        [...preselectedSkillDirs.value], knownSkills.value, chatStore.username, getStoredUsername()],
    }),
    write: text => {
      message.value = text
      nextTick(() => { autoResize(); syncMirror(); textareaRef.value?.focus() })
    },
    request: enhancePrompt, publish: state => { enhancement.value = state },
  })
  const invalidateEnhancement = controller.invalidate
  watch([message, () => props.sessionId, () => props.draftKey, () => props.activeExpert,
    () => props.prefillText, pendingExpertEditId, preselectedSkillDirs, knownSkills, blocked,
    () => chatStore.username], invalidateEnhancement, { deep: true, flush: 'sync' })
  watch(() => props.draftKey, (key, old) => {
    try { sessionStorage.setItem(old, message.value); message.value = sessionStorage.getItem(key) || '' }
    catch (_) { message.value = '' }
    nextTick(() => { autoResize(); syncMirror() })
  }, { flush: 'sync' })
  const stream = ({ sessionId }) => { if (sessionId === props.sessionId) invalidateEnhancement() }
  chatBus.on('stream-started', stream)
  for (const event of ['auth-expired', 'auth-changed', 'storage']) window.addEventListener(event, invalidateEnhancement)
  onUnmounted(() => {
    controller.dispose()
    chatBus.off('stream-started', stream)
    for (const event of ['auth-expired', 'auth-changed', 'storage']) window.removeEventListener(event, invalidateEnhancement)
  })
  return {
    enhancement, canEnhance, invalidateEnhancement, enhancePrompt: controller.enhance,
    enhancementCompositionStart() { invalidateEnhancement(); composing.value = true },
    enhancementCompositionEnd() { composing.value = false },
  }
}
