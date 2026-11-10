import { subscribeSessions } from './state.js'

export function onSessionArchiveChanged({ sessionId, archived }) {
  if (!archived) return
  if (this.currentSessionId !== sessionId) return
  // Persist this view's welcome choice, without deleting page history or drafts.
  if (this.pageSessions && this.pageKey) this.rememberPageSession('__new__')
  this.handleCreateSession()
  this.sessionLoading = false
  this.error = null
  this.pendingApproval = null
  this.streamingAssistantId = null
  this.currentAbortController = null
  if (!this.pageSessions) {
    try { localStorage.setItem('easy_agent_active_session', '__new__') } catch { /* ignore */ }
  }
}

export function connectSessionView(vm) {
  return subscribeSessions(event => {
    if (event.type === 'archive') onSessionArchiveChanged.call(vm, event)
    if (event.type === 'reset') {
      vm.sessionStates = {}; vm._historyVersions = {}; vm.currentSessionId = null
      vm.loadedSessionId = null; vm.messages = []; vm.currentTodos = []; vm.error = null
    }
  })
}
