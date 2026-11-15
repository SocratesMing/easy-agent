import { ref, watch } from 'vue'
import { referenceQuery, addReference, referenceDraft, initialReferenceSelection } from '../../../shared/fileReferences.js'

export function useFileReferences({ props, message, replaceTextWithCaret, closeSkillPicker, updateSkillPickerPosition }) {
  const fileReferences = ref([])
  const referenceOpen = ref(false)
  const referenceSearch = ref('')
  const referencePicker = ref(null)
  let referenceStart = -1
  let referenceEnd = -1
  function restoreInitialReferences(sessionId, previousSessionId) {
    try { fileReferences.value = initialReferenceSelection(sessionStorage.getItem(props.draftKey), message.value, sessionId, previousSessionId) }
    catch (_) { fileReferences.value = [] }
  }
  function updateReference(ta) {
    const match = ta && referenceQuery(ta.value, ta.selectionStart)
    if (!match) { closeReference(); return }
    closeSkillPicker()
    updateSkillPickerPosition()
    referenceOpen.value = true; referenceSearch.value = match.query; referenceStart = match.start; referenceEnd = match.end
  }
  function closeReference() { referenceOpen.value = false; referenceSearch.value = '' }
  function resetReferenceQuery() {
    const text = message.value.slice(0, referenceStart + 1) + message.value.slice(referenceEnd)
    referenceEnd = referenceStart + 1
    referenceSearch.value = ''
    replaceTextWithCaret({ text, caret: referenceEnd })
  }
  function selectReference(item) {
    if (!referenceOpen.value || !props.sessionId) return
    fileReferences.value = addReference(fileReferences.value, item)
    const text = message.value.slice(0, referenceStart) + message.value.slice(referenceEnd)
    replaceTextWithCaret({ text, caret: referenceStart }); closeReference()
  }
  function removeReference(item) { fileReferences.value = fileReferences.value.filter(ref => ref.path !== item.path) }
  watch([message, fileReferences], ([val]) => {
    try {
      sessionStorage.setItem(props.draftKey, referenceDraft(val, fileReferences.value, props.sessionId, sessionStorage.getItem(props.draftKey)))
    } catch (e) {
      // 隐私模式等场景下 sessionStorage 可能不可用，忽略即可
    }
  }, { deep: true })

  return { fileReferences, referenceOpen, referenceSearch, referencePicker, updateReference,
    closeReference, resetReferenceQuery, selectReference, removeReference, restoreInitialReferences }
}
