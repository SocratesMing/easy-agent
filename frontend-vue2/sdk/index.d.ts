export interface EasyAgentPageContext {
  /** 页面标识：同一 key 再次唤起侧边栏时优先回到该页面最近的会话。默认 location.pathname */
  key?: string
  /** 页面名称：显示在上下文标签与欢迎语中。默认 document.title */
  title?: string
  /** 页面用途的一句话描述，随消息提供给 AI */
  description?: string
  /** 欢迎页的快捷提问（最多 4 条） */
  suggestions?: string[]
  /** 页面业务数据（纯文本，建议 < 4000 字），随消息提供给 AI */
  content?: string
}

export type EasyAgentPageContextInput =
  | EasyAgentPageContext
  | string
  | null
  | (() => EasyAgentPageContext | string | null)

export interface EasyAgentAuth {
  /** 宿主后端换取的 Easy Agent token（推荐） */
  token?: string
  /** 异步获取 token；token 过期时 SDK 会再次调用以自动续期 */
  getToken?: () => string | Promise<string>
  /** 免密登录用户名（后端需开启免密登录；会触发单点登录，挤掉该用户其他端的登录） */
  username?: string
  /** 免密登录用户 ID，新用户为 0/空时由后端生成 */
  userId?: string
  /** 与 Easy Agent 主站同域部署时复用主站登录态 */
  shareMainAppLogin?: boolean
}

export interface EasyAgentFeatures {
  /** 右侧悬浮唤起按钮，默认 true */
  launcher?: boolean
  /** 划词「问 AI」，默认 true */
  selectionAsk?: boolean
  /** Ctrl/⌘ + J 快捷键，默认 true */
  shortcut?: boolean
  /** 悬浮 / 固定模式切换，默认 true */
  dock?: boolean
  /** 「在主站打开」按钮与「+」菜单的「在管理面板中打开」，配置了 mainAppUrl / onOpenInMain / onOpenPanel 时默认 true */
  openInMain?: boolean
}

export interface EasyAgentSidebarOptions {
  /** Easy Agent 后端地址，如 https://agent.example.com */
  baseURL: string
  auth: EasyAgentAuth
  pageContext?: EasyAgentPageContextInput
  features?: EasyAgentFeatures
  /** Easy Agent 主站前端地址，用于「在主站打开」 */
  mainAppUrl?: string
  /** 用户未切换过时的初始模式，默认 float */
  defaultMode?: 'float' | 'dock'
  /** 唤起按钮文字，默认「问 AI」 */
  launcherLabel?: string
  /** 欢迎页标题，默认「有什么可以帮你？」 */
  welcomeTitle?: string
  /** 颜色令牌，如 { '--accent-color': '#7c3aed' } */
  theme?: Record<string, string>
  /** 侧边栏层级，默认 3000 */
  zIndex?: number
  /** 挂载容器，默认 document.body */
  container?: string | HTMLElement
  /** 固定模式下需要让出右侧空间的元素，默认 document.body；传 false 不处理 */
  dockTarget?: string | HTMLElement | false
  /** 字体等静态资源所在目录（打包器引入时用于加载 KaTeX 公式字体） */
  assetsBaseURL?: string
  onReady?: (sidebar: EasyAgentSidebarInstance) => void
  onError?: (error: Error) => void
  onOpen?: () => void
  onClose?: () => void
  onAuthExpired?: (error: Error) => void
  onOpenInMain?: (sessionId: string) => void
  /** 自定义「在管理面板中打开」：默认新标签页打开 mainAppUrl?panel=<tab> */
  onOpenPanel?: (tab: 'experts' | 'skills' | 'connectors') => void
  onLayoutChange?: (layout: { dockWidth: number }) => void
}

export interface EasyAgentOpenOptions {
  /** 预填到输入框的内容（不自动发送） */
  prompt?: string
  /** 本次额外提供给 AI 的上下文文本 */
  context?: string
  /** 新建会话 */
  newSession?: boolean
}

export interface EasyAgentSidebarInstance {
  readonly version: string
  /** 初始化完成（登录成功、会话加载完毕） */
  readonly ready: Promise<EasyAgentSidebarInstance>
  open(options?: EasyAgentOpenOptions): Promise<void>
  close(): Promise<void>
  toggle(): Promise<void>
  isOpen(): boolean
  /** 打开侧边栏并发送一条消息 */
  send(text: string): Promise<void>
  setPageContext(context: EasyAgentPageContextInput): void
  setToken(token: string): Promise<void>
  /** 清除 SDK 登录态并销毁 */
  logout(): void
  destroy(): void
}

export declare const version: string
export declare function init(options: EasyAgentSidebarOptions): EasyAgentSidebarInstance

declare const EasyAgentSidebar: {
  version: string
  init: typeof init
}
export default EasyAgentSidebar
