import Vue from 'vue'

/**
 * 主页面对话与 AI 侧边栏共享的会话级状态。
 *
 * 两处各自持有一个 chatSessionMixin 实例（各自展示一个会话、各自流式），
 * 但会话列表、"进行中"会话集合必须是同一份：侧边栏新建/重命名/删除的会话，
 * 主页面会话列表立即可见，反之亦然。
 */
export const chatStore = Vue.observable({
  sessions: [],
  // 所有正在流式输出的会话 id（会话列表逐个显示"进行中"徽标）
  streamingSessions: [],
  // 存在待审批（HITL）消息的会话 id 集合（会话列表标题旁显示"待审批"标识）
  pendingApprovalSessions: [],
  // 最近开始流式的会话（页面刷新后据此重新挂载）
  lastStreamingSession: null,
  username: '',
  contextLength: null,
  models: [],
  activeModel: null,
})

/**
 * 跨实例事件：
 * - stream-started { sessionId, origin }：某实例开始流式，展示同一会话的其他实例据此挂载实时流
 * - session-removed { sessionId, origin }：会话被删除，展示该会话的其他实例需回退
 */
export const chatBus = new Vue()
