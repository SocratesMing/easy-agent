import { computed, onMounted, onUnmounted, watch } from 'vue'
import { chatBus, chatStore } from '../store/chatStore.js'
import { buildPageContext } from '../../../utils/pageContext.js'

/**
 * 主页面（App.vue）与 AI 侧边栏的联动：页面上下文、共享配置同步、
 * 「在主页面打开」，以及侧边栏发起的流式 / 删除会话在主页面的响应。
 * 会话列表本身通过 chatStore 共享，不在这里处理。
 */
export function useAiSidebarBridge(app) {
  const {
    panels, userProfile, sessions, currentSessionId, loadedSessionId, messages,
    sessionUsage, availableModels, selectedModel, sessionStates, attachedStreamingSessions,
    attachToStreamingSession, handleSelectSession, handleCreateSession,
  } = app

  // 主页面正展示的会话被删除（任一侧）：先清空归属，避免把已删除会话的界面数据再存回缓存
  async function fallbackAfterSessionRemoved() {
    currentSessionId.value = null
    loadedSessionId.value = null
    messages.value = []
    const nextId = sessions.value[0]?.session_id || null
    // 有剩余会话就复用切会话逻辑加载其历史（含骨架屏），否则回到新建会话首页
    if (nextId) await handleSelectSession(nextId)
    else await handleCreateSession()
  }

  const aiPageContext = computed(() => buildPageContext({
    showAssets: panels.showAssets.value,
    showSkillCenter: panels.showSkillCenter.value,
    showScheduledTasks: panels.showScheduledTasks.value,
    showSettingsPanel: panels.showSettingsPanel.value,
    showUserManagementPanel: panels.showUserManagementPanel.value,
    session: currentSessionId.value
      ? sessions.value.find(s => s.session_id === currentSessionId.value) || { session_id: currentSessionId.value }
      : null,
    messages: messages.value,
  }))

  watch(() => userProfile.value.username, (name) => { chatStore.username = name || '' }, { immediate: true })
  watch(() => sessionUsage.value.context_length, (len) => { if (len) chatStore.contextLength = len })
  watch(availableModels, (models) => { chatStore.models = models }, { immediate: true })
  watch(selectedModel, (name) => { chatStore.activeModel = name }, { immediate: true })

  async function handleOpenSessionInMain(sessionId) {
    if (!sessionId) return
    const onChatPage = !panels.showAssets.value && !panels.showSkillCenter.value && !panels.showScheduledTasks.value
    if (sessionId === currentSessionId.value && onChatPage) return
    await handleSelectSession(sessionId)
  }

  // 侧边栏在主页面正展示的会话里开始流式：挂载实时流同步展示
  function onPeerStreamStarted({ sessionId, origin }) {
    if (origin === 'main' || !sessionId || loadedSessionId.value !== sessionId) return
    if (attachedStreamingSessions.value[sessionId] === 'displayed') return
    attachToStreamingSession(sessionId, { switchTo: false, displayed: true })
  }

  // 侧边栏删除了主页面正展示的会话：回退到其他会话
  function onPeerSessionRemoved({ sessionId, origin }) {
    if (origin === 'main') return
    delete sessionStates.value[sessionId]
    if (currentSessionId.value === sessionId) fallbackAfterSessionRemoved()
  }

  onMounted(() => {
    chatBus.on('stream-started', onPeerStreamStarted)
    chatBus.on('session-removed', onPeerSessionRemoved)
  })
  onUnmounted(() => {
    chatBus.off('stream-started', onPeerStreamStarted)
    chatBus.off('session-removed', onPeerSessionRemoved)
  })

  return { aiPageContext, handleOpenSessionInMain, fallbackAfterSessionRemoved }
}
