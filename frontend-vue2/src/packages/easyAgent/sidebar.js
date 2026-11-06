/**
 * Easy Agent 业务模块 · AI 侧边栏 SDK 专用入口（只给 src/sdk/index.js 使用）。
 *
 * SDK 是另一种交付形态：独立打包、自带 Vue 运行时、挂在 Shadow DOM 里，可嵌入任意第三方页面。
 * 它复用与路由页面同一份业务实现，但不经过 index.js：宿主路由接入不要引用本文件。
 * SDK 产物自带一份模块副本，其运行时配置与宿主页面里的模块互不影响。
 */
import './styles/base.css'

export { default as AiSidebar } from './features/chat/components/AiSidebar.vue'
export { chatStore } from './features/chat/store/chatStore.js'
export { default as request, clearAuth, configureRequest, getStoredToken, storeAuth } from './utils/request.js'
export { passwordlessLogin } from './api/auth.js'
export { getUserProfile } from './api/files.js'
export { getModels } from './api/settings.js'
export { setExternalPageContext } from './utils/pageContext.js'
export { DEFAULT_COLORS, THEME_ROOT_CLASS, themeStyle } from './runtime/theme.js'
