import Vue from 'vue'

/**
 * AI 侧边栏的页面/功能上下文。
 *
 * - App 根据当前展示的页面生成基础上下文（buildPageContext）；
 * - 任意子组件可调用 setPageContextExtra(key, text) 补充更细的功能上下文
 *   （例如正在预览的文件、当前选中的标签页），传 null 移除。key 与页面 key 相同时
 *   只在该页面生效（见 EXTRA_PAGE），以 `global:` 开头的 key 在所有页面生效；
 * - 外部宿主页面可通过 window.EasyAgentAI.setContext(text) 注入上下文。
 *
 * 上下文文本的第一行固定为「页面：xxx」，消息气泡据此展示上下文标签。
 */
export const pageContextStore = Vue.observable({
  extras: {},
  external: '',
})

export function setPageContextExtra(key, text) {
  if (!key) return
  if (text) Vue.set(pageContextStore.extras, key, String(text))
  else Vue.delete(pageContextStore.extras, key)
}

export function setExternalPageContext(text) {
  pageContextStore.external = text ? String(text) : ''
}

const PAGES = {
  chat: {
    title: '对话',
    description: '用户正在主对话页与 AI 对话。',
    suggestions: ['总结主对话里的要点', '基于主对话的结论给出下一步建议', '帮我检查主对话里的回答是否有遗漏'],
  },
  assets: {
    title: '我的资产',
    description: '用户正在「我的资产」页面，管理上传的文件和 AI 生成的文件（文档、表格、演示文稿等）。',
    suggestions: ['我的资产里可以做哪些操作？', '如何把资产里的文件用于新的对话？', '帮我规划一下文件的分类整理方式'],
  },
  'skill-center': {
    title: '技能中心',
    description: '用户正在「技能中心」页面，浏览、启用、停用或创建 Agent 技能（Skill）与连接器（MCP）。',
    suggestions: ['介绍一下我已启用的技能', '帮我设计一个新技能的 SKILL.md', '技能和 MCP 连接器有什么区别？'],
  },
  'scheduled-tasks': {
    title: '定时任务',
    description: '用户正在「定时任务」页面，查看和管理按计划自动执行的 AI 任务。',
    suggestions: ['帮我写一个每天早上 9 点执行的任务提示词', 'Cron 表达式 0 9 * * 1-5 是什么意思？', '定时任务适合做哪些事情？'],
  },
  settings: {
    title: '设置',
    description: '用户正在「设置」面板，调整记忆、主题、模型等个人配置。',
    suggestions: ['长期记忆是如何工作的？', '帮我整理一份适合写进记忆的个人偏好', '不同模型该怎么选？'],
  },
  'user-management': {
    title: '用户管理',
    description: '用户正在「用户管理」面板，查看用户列表、重置密码等管理操作。',
    suggestions: ['用户管理有哪些注意事项？', '重置密码后应该提醒用户做什么？'],
  },
}

// 不以页面 key 命名的补充上下文所属页面
const EXTRA_PAGE = {
  workspace: 'chat',
}

const MAX_EXCERPT = 1200

function excerptMessages(messages) {
  if (!Array.isArray(messages) || messages.length === 0) return ''
  const recent = messages.filter(m => m && m.content && (m.role === 'user' || m.role === 'assistant')).slice(-4)
  let text = recent
    .map(m => `${m.role === 'user' ? '用户' : 'AI'}：${String(m.content).replace(/\s+/g, ' ').trim()}`)
    .join('\n')
  if (text.length > MAX_EXCERPT) text = '…' + text.slice(-MAX_EXCERPT)
  return text
}

/**
 * @param {object} state App 的页面状态
 * @returns {{ key: string, page: string, title: string, description: string, suggestions: string[] }}
 *   key 用于「当前页面会话」记忆：同一页面再次唤起侧边栏时优先回到该页面的会话
 */
export function buildPageContext(state) {
  let page = 'chat'
  if (state.showUserManagementPanel) page = 'user-management'
  else if (state.showSettingsPanel) page = 'settings'
  else if (state.showSkillCenter) page = 'skill-center'
  else if (state.showScheduledTasks) page = 'scheduled-tasks'
  else if (state.showAssets) page = 'assets'

  const meta = PAGES[page]
  const lines = [meta.description]
  if (page === 'chat' && state.session) {
    const title = state.session.title || '新会话'
    lines.push(`主对话当前会话：「${title}」`)
    const excerpt = excerptMessages(state.messages)
    if (excerpt) lines.push(`主对话最近内容：\n${excerpt}`)
  }
  return {
    key: page,
    page,
    title: page === 'chat' && state.session && state.session.title ? `对话 · ${state.session.title}` : meta.title,
    description: lines.join('\n'),
    suggestions: meta.suggestions,
  }
}

/** 组装最终发送给模型的上下文文本（第一行为「页面：xxx」） */
export function formatPageContext(ctx, { quote = '' } = {}) {
  if (!ctx) return ''
  const parts = [`页面：${ctx.title}`, ctx.description]
  const extras = Object.keys(pageContextStore.extras)
    .filter(k => k.startsWith('global:') || (EXTRA_PAGE[k] || k) === ctx.page)
    .map(k => pageContextStore.extras[k])
    .filter(Boolean)
  if (extras.length) parts.push(`功能上下文：\n${extras.join('\n')}`)
  if (pageContextStore.external) parts.push(`宿主页面上下文：\n${pageContextStore.external}`)
  if (quote) parts.push(`用户在页面中选中的内容：\n"""\n${quote}\n"""`)
  return parts.filter(Boolean).join('\n')
}

/** 上下文文本 → 气泡里展示的短标签 */
export function pageContextLabel(text) {
  if (!text) return ''
  const first = String(text).split('\n')[0] || ''
  return first.replace(/^页面：/, '') || '页面上下文'
}
