import { referenceQuery, addReference, referenceDraft, initialReferenceSelection } from '../../../shared/fileReferences.js'

export default {
  data() {
    return { fileReferences: [], referenceOpen: false, referenceSearch: '', referenceStart: -1, referenceEnd: -1 }
  },
  watch: {
    fileReferences: {
      deep: true,
      handler() {
        this.invalidateEnhancement()
        try { sessionStorage.setItem(this.draftKey, referenceDraft(this.message, this.fileReferences, this.sessionId, sessionStorage.getItem(this.draftKey))) } catch (_) { /* 存储不可用 */ }
      },
    },
  },
  methods: {
    restoreInitialReferences(sessionId, previousSessionId) {
      try { this.fileReferences = initialReferenceSelection(sessionStorage.getItem(this.draftKey), this.message, sessionId, previousSessionId) }
      catch (_) { this.fileReferences = [] }
    },
    updateReference(ta) {
      const match = ta && referenceQuery(ta.value, ta.selectionStart)
      if (!match) { this.closeReference(); return }
      this.closeSkillPicker()
      this.updateSkillPickerPosition()
      this.referenceOpen = true; this.referenceSearch = match.query; this.referenceStart = match.start; this.referenceEnd = match.end
    },
    closeReference() { this.referenceOpen = false; this.referenceSearch = '' },
    resetReferenceQuery() {
      const text = this.message.slice(0, this.referenceStart + 1) + this.message.slice(this.referenceEnd)
      this.referenceEnd = this.referenceStart + 1
      this.referenceSearch = ''
      this.replaceTextWithCaret({ text, caret: this.referenceEnd })
    },
    selectReference(item) {
      if (!this.referenceOpen || !this.sessionId) return
      this.invalidateEnhancement()
      this.fileReferences = addReference(this.fileReferences, item)
      const text = this.message.slice(0, this.referenceStart) + this.message.slice(this.referenceEnd)
      this.replaceTextWithCaret({ text, caret: this.referenceStart }); this.closeReference()
    },
    removeReference(item) { this.fileReferences = this.fileReferences.filter(ref => ref.path !== item.path) },
  },
}
