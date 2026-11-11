import { relatedEvidence } from '../markdown/citations.js'

let nextId = 0

/** 浮层优先放在编号下方，空间不足时翻到上方，并限制在可见窗口内。 */
export function citationViewport(win) {
  const visual = win.visualViewport
  return {
    left: visual ? visual.offsetLeft : 0,
    top: visual ? visual.offsetTop : 0,
    width: Math.min(win.document.documentElement.clientWidth, visual ? visual.width : win.innerWidth),
    height: Math.min(win.innerHeight, visual ? visual.height : win.innerHeight),
  }
}

export function citationPosition(anchor, card, viewport) {
  const gap = 8
  const margin = 12
  const left = (viewport.left || 0) + margin
  const top = (viewport.top || 0) + margin
  const right = (viewport.left || 0) + viewport.width - margin
  const bottom = (viewport.top || 0) + viewport.height - margin
  const width = Math.max(1, Math.min(420, viewport.width - margin * 2))
  const maxHeight = Math.max(1, viewport.height - margin * 2)
  const height = Math.min(card.height, maxHeight)
  const below = anchor.bottom + gap
  const above = anchor.top - gap - height
  return {
    left: Math.max(left, Math.min(anchor.left, right - width)),
    top: Math.max(top, Math.min(below + height <= bottom ? below : above, bottom - height)),
    width,
    maxHeight,
  }
}

export default {
  data() {
    return { citationPreview: null, citationAnchor: null, citationTimer: null,
      citationStyle: {}, citationPopoverId: `citation-preview-${++nextId}`, citationRestoringFocus: false }
  },
  watch: {
    currentSessionId() { this.closeCitation() },
    'messages.length'() { this.closeCitation() },
  },
  mounted() {
    document.addEventListener('pointerdown', this.outsideCitation, true)
    document.addEventListener('keydown', this.citationKeydown)
    window.addEventListener('resize', this.positionCitation)
    if (window.visualViewport) {
      window.visualViewport.addEventListener('resize', this.positionCitation)
      window.visualViewport.addEventListener('scroll', this.positionCitation)
    }
    window.addEventListener('scroll', this.scrollCitation, true)
  },
  beforeDestroy() { this.disposeCitations() },
  updated() {
    if (this.citationAnchor && !this.citationAnchor.isConnected) this.closeCitation()
  },
  methods: {
    citationTrigger(event) {
      const target = event.target
      return target && typeof target.closest === 'function' ? target.closest('.ke-cite-number') : null
    },
    previewCitation(event) {
      if (this.citationRestoringFocus) return
      const anchor = this.citationTrigger(event)
      if (!anchor || !this.$el.contains(anchor)) return
      const wrapper = anchor.closest('[data-message-index]')
      const message = wrapper && this.messages[Number(wrapper.dataset.messageIndex)]
      const number = Number(anchor.dataset.cite)
      const item = message && relatedEvidence(message).items[number - 1]
      if (!item) return
      this.keepCitation()
      if (this.citationAnchor !== anchor) this.closeCitation()
      this.citationAnchor = anchor
      this.citationPreview = { item, number }
      const viewport = citationViewport(window)
      this.citationStyle = {
        width: `${Math.max(1, Math.min(420, viewport.width - 24))}px`,
        maxHeight: `${Math.max(1, viewport.height - 24)}px`, visibility: 'hidden',
      }
      anchor.setAttribute('aria-describedby', this.citationPopoverId)
      this.$nextTick(this.positionCitation)
    },
    positionCitation() {
      const anchor = this.citationAnchor
      const card = this.$refs.citationPopover
      if (!anchor || !card) return
      if (!anchor.isConnected) return this.closeCitation()
      const position = citationPosition(anchor.getBoundingClientRect(), card.$el.getBoundingClientRect(), citationViewport(window))
      const style = Object.fromEntries(Object.entries(position).map(([key, value]) => [key, `${value}px`]))
      if (Object.keys(style).some(key => style[key] !== this.citationStyle[key]) || this.citationStyle.visibility) this.citationStyle = style
    },
    leaveCitation(event) {
      const anchor = this.citationTrigger(event)
      if (anchor && (!event.relatedTarget || !anchor.contains(event.relatedTarget))) this.deferCitationClose()
    },
    keepCitation() { clearTimeout(this.citationTimer) },
    deferCitationClose() {
      this.keepCitation()
      this.citationTimer = setTimeout(() => this.closeCitation(), 180)
    },
    closeCitation() {
      this.keepCitation()
      if (this.citationAnchor) {
        this.citationAnchor.removeAttribute('aria-describedby')
      }
      this.citationPreview = null
      this.citationAnchor = null
    },
    dismissCitation() {
      const anchor = this.citationAnchor
      this.closeCitation()
      if (anchor && anchor.isConnected) {
        this.citationRestoringFocus = true
        anchor.focus({ preventScroll: true })
        this.$nextTick(() => { this.citationRestoringFocus = false })
      }
    },
    outsideCitation(event) {
      const card = this.$refs.citationPopover
      if (card && !card.$el.contains(event.target) && !this.citationTrigger(event)) this.closeCitation()
    },
    citationKeydown(event) {
      if (event.key === 'Escape' && this.citationPreview) {
        event.preventDefault()
        this.dismissCitation()
      }
    },
    scrollCitation(event) {
      const card = this.$refs.citationPopover
      if (card && !card.$el.contains(event.target)) this.closeCitation()
    },
    disposeCitations() {
      this.closeCitation()
      document.removeEventListener('pointerdown', this.outsideCitation, true)
      document.removeEventListener('keydown', this.citationKeydown)
      window.removeEventListener('resize', this.positionCitation)
      if (window.visualViewport) {
        window.visualViewport.removeEventListener('resize', this.positionCitation)
        window.visualViewport.removeEventListener('scroll', this.positionCitation)
      }
      window.removeEventListener('scroll', this.scrollCitation, true)
    },
  },
}
