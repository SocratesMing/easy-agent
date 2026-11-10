import { enhancePrompt } from '../../../api/promptEnhancement.js'
import { getStoredUsername } from '../../../utils/request.js'
import { onRuntimeEvent } from '../../../runtime/events.js'
import { chatStore, chatBus } from '../store/chatStore.js'
import * as tokens from '../../../utils/skillPillTokens.js'
import { createEnhancementController, enhancementSnapshot } from '../../../shared/promptEnhancement/controller.js'

export default {
  data() {
    return { enhancement: { pending: false, notice: '' }, enhancementComposing: false }
  },
  computed: {
    enhancementBlocked() {
      return this.disabled || this.isStreaming || this.preparingFiles || this.enhancementComposing ||
        (this.sessionId && chatStore.streamingSessions.includes(this.sessionId))
    },
    canEnhance() {
      return !this.enhancement.pending && !this.enhancementBlocked && !!tokens.stripSkillTokens(this.message, this.tokenMap).trim()
    },
  },
  created() {
    this._enhancer = createEnhancementController({
      read: () => ({
        ...enhancementSnapshot(this.message, this.tokenMap, tokens),
        blocked: !!this.enhancementBlocked,
        scope: [this.sessionId, this.draftKey, this.activeExpert, this.expertPrompt,
          this.pendingExpertEditId, this.preselectedSkillDirs, this.knownSkills,
          chatStore.username, getStoredUsername()],
      }),
      write: text => {
        this.message = text
        this.$nextTick(() => {
          this.autoResize(); this.syncMirror(); this.$refs.textareaRef?.focus()
        })
      },
      request: enhancePrompt,
      publish: state => { this.enhancement = state },
    })
    // Vue2's default watcher batches edits; sync observes edit-then-revert too.
    this._enhancementUnwatch = [
      'message', 'sessionId', 'draftKey', 'activeExpert', 'expertPrompt',
      'pendingExpertEditId', 'preselectedSkillDirs', 'knownSkills', 'enhancementBlocked',
    ].map(key => this.$watch(key, this.invalidateEnhancement, { deep: true, sync: true }))
    this._enhancementUnwatch.push(this.$watch(() => chatStore.username, this.invalidateEnhancement, { sync: true }))
    this._enhancementStream = ({ sessionId }) => {
      if (sessionId === this.sessionId) this.invalidateEnhancement()
    }
    chatBus.$on('stream-started', this._enhancementStream)
    this._enhancementOff = ['auth-expired', 'auth-changed'].map(event => onRuntimeEvent(event, this.invalidateEnhancement))
    window.addEventListener('storage', this.invalidateEnhancement)
    this._enhancementUnwatch.push(this.$watch('draftKey', (key, old) => {
      try {
        sessionStorage.setItem(old, this.message)
        this.message = sessionStorage.getItem(key) || ''
      } catch (_) { this.message = '' }
      this.$nextTick(() => { this.autoResize(); this.syncMirror() })
    }, { sync: true }))
  },
  beforeDestroy() {
    this._enhancer.dispose()
    this._enhancementUnwatch.forEach(off => off())
    this._enhancementOff.forEach(off => off())
    chatBus.$off('stream-started', this._enhancementStream)
    window.removeEventListener('storage', this.invalidateEnhancement)
  },
  methods: {
    invalidateEnhancement() { this._enhancer?.invalidate() },
    enhancePrompt() { return this._enhancer.enhance() },
    enhancementCompositionStart() { this.invalidateEnhancement(); this.enhancementComposing = true },
    enhancementCompositionEnd() { this.enhancementComposing = false },
  },
}
